"""Followers LinkedIn: DM the people who follow a connected LinkedIn account.

A parallel, self-contained lane alongside the prospect-driven LinkedIn module.
It shares the provider, the ``LinkedInMessage`` table, the per-account daily cap
and the reply poller, but nothing in it can touch prospect outreach: every query
here is scoped by ``LinkedInMessage.follower_id IS NOT NULL``, and every query in
the existing module is scoped by ``IS NULL``.

Four differences from the prospect lane are deliberate:

* **Audience.** Only people the provider reports as followers of the selected
  account are ever eligible. Nobody else can enter this lane.
* **No AI copy.** The user's message is sent verbatim, with only
  ``Hi <first name>,`` prepended. Nothing rewrites, personalises or truncates it
  — see ``build_follower_dm``. The prospect lane still generates copy from
  research as it always did; only this lane is literal.
* **No connection invitations.** A follower is reached by direct message only —
  1st-degree, then open profile, then InMail — and skipped when none of those is
  available. Drafting an invitation note would be dead weight here.
* **A durable checkpoint.** ``LinkedInFollowerSend`` holds one row per
  (account, follower, campaign) behind a UNIQUE index, written as a claim
  *before* the send. That is what makes "run the same campaign tomorrow and it
  continues with the next 50" true across restarts, retries and crashes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.enums import AuditAction, LinkedInStatus
from app.models.linkedin_follower import (
    FollowerSendStatus,
    LinkedInFollower,
    LinkedInFollowerSend,
)
from app.models.linkedin_message import LinkedInMessage
from app.models.principal import Principal
from app.models.suppression import OutreachHistory
from app.services.app_settings import get_setting, set_setting
from app.services.audit import log_action
from app.services.linkedin_budget import linkedin_sent_today
from app.services.linkedin_providers import (
    ACTIVE_ACCOUNT_SETTING,
    get_linkedin_provider,
    public_identifier_from_url,
)

logger = logging.getLogger(__name__)

PROGRESS_KEY = "linkedin_followers_progress"

#: Hard ceiling on how many pages one sync will pull, so a huge network can never
#: turn into an unbounded background job. Pages are 50 records each (the
#: provider's real maximum), so this covers 15,000 people; beyond that, run the
#: sync again to pick up the rest. Sized off a real account with 7,533
#: connections (151 pages, roughly 4 minutes) with room to spare.
MAX_SYNC_PAGES = 300

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_STOPPED = "stopped"
STATUS_FAILED = "failed"

#: Identifies THIS process's claims. A CLAIMED row carrying a different token is
#: a claim whose worker died — see ``interrupted_sends``.
_PROCESS_TOKEN = uuid.uuid4().hex[:16]

# One job at a time per kind. Repeated clicks must not stack two workers over the
# same followers — the checkpoint would stop the duplicate DM, but the wasted
# provider calls and the confusing progress record are worth avoiding outright.
_JOB_LOCKS: dict[str, threading.Lock] = {
    "sync": threading.Lock(),
    "draft": threading.Lock(),
    "send": threading.Lock(),
}

_IDLE: dict = {
    "job": None,
    "status": STATUS_IDLE,
    "total": 0,
    "done": 0,
    "drafted": 0,
    "approved": 0,
    "sent": 0,
    "skipped": 0,
    "failed": 0,
    "imported": 0,
    "stop_requested": False,
    "message": None,
    "campaign_key": None,
}


# --------------------------------------------------------------------------
# Campaign identity
# --------------------------------------------------------------------------


def normalize_message(message: Optional[str]) -> str:
    """Collapse whitespace + case so trivial edits stay the SAME campaign.

    Re-pasting the same message with a stray double space or a changed capital
    must not silently unlock a second DM to everyone already contacted. This is
    used ONLY to derive the campaign key — the message that actually goes out is
    never normalised.
    """
    return " ".join((message or "").split()).strip().lower()


def campaign_key_for(message: Optional[str]) -> str:
    """Stable id for one outreach message — the campaign half of the dedup key.

    A hash rather than a row id so the key is reproducible from the message text
    alone: the same message always resolves to the same campaign without the UI
    having to carry state, and a genuinely different message starts a new one.
    """
    normalized = normalize_message(message)
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def active_account_id() -> Optional[str]:
    """The connected account the Followers module reads and sends as.

    Deliberately the same setting the rest of the app uses, so "which account am
    I acting as" has exactly one answer everywhere.
    """
    return get_setting(ACTIVE_ACCOUNT_SETTING) or settings.unipile_account_id or None


# --------------------------------------------------------------------------
# Progress (AppSetting-backed, so a different thread/process can read + stop it)
# --------------------------------------------------------------------------


def read_progress() -> dict:
    raw = get_setting(PROGRESS_KEY)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return {**_IDLE, **data}
        except (ValueError, TypeError):
            pass
    return dict(_IDLE)


def write_progress(**changes) -> dict:
    state = {**read_progress(), **changes}
    set_setting(PROGRESS_KEY, json.dumps(state))
    return state


def start_progress(job: str, *, total: int, campaign_key: Optional[str] = None) -> None:
    """Open a fresh record. Clears any stale stop request so an old Stop click
    can never halt the next job before it starts."""
    set_setting(
        PROGRESS_KEY,
        json.dumps(
            {
                **_IDLE,
                "job": job,
                "status": STATUS_RUNNING,
                "total": total,
                "campaign_key": campaign_key,
            }
        ),
    )


def request_stop() -> bool:
    """Ask the running job to stop. False when there is nothing to stop."""
    if read_progress().get("status") != STATUS_RUNNING:
        return False
    write_progress(stop_requested=True)
    return True


def stop_requested() -> bool:
    return bool(read_progress().get("stop_requested"))


def finish_progress(*, stopped: bool = False, message: Optional[str] = None) -> None:
    write_progress(
        status=STATUS_STOPPED if stopped else STATUS_DONE,
        stop_requested=False,
        message=message,
    )


def job_running() -> bool:
    return read_progress().get("status") == STATUS_RUNNING


# --------------------------------------------------------------------------
# Roster sync
# --------------------------------------------------------------------------


def sync_followers(
    db: Session, *, account_id: str, max_pages: int = MAX_SYNC_PAGES
) -> dict:
    """Refresh the audience roster for ``account_id`` from the provider.

    The source is the account's **1st-degree connections**, not the followers
    list. LinkedIn hard-caps ``/users/followers`` at 1,000 records — measured on
    an account with 7,759 followers, it stopped dead at exactly 1,000 and dropped
    the cursor — while it pages connections all the way through. Since connecting
    on LinkedIn auto-follows, the two sets very nearly coincide, and every
    connection is 1st-degree so it can be DM'd without an InMail credit.

    Upsert by (account_id, provider_id): re-syncing updates the same rows rather
    than duplicating people, and ``last_seen_at`` records who is still in the
    network. Both endpoints return the same ACoAA… member id, so people already
    synced from the followers list dedupe against this cleanly. Rows are never
    deleted — someone who disconnects after being DM'd must stay visible in the
    Sent tab.
    """
    provider = get_linkedin_provider(account_id)
    if not provider.supports_followers():
        return {
            "supported": False,
            "imported": 0,
            "updated": 0,
            "pages": 0,
            "error": "This LinkedIn provider cannot list your network.",
        }

    now = datetime.utcnow()
    imported = 0
    updated = 0
    pages = 0
    error: Optional[str] = None
    page_size = 50
    # 1 restores the original strictly-sequential paging.
    workers = max(1, int(getattr(settings, "linkedin_sync_concurrency", 1)))

    def upsert(record) -> None:
        """Insert or refresh one person. Runs on THIS thread only — the Session
        is not thread-safe, so only the HTTP fetches are parallelised."""
        nonlocal imported, updated
        existing = db.execute(
            select(LinkedInFollower).where(
                LinkedInFollower.account_id == account_id,
                LinkedInFollower.provider_id == record.provider_id,
            )
        ).scalars().first()
        public_id = public_identifier_from_url(record.profile_url or "") or None
        if existing is None:
            db.add(
                LinkedInFollower(
                    account_id=account_id,
                    provider_id=record.provider_id,
                    urn=record.urn,
                    public_identifier=public_id,
                    name=record.name,
                    headline=record.headline,
                    profile_url=record.profile_url,
                    picture_url=record.picture_url,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            imported += 1
        else:
            # Refresh the display fields; never overwrite a good value with a
            # blank one from a sparser page.
            existing.name = record.name or existing.name
            existing.headline = record.headline or existing.headline
            existing.profile_url = record.profile_url or existing.profile_url
            existing.picture_url = record.picture_url or existing.picture_url
            existing.public_identifier = public_id or existing.public_identifier
            existing.urn = record.urn or existing.urn
            existing.last_seen_at = now
            updated += 1

    # Fetch a batch of pages at once. A page is ~2s of pure waiting on the
    # provider and a large network is 150+ pages, so sequential paging spent
    # minutes idle. Offsets are computed rather than followed, which is only
    # possible because the cursor is a plain {"limit","startIndex"} (see
    # cursor_for_offset) — verified to return identical rows to walking there.
    offset = 0
    done = False
    while not done and pages < max_pages:
        if stop_requested():
            break
        batch = [
            offset + i * page_size
            for i in range(min(workers, max_pages - pages))
        ]
        if workers == 1:
            results = [(batch[0], provider.list_connections(offset=batch[0] or None))]
        else:
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                futures = {
                    pool.submit(provider.list_connections, offset=off or None): off
                    for off in batch
                }
                results = sorted(
                    ((futures[f], f.result()) for f in as_completed(futures)),
                    key=lambda pair: pair[0],
                )

        # Apply in offset order so the roster keeps LinkedIn's own ordering —
        # that ordering is what makes "the next 50" predictable between runs.
        for off, page in results:
            pages += 1
            if page.error:
                error = page.error
                done = True
                break
            for record in page.followers:
                upsert(record)
            # A page with no cursor is the last one. Emptiness alone is not a
            # reliable end signal: LinkedIn returns short pages mid-list (37 of
            # 75 pages on a real account), so a short page must NOT stop the sync.
            if not page.cursor or not page.followers:
                done = True
                break
        # Commit per batch so a long sync keeps its work if it is interrupted.
        db.commit()
        write_progress(done=imported + updated, imported=imported)
        offset += len(batch) * page_size

    return {
        "supported": True,
        "imported": imported,
        "updated": updated,
        "pages": pages,
        "error": error,
    }


# --------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------


def _contacted_provider_ids(
    db: Session, *, account_id: str, campaign_key: str
) -> set[str]:
    """Followers this account must NOT DM again for this campaign.

    SENT is permanent. CLAIMED is included because its outcome is unknown: a
    claim whose worker died may well have delivered, and re-sending a possible
    duplicate is worse than leaving one message unsent. FAILED and SKIPPED are
    absent on purpose — those are safe to attempt again.
    """
    rows = db.execute(
        select(LinkedInFollowerSend.follower_provider_id).where(
            LinkedInFollowerSend.account_id == account_id,
            LinkedInFollowerSend.campaign_key == campaign_key,
            LinkedInFollowerSend.status.in_(
                [FollowerSendStatus.SENT, FollowerSendStatus.CLAIMED]
            ),
        )
    ).scalars().all()
    return {r for r in rows if r}


def eligible_followers(
    db: Session, *, account_id: str, campaign_key: str, limit: Optional[int] = None
) -> list[LinkedInFollower]:
    """Followers of ``account_id`` with no message yet for this campaign.

    Ordered by id so repeated runs walk the roster in a stable order — that is
    what makes "tomorrow it picks up the next batch" predictable.
    """
    drafted = select(LinkedInMessage.follower_id).where(
        LinkedInMessage.follower_id.is_not(None),
        LinkedInMessage.follower_campaign_key == campaign_key,
    )
    query = (
        select(LinkedInFollower)
        .where(
            LinkedInFollower.account_id == account_id,
            LinkedInFollower.id.not_in(drafted),
        )
        .order_by(LinkedInFollower.id)
    )
    if limit is not None:
        query = query.limit(limit)
    followers = list(db.execute(query).scalars().all())
    contacted = _contacted_provider_ids(
        db, account_id=account_id, campaign_key=campaign_key
    )
    if not contacted:
        return followers
    return [f for f in followers if f.provider_id not in contacted]


def count_eligible_followers(db: Session, *, account_id: str, campaign_key: str) -> int:
    """How many followers still need a draft — counted in SQL.

    ``eligible_followers`` materialises every row, which the stats endpoint used
    to do purely to call ``len()`` on it. That endpoint is polled every few
    seconds while a job runs, so with a large roster it was loading the whole
    follower list repeatedly to produce one number.

    Neither subquery can yield NULL (``follower_id`` is filtered to NOT NULL and
    ``follower_provider_id`` is non-nullable), which matters because SQL ``NOT
    IN`` against a NULL matches nothing at all.
    """
    drafted = select(LinkedInMessage.follower_id).where(
        LinkedInMessage.follower_id.is_not(None),
        LinkedInMessage.follower_campaign_key == campaign_key,
    )
    contacted = select(LinkedInFollowerSend.follower_provider_id).where(
        LinkedInFollowerSend.account_id == account_id,
        LinkedInFollowerSend.campaign_key == campaign_key,
        LinkedInFollowerSend.status.in_(
            [FollowerSendStatus.SENT, FollowerSendStatus.CLAIMED]
        ),
    )
    return int(
        db.execute(
            select(func.count())
            .select_from(LinkedInFollower)
            .where(
                LinkedInFollower.account_id == account_id,
                LinkedInFollower.id.not_in(drafted),
                LinkedInFollower.provider_id.not_in(contacted),
            )
        ).scalar_one()
    )


def interrupted_sends(db: Session, *, account_id: str, campaign_key: str) -> int:
    """Claims left behind by a worker that died mid-send (needs human review)."""
    return int(
        db.execute(
            select(func.count())
            .select_from(LinkedInFollowerSend)
            .where(
                LinkedInFollowerSend.account_id == account_id,
                LinkedInFollowerSend.campaign_key == campaign_key,
                LinkedInFollowerSend.status == FollowerSendStatus.CLAIMED,
                or_(
                    LinkedInFollowerSend.claimed_by.is_(None),
                    LinkedInFollowerSend.claimed_by != _PROCESS_TOKEN,
                ),
            )
        ).scalar_one()
    )


# --------------------------------------------------------------------------
# Drafting
# --------------------------------------------------------------------------


def first_name_of(follower: LinkedInFollower) -> str:
    """The greeting name for one follower.

    First name only. LinkedIn names routinely carry credential suffixes
    ("Jennie Reis, CPCC, ACC") and the full string would read badly in a
    greeting. Trailing punctuation is stripped so "Reis," never becomes part of
    the name, and a nameless follower falls back to "there" rather than
    producing "Hi ,".
    """
    raw = (follower.name or "").strip()
    if not raw:
        return "there"
    first = raw.split()[0].strip().strip(",.;:").strip()
    return first or "there"


def build_follower_dm(message: str, follower: LinkedInFollower) -> str:
    """The DM for one follower: a greeting, then the message VERBATIM.

    No model is involved. The text the user typed is used exactly as written —
    not rewritten, personalised, summarised, reflowed or truncated — because the
    whole point of this path is that what they see in the box is what gets sent.
    The only addition is the ``Hi <first name>,`` line and one blank line.

    Note ``message`` is deliberately not stripped of internal formatting; only
    surrounding blank space is trimmed so the greeting sits flush against it.
    """
    return f"Hi {first_name_of(follower)},\n\n{(message or '').strip()}"


def draft_followers(
    db: Session,
    *,
    account_id: str,
    campaign_key: str,
    message: str,
    principal_id: int,
    limit: Optional[int] = None,
) -> dict:
    """Draft a DM for every eligible follower. Commits per message.

    Each draft is the user's message with a greeting prepended — no model call,
    so this is fast and free. Commits stay per-message anyway: a killed worker
    must keep the drafts it already wrote, and the progress record is what the UI
    watches.

    ``principal_id`` no longer shapes the text (there is nothing to write in
    anyone's voice); it is still stamped on the row for attribution, exactly as
    the prospect-driven messages are.
    """
    principal = db.get(Principal, principal_id)
    if principal is None:
        return {"drafted": 0, "failed": 0, "errors": ["Principal not found."]}

    followers = eligible_followers(
        db, account_id=account_id, campaign_key=campaign_key, limit=limit
    )
    start_progress("draft", total=len(followers), campaign_key=campaign_key)
    drafted = 0
    failed = 0
    errors: list[str] = []
    stopped = False

    for index, follower in enumerate(followers, start=1):
        if stop_requested():
            stopped = True
            break
        try:
            body = build_follower_dm(message, follower)
            db.add(
                LinkedInMessage(
                    principal_id=principal.id,
                    body=body,
                    # No invitation note: this lane never sends invitations.
                    status=LinkedInStatus.DRAFT,
                    follower_id=follower.id,
                    follower_campaign_key=campaign_key,
                    linkedin_provider_id=follower.provider_id,
                    public_identifier=follower.public_identifier,
                )
            )
            db.commit()
            drafted += 1
        except Exception as exc:  # noqa: BLE001 - one bad draft must not stop the run
            db.rollback()
            failed += 1
            if len(errors) < 10:
                errors.append(f"{follower.name or follower.provider_id}: {exc}")
            logger.warning("Follower draft failed for %s: %s", follower.provider_id, exc)
        write_progress(done=index, drafted=drafted, failed=failed)

    if drafted:
        log_action(
            db,
            AuditAction.LINKEDIN_DRAFT,
            entity_type="linkedin_followers",
            actor="user",
            summary=f"Drafted {drafted} follower DM(s) for campaign {campaign_key}",
            commit=True,
        )
    finish_progress(
        stopped=stopped,
        message=(
            f"Drafted {drafted} DM(s)."
            + (f" {failed} failed." if failed else "")
            + (" Stopped early." if stopped else "")
        ),
    )
    return {"drafted": drafted, "failed": failed, "errors": errors, "stopped": stopped}


def approve_all(db: Session, *, campaign_key: str, approved_by: str = "user") -> int:
    """Approve every drafted follower DM in this campaign.

    Plain status updates, no provider calls, so this stays a fast inline request
    exactly like the existing per-message approve.
    """
    drafts = list(
        db.execute(
            select(LinkedInMessage).where(
                LinkedInMessage.follower_id.is_not(None),
                LinkedInMessage.follower_campaign_key == campaign_key,
                LinkedInMessage.status == LinkedInStatus.DRAFT,
            )
        ).scalars().all()
    )
    now = datetime.utcnow()
    for msg in drafts:
        msg.status = LinkedInStatus.APPROVED
        msg.approved_by = approved_by
        msg.approved_at = now
    if drafts:
        log_action(
            db,
            AuditAction.LINKEDIN_APPROVAL,
            entity_type="linkedin_followers",
            actor=approved_by,
            summary=f"Approved {len(drafts)} follower DM(s) for campaign {campaign_key}",
        )
    db.commit()
    return len(drafts)


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------


def _deliver(provider, *, provider_id: str, text: str, profile) -> tuple:
    """Send by the cheapest path this person allows. Returns (result, reach).

    Escalation, never a connection invitation:
      1st-degree  -> ordinary DM
      open profile -> ordinary DM (retried as InMail if LinkedIn refuses it)
      otherwise    -> InMail, which burns a credit and is the last resort
    """
    if profile.is_connected:
        return provider.send_message(provider_id=provider_id, text=text), "connected"
    if profile.is_open_profile:
        result = provider.send_message(provider_id=provider_id, text=text)
        # Not every open profile accepts a plain message; only an explicit
        # "unreachable" justifies spending an InMail credit instead.
        if result.sent or not result.unreachable:
            return result, "open_profile"
    result = provider.send_message(provider_id=provider_id, text=text, inmail=True)
    return result, "inmail"


def _claim(
    db: Session,
    *,
    account_id: str,
    follower: LinkedInFollower,
    campaign_key: str,
    message: str,
    message_id: int,
) -> Optional[LinkedInFollowerSend]:
    """Reserve this (account, follower, campaign) before sending.

    Returns None when the reservation is refused, which is the whole point: a
    duplicate click, a retried request, or a second worker hits the UNIQUE index
    and gets None instead of sending a second DM. An existing retryable row is
    re-used in place so retries never grow the table.
    """
    now = datetime.utcnow()
    existing = db.execute(
        select(LinkedInFollowerSend).where(
            LinkedInFollowerSend.account_id == account_id,
            LinkedInFollowerSend.follower_provider_id == follower.provider_id,
            LinkedInFollowerSend.campaign_key == campaign_key,
        )
    ).scalars().first()
    if existing is not None:
        if existing.status not in FollowerSendStatus.RETRYABLE:
            return None
        existing.status = FollowerSendStatus.CLAIMED
        existing.claimed_by = _PROCESS_TOKEN
        existing.claimed_at = now
        existing.message_id = message_id
        existing.attempts = (existing.attempts or 0) + 1
        existing.error = None
        db.commit()
        return existing

    row = LinkedInFollowerSend(
        account_id=account_id,
        follower_provider_id=follower.provider_id,
        campaign_key=campaign_key,
        campaign_goal=message,
        follower_id=follower.id,
        message_id=message_id,
        status=FollowerSendStatus.CLAIMED,
        claimed_by=_PROCESS_TOKEN,
        claimed_at=now,
        attempts=1,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Another worker/request claimed this follower first — that is a
        # successful defence, not an error.
        db.rollback()
        return None
    return row


def send_one(
    db: Session,
    *,
    msg: LinkedInMessage,
    follower: LinkedInFollower,
    account_id: str,
    campaign_key: str,
    message: str,
) -> str:
    """Send one follower DM under the checkpoint. Returns the outcome.

    Outcomes: ``sent`` | ``skipped`` (unreachable) | ``failed`` | ``duplicate``.
    The message is marked SENT only after the provider confirms delivery.
    """
    claim = _claim(
        db,
        account_id=account_id,
        follower=follower,
        campaign_key=campaign_key,
        message=message,
        message_id=msg.id,
    )
    if claim is None:
        return "duplicate"

    provider = get_linkedin_provider(account_id)
    identifier = (
        follower.public_identifier
        or public_identifier_from_url(follower.profile_url or "")
        or follower.provider_id
    )
    profile = provider.resolve_profile(identifier)

    # An unresolvable profile is a failure, not a skip: it is usually transport
    # or rate limiting, and must stay retryable.
    if not profile.found or not profile.provider_id:
        claim.status = FollowerSendStatus.FAILED
        claim.error = profile.error or "Could not resolve LinkedIn profile"
        msg.error = claim.error
        db.commit()
        return "failed"

    result, reach = _deliver(
        provider,
        provider_id=profile.provider_id or follower.provider_id,
        text=msg.body,
        profile=profile,
    )

    msg.provider = provider.name
    msg.from_account = getattr(provider, "account_id", None) or account_id
    msg.linkedin_provider_id = profile.provider_id or follower.provider_id
    msg.public_identifier = profile.public_identifier or follower.public_identifier
    msg.network_distance = profile.network_distance
    msg.connected = profile.is_connected

    if result.sent:
        now = datetime.utcnow()
        msg.provider_chat_id = result.chat_id
        msg.provider_message_id = result.message_id
        msg.status = LinkedInStatus.SENT
        msg.sent_at = now
        msg.error = None
        claim.status = FollowerSendStatus.SENT
        claim.reach = reach
        claim.sent_at = now
        claim.error = None
        db.add(
            OutreachHistory(
                contact_id=None,
                channel="linkedin",
                detail=f"Sent LinkedIn DM to follower via {provider.name} ({reach})",
            )
        )
        log_action(
            db,
            AuditAction.LINKEDIN_SEND,
            entity_type="linkedin_message",
            entity_id=msg.id,
            summary=f"Sent follower DM to {follower.name or follower.provider_id} ({reach})",
        )
        db.commit()
        return "sent"

    if result.unreachable:
        # No path exists today: not connected, not an open profile, no InMail.
        # Left retryable — connection state changes over time.
        claim.status = FollowerSendStatus.SKIPPED
        claim.reach = reach
        claim.error = result.error or "Not reachable by DM, open profile, or InMail"
        msg.error = claim.error
        db.commit()
        return "skipped"

    claim.status = FollowerSendStatus.FAILED
    # Record which path was attempted even on failure. Without this a failed send
    # was indistinguishable from one that never got past the profile lookup, which
    # made a broken InMail request look like a resolve problem.
    claim.reach = reach
    claim.error = result.error or "LinkedIn send failed"
    msg.error = claim.error
    db.commit()
    return "failed"


def send_all(
    db: Session,
    *,
    account_id: str,
    campaign_key: str,
    message: str,
    approve_first: bool = True,
) -> dict:
    """Approve (optionally) then send every open follower DM, paced and capped.

    Shares the account's ``linkedin_daily_send_cap`` with every other LinkedIn
    send path, because LinkedIn's limit is per account and does not care which
    module spent it. The overflow is reported as ``held`` and goes out next run.
    """
    if approve_first:
        approve_all(db, campaign_key=campaign_key)

    messages = list(
        db.execute(
            select(LinkedInMessage)
            .where(
                LinkedInMessage.follower_id.is_not(None),
                LinkedInMessage.follower_campaign_key == campaign_key,
                LinkedInMessage.status.in_(
                    [LinkedInStatus.DRAFT, LinkedInStatus.APPROVED]
                ),
            )
            .order_by(LinkedInMessage.id)
        ).scalars().all()
    )

    cap = max(0, int(settings.linkedin_daily_send_cap))
    sent_today = linkedin_sent_today(db, account_id)
    remaining = max(0, cap - sent_today)
    queue = messages[:remaining]
    held = len(messages) - len(queue)

    start_progress("send", total=len(queue), campaign_key=campaign_key)
    write_progress(message=f"{held} held for the next run." if held else None)

    delay = max(0.0, float(settings.bulk_linkedin_send_delay_seconds))
    sent = skipped = failed = duplicates = 0
    stopped = False

    for index, msg in enumerate(queue, start=1):
        if stop_requested():
            stopped = True
            break
        follower = db.get(LinkedInFollower, msg.follower_id)
        if follower is None:
            failed += 1
            write_progress(done=index, failed=failed)
            continue
        try:
            outcome = send_one(
                db,
                msg=msg,
                follower=follower,
                account_id=account_id,
                campaign_key=campaign_key,
                message=message,
            )
        except Exception as exc:  # noqa: BLE001 - one send must not kill the run
            db.rollback()
            outcome = "failed"
            logger.exception("Follower send crashed for %s: %s", follower.provider_id, exc)
        if outcome == "sent":
            sent += 1
        elif outcome == "skipped":
            skipped += 1
        elif outcome == "duplicate":
            duplicates += 1
        else:
            failed += 1
        write_progress(
            done=index, sent=sent, skipped=skipped, failed=failed
        )
        # Pace only between real sends; a skip cost the account nothing.
        if outcome == "sent" and index < len(queue) and delay:
            if sleep_unless_stopped(delay):
                stopped = True
                break

    finish_progress(
        stopped=stopped,
        message=(
            f"Sent {sent} DM(s)."
            + (f" {skipped} not reachable." if skipped else "")
            + (f" {failed} failed." if failed else "")
            + (f" {duplicates} already contacted." if duplicates else "")
            + (f" {held} held for the next run (daily cap {cap})." if held else "")
            + (" Stopped early." if stopped else "")
        ),
    )
    return {
        "queued": len(queue),
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "duplicates": duplicates,
        "held": held,
        "cap": cap,
        "sent_today": sent_today,
        "stopped": stopped,
    }


def sleep_unless_stopped(seconds: float, poll: float = 2.0) -> bool:
    """Pace the next send while staying responsive to Stop.

    Sleeping the whole gap in one call would leave Stop with no effect until it
    elapsed. True if a stop was requested during the wait.
    """
    import time

    waited = 0.0
    while waited < seconds:
        if stop_requested():
            return True
        chunk = min(poll, seconds - waited)
        time.sleep(chunk)
        waited += chunk
    return stop_requested()


# --------------------------------------------------------------------------
# Background job launchers
# --------------------------------------------------------------------------


def _run_job(kind: str, work) -> bool:
    """Run ``work(db)`` on a daemon thread under the per-kind lock.

    Each worker owns its own session: the request that started it has long since
    returned its own.
    """
    lock = _JOB_LOCKS[kind]
    if not lock.acquire(blocking=False):
        return False

    # Open the record HERE, in the request, not in the worker. The thread takes a
    # moment to start, and until it does a poll of /progress would still return
    # the PREVIOUS job's "done" — so the UI would show a finished bar (or none)
    # for the job it just started, then jump. The worker re-opens it with the real
    # total once it knows it.
    start_progress(kind, total=0)

    def _worker() -> None:
        try:
            db = SessionLocal()
            try:
                work(db)
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - never let a background job die silently
            logger.exception("Followers %s job failed", kind)
            try:
                write_progress(
                    status=STATUS_FAILED,
                    stop_requested=False,
                    message=f"The {kind} job failed — see backend logs.",
                )
            except Exception:  # noqa: BLE001
                pass
        finally:
            lock.release()

    threading.Thread(target=_worker, name=f"linkedin-followers-{kind}", daemon=True).start()
    return True


def launch_sync(*, account_id: str) -> bool:
    def work(db: Session) -> None:
        result = sync_followers(db, account_id=account_id)
        finish_progress(
            message=(
                result.get("error")
                or f"Found {result['imported']} new follower(s); "
                f"{result['updated']} already known."
            )
        )

    return _run_job("sync", work)


def launch_draft(
    *,
    account_id: str,
    campaign_key: str,
    message: str,
    principal_id: int,
    limit: Optional[int] = None,
) -> bool:
    def work(db: Session) -> None:
        draft_followers(
            db,
            account_id=account_id,
            campaign_key=campaign_key,
            message=message,
            principal_id=principal_id,
            limit=limit,
        )

    return _run_job("draft", work)


def launch_send(*, account_id: str, campaign_key: str, message: str) -> bool:
    def work(db: Session) -> None:
        send_all(db, account_id=account_id, campaign_key=campaign_key, message=message)

    return _run_job("send", work)


# --------------------------------------------------------------------------
# Stats (authoritative, DB-derived — never resets on refresh or restart)
# --------------------------------------------------------------------------


def campaign_stats(db: Session, *, account_id: str, campaign_key: str) -> dict:
    """Tab counts for one follower campaign, straight from the database."""

    def _count(*conditions) -> int:
        return int(
            db.execute(
                select(func.count())
                .select_from(LinkedInMessage)
                .where(
                    LinkedInMessage.follower_id.is_not(None),
                    LinkedInMessage.follower_campaign_key == campaign_key,
                    *conditions,
                )
            ).scalar_one()
        )

    followers_total = int(
        db.execute(
            select(func.count())
            .select_from(LinkedInFollower)
            .where(LinkedInFollower.account_id == account_id)
        ).scalar_one()
    )
    sent_rows = int(
        db.execute(
            select(func.count())
            .select_from(LinkedInFollowerSend)
            .where(
                LinkedInFollowerSend.account_id == account_id,
                LinkedInFollowerSend.campaign_key == campaign_key,
                LinkedInFollowerSend.status == FollowerSendStatus.SENT,
            )
        ).scalar_one()
    )
    skipped_rows = int(
        db.execute(
            select(func.count())
            .select_from(LinkedInFollowerSend)
            .where(
                LinkedInFollowerSend.account_id == account_id,
                LinkedInFollowerSend.campaign_key == campaign_key,
                LinkedInFollowerSend.status == FollowerSendStatus.SKIPPED,
            )
        ).scalar_one()
    )
    # Roster-wide progress, across EVERY campaign this account has run — the
    # "how far through my 999 followers am I" number. Distinct followers, so a
    # follower reached under two different messages still counts once.
    contacted_all_time = int(
        db.execute(
            select(func.count(func.distinct(LinkedInFollowerSend.follower_provider_id)))
            .where(
                LinkedInFollowerSend.account_id == account_id,
                LinkedInFollowerSend.status == FollowerSendStatus.SENT,
            )
        ).scalar_one()
    )
    cap = max(0, int(settings.linkedin_daily_send_cap))
    sent_today = linkedin_sent_today(db, account_id)
    return {
        "followers_total": followers_total,
        "contacted_all_time": contacted_all_time,
        "never_contacted": max(0, followers_total - contacted_all_time),
        "eligible": count_eligible_followers(
            db, account_id=account_id, campaign_key=campaign_key
        ),
        "all": _count(),
        "draft": _count(LinkedInMessage.status == LinkedInStatus.DRAFT),
        "approved": _count(LinkedInMessage.status == LinkedInStatus.APPROVED),
        "sent": _count(LinkedInMessage.status == LinkedInStatus.SENT),
        "replied": _count(LinkedInMessage.status == LinkedInStatus.REPLIED),
        # Checkpoint truth, which outlives any message edit.
        "contacted_ever": sent_rows,
        "not_reachable": skipped_rows,
        "needs_review": interrupted_sends(
            db, account_id=account_id, campaign_key=campaign_key
        ),
        "cap": cap,
        "sent_today": sent_today,
        "remaining_today": max(0, cap - sent_today),
    }
