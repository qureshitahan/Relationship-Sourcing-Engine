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
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, func, or_, select
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

#: A job whose thread died leaves ``running`` in the progress row forever: the
#: row lives in the database, the worker lives in the process, and a restart ends
#: one without touching the other. Every progress write stamps a heartbeat, so a
#: ``running`` record older than this is reported as failed rather than believed.
#: Generous on purpose — the send job paces ~20s per DM, and a sync batch can sit
#: on the provider for the full 30s request timeout.
STALE_JOB_AFTER = timedelta(minutes=10)

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
    # Already contacted under this message, and whatever the daily cap left for
    # the next run. Both were reported only inside the finish sentence, which the
    # UI cannot break apart to show a tidy summary.
    "duplicates": 0,
    "held": 0,
    "imported": 0,
    # When the worker last said anything. A record still claiming to be running
    # long after its last beat is a job whose process is gone. See STALE_JOB_AFTER.
    "heartbeat": None,
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
    # Stamped on every write, so "when did the worker last speak" needs no extra
    # call site. Deliberately NOT applied when reading: a live worker that went
    # quiet for a while must be able to write again and still count as running.
    state = {**read_progress(), "heartbeat": datetime.utcnow().isoformat(), **changes}
    set_setting(PROGRESS_KEY, json.dumps(state))
    return state


def stale_progress(state: Optional[dict] = None) -> Optional[dict]:
    """The record with a dead ``running`` corrected to failed, or None if live.

    A restart, a deploy or a recycled worker ends the thread without touching the
    row it was writing, which then claims to be running forever: the page shows a
    frozen bar and Stop cannot help, because the flag it sets has no reader left.
    A record with no heartbeat at all predates this stamping, so it belongs to a
    process that is certainly gone.
    """
    state = read_progress() if state is None else state
    if state.get("status") != STATUS_RUNNING:
        return None
    beat = state.get("heartbeat")
    if beat:
        try:
            if datetime.utcnow() - datetime.fromisoformat(beat) < STALE_JOB_AFTER:
                return None
        except (TypeError, ValueError):
            return None  # unreadable stamp: leave the record alone
    return {
        **state,
        "status": STATUS_FAILED,
        "stop_requested": False,
        "message": state.get("message")
        or f"The {state.get('job') or 'followers'} job stopped without finishing "
        "(the server restarted). Whatever it had already saved is kept — "
        "start it again to carry on.",
    }


def clear_stale_progress() -> bool:
    """Persist that correction. True when a dead record was actually cleared."""
    corrected = stale_progress()
    if corrected is None:
        return False
    set_setting(PROGRESS_KEY, json.dumps(corrected))
    return True


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
                "heartbeat": datetime.utcnow().isoformat(),
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

    # The roster this account already holds, keyed by provider id, fetched ONCE.
    # The per-record lookup below used to be a SELECT of its own, so a 7,400-person
    # network spent 7,400 round trips on a database that answers in milliseconds
    # but is not on this machine — minutes of pure waiting before a single page of
    # LinkedIn data was even asked for. One query returns the same rows, and every
    # insert is added to the map so a provider id repeated within one sync still
    # resolves to the row just created rather than inserting it twice.
    known: dict[str, LinkedInFollower] = {
        row.provider_id: row
        for row in db.execute(
            select(LinkedInFollower).where(LinkedInFollower.account_id == account_id)
        ).scalars().all()
    }

    def upsert(record) -> None:
        """Insert or refresh one person. Runs on THIS thread only — the Session
        is not thread-safe, so only the HTTP fetches are parallelised."""
        nonlocal imported, updated
        existing = known.get(record.provider_id)
        public_id = public_identifier_from_url(record.profile_url or "") or None
        if existing is None:
            fresh = LinkedInFollower(
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
            db.add(fresh)
            known[record.provider_id] = fresh
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


def unsettled_follower_filter(account_id: str, campaign_key: str):
    """Excludes drafts whose follower is already settled for this message.

    A follower with a SENT checkpoint has had this message; one CLAIMED is
    awaiting review and is deliberately never auto-retried. Either way ``_claim``
    will refuse the send, so a draft pointing at them can NEVER go out.

    Such a row is left behind whenever a follower ended up with more than one
    draft for the same message: one row sent and moved on, the duplicate stayed
    APPROVED forever. Counting those as work still to do is what made the page
    offer "Approve & send all (97)" while the queue could only ever attempt 3.
    """
    settled = select(LinkedInFollowerSend.follower_provider_id).where(
        LinkedInFollowerSend.account_id == account_id,
        LinkedInFollowerSend.campaign_key == campaign_key,
        LinkedInFollowerSend.status.not_in(FollowerSendStatus.RETRYABLE),
    )
    settled_followers = select(LinkedInFollower.id).where(
        LinkedInFollower.account_id == account_id,
        LinkedInFollower.provider_id.in_(settled),
    )
    return LinkedInMessage.follower_id.not_in(settled_followers)


def open_message_conditions(account_id: str, campaign_key: str) -> list:
    """Every condition for "an open follower DM that can still be sent".

    One definition shared by the send queue, the tab counts and the list, so the
    three can no longer disagree about how much work is left.
    """
    return [
        LinkedInMessage.follower_id.is_not(None),
        LinkedInMessage.follower_campaign_key == campaign_key,
        LinkedInMessage.status.in_([LinkedInStatus.DRAFT, LinkedInStatus.APPROVED]),
        unsettled_follower_filter(account_id, campaign_key),
    ]


def _retry_attempts(
    db: Session, *, account_id: str, campaign_key: str
) -> dict[int, int]:
    """How many times each follower has already been tried for this message.

    Only retryable checkpoints count (FAILED / SKIPPED). A settled one is filtered
    out of the queue entirely, so it never needs an attempt number.

    Keyed by ``LinkedInFollower.id`` so a queued message's ``follower_id`` reads
    straight off it, and absent means never tried.
    """
    rows = db.execute(
        select(LinkedInFollower.id, LinkedInFollowerSend.attempts).join(
            LinkedInFollowerSend,
            and_(
                LinkedInFollowerSend.account_id == LinkedInFollower.account_id,
                LinkedInFollowerSend.follower_provider_id
                == LinkedInFollower.provider_id,
            ),
        ).where(
            LinkedInFollower.account_id == account_id,
            LinkedInFollowerSend.campaign_key == campaign_key,
            LinkedInFollowerSend.status.in_(FollowerSendStatus.RETRYABLE),
        )
    ).all()
    return {fid: int(n or 0) for fid, n in rows if fid is not None}


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

    # Settled followers are kept out of the queue rather than discovered one by
    # one inside it. This is a pre-filter only — every send still passes through
    # ``_claim``, which remains the actual duplicate guarantee.
    messages = list(
        db.execute(
            select(LinkedInMessage)
            .where(*open_message_conditions(account_id, campaign_key))
            .order_by(LinkedInMessage.id)
        ).scalars().all()
    )

    cap = max(0, int(settings.linkedin_daily_send_cap))
    sent_today = linkedin_sent_today(db, account_id)
    remaining = max(0, cap - sent_today)

    # Least-tried followers first. A follower LinkedIn refuses to resolve (a
    # locked or restricted profile) is left RETRYABLE on purpose, so it comes
    # back in every run — and with the queue ordered by message id, that block of
    # old failures sat at the FRONT and was retried ahead of everyone else, day
    # after day. Ordering by attempt count puts anyone never tried first, so a
    # run reaches new people before re-trying known problems. Nothing is dropped:
    # the previously-tried ones still follow, just behind.
    attempts_by_follower = _retry_attempts(
        db, account_id=account_id, campaign_key=campaign_key
    )
    messages.sort(key=lambda m: (attempts_by_follower.get(m.follower_id, 0), m.id))

    # The cap counts DELIVERIES, not attempts. This used to slice the queue to
    # ``remaining`` and attempt exactly that many, so every failure, skip and
    # duplicate spent one of the day's places and delivered nothing — a run
    # allowed 50 sends could finish having sent 15, with the rest of the
    # allowance unusable until tomorrow. The loop below instead runs until
    # ``remaining`` messages have actually left (or the list is exhausted), so a
    # non-delivery costs an attempt rather than a send.
    target = min(remaining, len(messages))

    start_progress("send", total=target, campaign_key=campaign_key)
    # How many will not be reached today is no longer knowable up front: it
    # depends on how many attempts deliver. The exact figure is reported in the
    # finish message once the run is over.
    write_progress(
        message=(
            f"{len(messages) - target} or more held for the next run."
            if len(messages) > target
            else None
        )
    )

    delay = max(0.0, float(settings.bulk_linkedin_send_delay_seconds))
    sent = skipped = failed = duplicates = 0
    attempted = 0
    stopped = False

    for msg in messages:
        # Deliveries, not attempts — the whole point of the change above.
        if sent >= remaining:
            break
        if stop_requested():
            stopped = True
            break
        attempted += 1
        follower = db.get(LinkedInFollower, msg.follower_id)
        if follower is None:
            failed += 1
            write_progress(done=sent, failed=failed)
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
        # ``done`` follows deliveries so the bar agrees with its own "N of M"
        # label, which already reads from ``sent``. Counting attempts here would
        # fill the bar while nothing was actually being delivered.
        write_progress(
            done=sent, sent=sent, skipped=skipped, failed=failed
        )
        # Pace only between real sends; a skip cost the account nothing.
        if outcome == "sent" and sent < remaining and delay:
            if sleep_unless_stopped(delay):
                stopped = True
                break

    # Now exact rather than an estimate: whatever was never reached today.
    held = max(0, len(messages) - attempted)
    # Written before finishing so the completed record carries the full picture:
    # the page shows this summary after the run, and it must not have to re-read
    # the sentence below to find the numbers.
    write_progress(duplicates=duplicates, held=held)

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
        "queued": target,
        # Attempts made to reach ``sent`` deliveries. Larger than ``queued``
        # whenever profiles could not be reached, which is exactly what the old
        # queue slice hid.
        "attempted": attempted,
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


#: How far along a follower is under one message, used to pick ONE row when a
#: follower ended up with several. Anything unlisted (failed, not interested)
#: ranks lowest: it says least about what the follower actually received. The
#: list applies the same order, so the tab counts and the rows underneath them
#: can no longer disagree.
PROGRESS_RANK = {
    LinkedInStatus.REPLIED: 4,
    LinkedInStatus.SENT: 3,
    LinkedInStatus.APPROVED: 2,
    LinkedInStatus.DRAFT: 1,
}


def campaign_status_counts(db: Session, *, account_id: str, campaign_key: str) -> dict:
    """Per-status counts for one campaign, counting PEOPLE and not message rows.

    These used to be row counts, and a follower can hold several message rows for
    the same message (see ``unsettled_follower_filter``). Live data on 2026-09-08:
    654 rows over 507 followers, so the Sent tab read 597 while the checkpoint --
    and the rows the list actually rendered, deduped by the same rank -- said 503.
    Nothing was wrong with the sending; the page simply counted in two different
    units. Counting followers makes every number here mean the same thing.

    A follower's status is the furthest-along of their rows, so a leftover
    APPROVED duplicate never outranks the row that really sent.
    """
    rows = db.execute(
        select(
            LinkedInMessage.follower_id,
            LinkedInMessage.status,
            LinkedInMessage.id,
        ).where(
            LinkedInMessage.follower_id.is_not(None),
            LinkedInMessage.follower_campaign_key == campaign_key,
        )
    ).all()

    best: dict[int, tuple[tuple[int, int], str]] = {}
    for follower_id, status, message_id in rows:
        rank = (PROGRESS_RANK.get(status, 0), message_id or 0)
        current = best.get(follower_id)
        if current is None or rank > current[0]:
            best[follower_id] = (rank, status)

    # A draft for a follower this message has already settled with can never be
    # sent, so it is not work waiting. Same exclusion the send queue and the list
    # already apply -- expressed here over ids we have already loaded.
    settled = set(
        db.execute(
            select(LinkedInFollower.id).where(
                LinkedInFollower.account_id == account_id,
                LinkedInFollower.provider_id.in_(
                    select(LinkedInFollowerSend.follower_provider_id).where(
                        LinkedInFollowerSend.account_id == account_id,
                        LinkedInFollowerSend.campaign_key == campaign_key,
                        LinkedInFollowerSend.status.not_in(
                            FollowerSendStatus.RETRYABLE
                        ),
                    )
                ),
            )
        ).scalars().all()
    )

    counts = {"all": 0, "draft": 0, "approved": 0, "sent": 0, "replied": 0}
    for follower_id, (_rank, status) in best.items():
        counts["all"] += 1
        if status in (LinkedInStatus.DRAFT, LinkedInStatus.APPROVED) and (
            follower_id in settled
        ):
            continue
        if status in counts:
            counts[status] += 1
    return counts


def campaign_people_drafted(db: Session, *, campaign_key: str) -> int:
    """How many FOLLOWERS have this message drafted -- not how many rows exist.

    What the "Draft how many (total)" target is measured against, so the target
    counts the same unit the page reports back ("Already N drafted").
    """
    return int(
        db.execute(
            select(func.count(func.distinct(LinkedInMessage.follower_id))).where(
                LinkedInMessage.follower_id.is_not(None),
                LinkedInMessage.follower_campaign_key == campaign_key,
            )
        ).scalar_one()
    )


def campaign_stats(db: Session, *, account_id: str, campaign_key: str) -> dict:
    """Tab counts for one follower campaign, straight from the database.

    The per-status counts come from ``campaign_status_counts`` -- the local
    ``_count`` helper that used to build them here counted message ROWS, which is
    the miscount this function no longer makes.
    """
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
    # People, not message rows -- and the settled exclusion that used to be a
    # separate SQL filter here now lives inside these counts, applied to the one
    # row that represents each follower.
    people = campaign_status_counts(
        db, account_id=account_id, campaign_key=campaign_key
    )
    return {
        "followers_total": followers_total,
        "contacted_all_time": contacted_all_time,
        "never_contacted": max(0, followers_total - contacted_all_time),
        "eligible": count_eligible_followers(
            db, account_id=account_id, campaign_key=campaign_key
        ),
        "all": people["all"],
        # Sendable only. A draft for a follower already settled under this
        # message can never leave, so counting it here promised work the send
        # queue would then refuse to do.
        "draft": people["draft"],
        "approved": people["approved"],
        "sent": people["sent"],
        "replied": people["replied"],
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
