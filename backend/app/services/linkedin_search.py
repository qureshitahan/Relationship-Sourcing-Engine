"""Classic Search LinkedIn: source prospects from LinkedIn's own search.

The tab this serves replaces Apollo as the SOURCE of people — the filters are
LinkedIn's own (keywords, job title, seniority, company headcount, industry,
location, connection degree) — and then reaches them exactly the way the
existing LinkedIn module does: a direct message when they are already a
1st-degree connection, otherwise a connection invitation carrying a note, with
the message delivered once that invitation is accepted.

Nothing here is automatic. Search, draft, approve and send are four separate
button presses; there is no scheduler and no background trigger.

Deliberately self-contained, for the same reason the followers module is:

* Its own audience tables (``linkedin_search_leads``, ``linkedin_search_sends``)
  rather than ``Contact`` rows, so Discover / Prospects / Emails / Campaigns list
  and count exactly what they listed and counted before.
* Its own progress record and job lock (``linkedin_search_progress``), so a
  search job and a followers job can never fight over one another's state.
* Its own copy of the small amount of progress plumbing the followers module
  has. Sharing it would have meant editing that module, and the brief was to add
  without touching what works.

Two things it does NOT own, on purpose:

* The daily cap. ``linkedin_daily_send_cap`` is per LinkedIn ACCOUNT and does not
  care which module spends it, so this shares the same allowance as the LinkedIn
  and Followers tabs — counted through ``linkedin_sent_today``.
* The duplicate guarantee's shape. It is a UNIQUE index on
  ``(account_id, lead_provider_id, campaign_key)`` plus a claim row written
  BEFORE the send, so a retry, a double click or a second worker collides on the
  index instead of sending twice. A claim left behind by a dead worker is never
  retried automatically — a possible duplicate is worse than a missed message —
  and surfaces as "needs review".
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.enums import AuditAction, LinkedInStatus
from app.models.linkedin_message import LinkedInMessage
from app.models.linkedin_search_lead import (
    LinkedInSearchLead,
    LinkedInSearchSend,
    SearchSendStatus,
)
from app.services.app_settings import get_setting, set_setting
from app.services.audit import log_action
from app.services.linkedin_budget import linkedin_sent_today
from app.services.linkedin_providers import get_linkedin_provider

logger = logging.getLogger(__name__)

#: Where the active LinkedIn account id lives. The same row the LinkedIn and
#: Followers pages read, so "which account am I acting as" has one answer.
ACTIVE_ACCOUNT_SETTING = "linkedin_account_id"
#: Job state, so the page survives a refresh, a restart, or a closed browser.
PROGRESS_SETTING = "linkedin_search_progress"

#: A "running" record older than this has no worker behind it any more.
STALE_JOB_AFTER = timedelta(minutes=10)

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"

#: Identifies THIS process's claims, so a claim from a worker that no longer
#: exists can be told apart from one still being worked on.
_PROCESS_TOKEN = uuid.uuid4().hex[:16]

#: One job at a time. Repeated clicks must not stack two workers over the same
#: leads — the checkpoint would stop the duplicate, but the wasted provider calls
#: and the confusing progress record are worth avoiding outright.
_JOB_LOCKS: dict[str, threading.Lock] = {
    "search": threading.Lock(),
    "draft": threading.Lock(),
    "send": threading.Lock(),
}

_IDLE: dict = {
    "job": None,
    "status": STATUS_IDLE,
    "total": 0,
    "done": 0,
    "sent": 0,
    "invited": 0,
    "skipped": 0,
    "failed": 0,
    "duplicates": 0,
    "held": 0,
    "imported": 0,
    "heartbeat": None,
    "stop_requested": False,
    "message": None,
    "campaign_key": None,
}

_STOP = threading.Event()


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------


def campaign_key_for(message: Optional[str]) -> Optional[str]:
    """sha1 of the normalised message text — the campaign half of the dedup key.

    Editing the message starts a NEW campaign, which is what makes the same
    people eligible again under genuinely different copy.
    """
    text = " ".join((message or "").split()).strip().lower()
    if not text:
        return None
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def search_key_for(filters: Optional[dict]) -> Optional[str]:
    """sha1 of the normalised filter set, identifying one saved search.

    Keys are sorted and blanks dropped before hashing, so the same filters typed
    in a different order are the same search rather than a second one.
    """
    cleaned = {
        key: value
        for key, value in sorted((filters or {}).items())
        if value not in (None, "", [], {})
    }
    if not cleaned:
        return None
    blob = json.dumps(cleaned, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _cursor_setting(account_id: str, search_key: str) -> str:
    """Where this search left off, per account.

    ``app_settings.key`` is 100 characters, and an account id plus a 40-char sha1
    fits, but the search key is hashed again to keep it comfortably inside.
    """
    short = hashlib.sha1(f"{account_id}:{search_key}".encode("utf-8")).hexdigest()
    return f"linkedin_search_cursor:{short}"


def read_cursor(account_id: str, search_key: str) -> Optional[str]:
    return (get_setting(_cursor_setting(account_id, search_key)) or "").strip() or None


def write_cursor(account_id: str, search_key: str, cursor: Optional[str]) -> None:
    """Remember the next page, or clear it once the results run out."""
    set_setting(_cursor_setting(account_id, search_key), cursor or "")


def active_account_id() -> Optional[str]:
    value = (get_setting(ACTIVE_ACCOUNT_SETTING) or "").strip()
    return value or (settings.unipile_account_id or "").strip() or None


# --------------------------------------------------------------------------
# Message building — verbatim, no model call
# --------------------------------------------------------------------------


def first_name_of(name: Optional[str]) -> str:
    """First token of a display name, or a neutral fallback.

    LinkedIn names carry suffixes ("Jennie Reis, CPCC, ACC"), so only the first
    token is safe to greet with.
    """
    token = (name or "").strip().split(" ")[0].strip().strip(",")
    token = re.sub(r"[^A-Za-z\-']", "", token)
    return token or "there"


def build_dm(*, message: str, name: Optional[str]) -> str:
    """``Hi <first name>,`` then the message EXACTLY as written.

    No model rewrites this. The same decision as the followers lane: generated
    copy softened the offer into a question, so the text the user typed is the
    text that gets sent.
    """
    return f"Hi {first_name_of(name)},\n\n{(message or '').strip()}"


def build_invite_note(*, note: Optional[str], message: str, name: Optional[str]) -> str:
    """The <=300 character note carried by a connection invitation.

    Falls back to the message itself, which is what the existing LinkedIn module
    does (``msg.invitation_note or msg.body``), so an empty note box behaves the
    same way rather than sending a bare invitation.
    """
    text = (note or "").strip() or build_dm(message=message, name=name)
    return text[: settings.linkedin_invite_note_max_chars]


# --------------------------------------------------------------------------
# Progress (its own record, so followers jobs are unaffected)
# --------------------------------------------------------------------------


def read_progress() -> dict:
    raw = get_setting(PROGRESS_SETTING)
    if not raw:
        return dict(_IDLE)
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        return dict(_IDLE)
    merged = dict(_IDLE)
    merged.update(state if isinstance(state, dict) else {})
    return merged


def _write(**fields) -> None:
    state = read_progress()
    state.update(fields)
    state["heartbeat"] = datetime.utcnow().isoformat()
    set_setting(PROGRESS_SETTING, json.dumps(state))


def write_progress(**fields) -> None:
    _write(**fields)


def start_progress(job: str, *, total: int, campaign_key: Optional[str] = None) -> None:
    _STOP.clear()
    set_setting(
        PROGRESS_SETTING,
        json.dumps(
            {
                **_IDLE,
                "job": job,
                "status": STATUS_RUNNING,
                "total": int(total),
                "campaign_key": campaign_key,
                "heartbeat": datetime.utcnow().isoformat(),
            }
        ),
    )


def finish_progress(*, stopped: bool = False, message: Optional[str] = None) -> None:
    _write(
        status=STATUS_STOPPED if stopped else STATUS_DONE,
        stop_requested=False,
        message=message,
    )


def fail_progress(message: str) -> None:
    _write(status=STATUS_FAILED, stop_requested=False, message=message)


def stale_progress(state: dict) -> Optional[dict]:
    """Report a ``running`` record with no live worker behind it as failed.

    Applied where the record is READ, never where it is written: a live worker
    that has gone quiet must still be able to write and still count as running.
    """
    if state.get("status") != STATUS_RUNNING:
        return None
    beat = state.get("heartbeat")
    try:
        last = datetime.fromisoformat(beat) if beat else None
    except (TypeError, ValueError):
        last = None
    if last is not None and datetime.utcnow() - last < STALE_JOB_AFTER:
        return None
    corrected = dict(state)
    corrected["status"] = STATUS_FAILED
    corrected["stop_requested"] = False
    corrected["message"] = (
        "That job stopped when the server restarted. Anything it saved is kept — "
        "press the button again to carry on."
    )
    return corrected


def clear_stale_progress() -> bool:
    """Retire a dead ``running`` record so the page's buttons come back."""
    state = read_progress()
    if stale_progress(state) is None:
        return False
    set_setting(PROGRESS_SETTING, json.dumps({**_IDLE, "job": state.get("job")}))
    return True


def request_stop() -> bool:
    state = read_progress()
    if state.get("status") != STATUS_RUNNING:
        return False
    _STOP.set()
    _write(stop_requested=True)
    return True


def stop_requested() -> bool:
    return _STOP.is_set()


def sleep_unless_stopped(seconds: float) -> bool:
    """Pace between sends, but wake immediately when Stop is pressed."""
    return _STOP.wait(timeout=seconds)


def _run_job(kind: str, work) -> bool:
    """Run ``work`` on a daemon thread under the per-kind lock."""
    lock = _JOB_LOCKS[kind]
    if not lock.acquire(blocking=False):
        return False
    state = read_progress()
    if state.get("status") == STATUS_RUNNING and stale_progress(state) is None:
        lock.release()
        return False

    def runner() -> None:
        db = SessionLocal()
        try:
            work(db)
        except Exception as exc:  # noqa: BLE001 - a job must not kill the process
            logger.exception("LinkedIn search %s job failed: %s", kind, exc)
            try:
                fail_progress(str(exc)[:300])
            except Exception:  # noqa: BLE001
                pass
        finally:
            db.close()
            lock.release()

    threading.Thread(target=runner, daemon=True, name=f"li-search-{kind}").start()
    return True


# --------------------------------------------------------------------------
# Search + import
# --------------------------------------------------------------------------


#: How many NEW people one press of Search should bring. Matches the daily send
#: cap, because that is what a day's work is.
SEARCH_BATCH = 50

#: Safety bound on how many provider calls one press may make. Classic search
#: returns ten per page, so fifty people is five calls; the rest of the headroom
#: is for pages that are mostly people already stored.
MAX_SEARCH_PAGES = 25


def run_search(
    db: Session,
    *,
    account_id: str,
    filters: dict,
    api: str,
    search_key: str,
    want: int = SEARCH_BATCH,
) -> dict:
    """Pull results until ``want`` NEW people are stored, or LinkedIn runs out.

    Counted in people, not pages, because a page is not a fixed size: Sales
    Navigator returns the 50 it is asked for, while CLASSIC search returns TEN
    per page and ignores the limit entirely. Fetching "one page" therefore gave
    50 people on one API and 10 on the other, which is exactly what a press of
    Search felt like. Pages are now an implementation detail — it keeps asking
    for the next one until it has what was wanted.

    Stored, not just shown, because everything after this — drafting, the tab
    counts, the send queue — has to survive a page refresh and a restart.
    """
    provider = get_linkedin_provider(account_id)
    known = {
        row.provider_id
        for row in db.execute(
            select(LinkedInSearchLead).where(
                LinkedInSearchLead.account_id == account_id,
                LinkedInSearchLead.search_key == search_key,
            )
        ).scalars().all()
    }
    imported = skipped = 0
    # Resume where the last run for these filters stopped. Without this every run
    # asked LinkedIn for page one again, so the same 50 people came back, were
    # all recognised as already stored, and a repeat search found nobody new.
    cursor: Optional[str] = read_cursor(account_id, search_key)
    total: Optional[int] = None
    error: Optional[str] = None
    exhausted = False

    want = max(1, int(want))
    pages_fetched = 0
    while imported < want:
        if stop_requested():
            break
        # Bounded so a filter that keeps returning people already stored cannot
        # walk LinkedIn all night looking for its fiftieth new one.
        if pages_fetched >= MAX_SEARCH_PAGES:
            break
        pages_fetched += 1
        page = provider.search_people(
            filters=filters, api=api, cursor=cursor, limit=want
        )
        if not page.supported or page.error:
            error = page.error or "This provider cannot search LinkedIn."
            break
        if total is None:
            total = page.total
        for lead in page.leads:
            if lead.provider_id in known:
                skipped += 1
                continue
            db.add(
                LinkedInSearchLead(
                    account_id=account_id,
                    search_key=search_key,
                    provider_id=lead.provider_id,
                    public_identifier=lead.public_identifier,
                    profile_url=lead.profile_url,
                    name=lead.name,
                    first_name=lead.first_name,
                    headline=lead.headline,
                    location=lead.location,
                    company=lead.company,
                    job_title=lead.job_title,
                    network_distance=lead.network_distance,
                    picture_url=lead.picture_url,
                )
            )
            # Added to the map as we go, so a person repeated inside one run is
            # updated rather than inserted twice.
            known.add(lead.provider_id)
            imported += 1
        db.commit()
        write_progress(imported=imported, done=imported)
        cursor = page.cursor
        # Saved per page, not at the end: a run stopped or killed half way still
        # carries on from the right place next time.
        write_cursor(account_id, search_key, cursor)
        if not cursor:
            # LinkedIn has no more pages. The cursor is already cleared above, so
            # the next run starts the search over — which is what picks up people
            # who match these filters but were not there before.
            exhausted = True
            break

    return {
        "imported": imported,
        "skipped": skipped,
        "total": total,
        "error": error,
        "exhausted": exhausted,
    }


def launch_search(
    *, account_id: str, filters: dict, api: str, search_key: str, want: int
) -> bool:
    def work(db: Session) -> None:
        start_progress("search", total=0)
        result = run_search(
            db,
            account_id=account_id,
            filters=filters,
            api=api,
            search_key=search_key,
            want=want,
        )
        if result["error"]:
            fail_progress(result["error"][:300])
            return
        found = result["imported"]
        finish_progress(
            stopped=stop_requested(),
            message=(
                f"Found {found} new {'person' if found == 1 else 'people'}."
                + (f" {result['skipped']} already on the list." if result["skipped"] else "")
                + (
                    " That is everyone LinkedIn has for these filters — "
                    "searching again starts from the top."
                    if result["exhausted"]
                    else " Search again for the next batch."
                )
            ),
        )

    return _run_job("search", work)


# --------------------------------------------------------------------------
# Audience queries
# --------------------------------------------------------------------------


def settled_lead_ids(db: Session, *, account_id: str, campaign_key: str) -> set[str]:
    """People this account must NOT contact again under this message.

    CLAIMED counts as settled because its outcome is unknown: a claim whose
    worker died may well have delivered. FAILED and SKIPPED are absent on
    purpose — those are safe to attempt again.
    """
    rows = db.execute(
        select(LinkedInSearchSend.lead_provider_id).where(
            LinkedInSearchSend.account_id == account_id,
            LinkedInSearchSend.campaign_key == campaign_key,
            LinkedInSearchSend.status.in_(SearchSendStatus.SETTLED),
        )
    ).scalars().all()
    return {row for row in rows if row}


def eligible_leads(
    db: Session,
    *,
    account_id: str,
    search_key: str,
    campaign_key: str,
    limit: Optional[int] = None,
) -> list[LinkedInSearchLead]:
    """Leads of this search with no message yet for this campaign."""
    drafted = select(LinkedInMessage.search_lead_id).where(
        LinkedInMessage.search_lead_id.is_not(None),
        LinkedInMessage.search_campaign_key == campaign_key,
    )
    query = (
        select(LinkedInSearchLead)
        .where(
            LinkedInSearchLead.account_id == account_id,
            LinkedInSearchLead.search_key == search_key,
            LinkedInSearchLead.id.not_in(drafted),
        )
        .order_by(LinkedInSearchLead.id)
    )
    if limit is not None:
        query = query.limit(limit)
    leads = list(db.execute(query).scalars().all())
    settled = settled_lead_ids(db, account_id=account_id, campaign_key=campaign_key)
    if not settled:
        return leads
    return [lead for lead in leads if lead.provider_id not in settled]


def account_lead_filter(account_id: str, search_key: Optional[str] = None):
    """Restrict search messages to THIS account's leads (and one search).

    ``linkedin_messages`` has no account of its own, and the campaign key is only
    a hash of the message text, so without this two connected accounts running
    the same message would share one pool of drafts — the exact bug the followers
    module had to be fixed for.
    """
    lead_ids = select(LinkedInSearchLead.id).where(
        LinkedInSearchLead.account_id == account_id
    )
    if search_key:
        lead_ids = lead_ids.where(LinkedInSearchLead.search_key == search_key)
    return LinkedInMessage.search_lead_id.in_(lead_ids)


def unsettled_lead_filter(account_id: str, campaign_key: str):
    """Exclude drafts whose lead has already been reached under this message.

    Such a row is left behind whenever a lead ends up with more than one draft:
    one sends and moves on, the duplicate stays APPROVED forever. Counting those
    as work still to do is what let the followers page offer to send 97 messages
    for a run that could only ever attempt 3.
    """
    settled = select(LinkedInSearchSend.lead_provider_id).where(
        LinkedInSearchSend.account_id == account_id,
        LinkedInSearchSend.campaign_key == campaign_key,
        LinkedInSearchSend.status.in_(SearchSendStatus.SETTLED),
    )
    settled_leads = select(LinkedInSearchLead.id).where(
        LinkedInSearchLead.account_id == account_id,
        LinkedInSearchLead.provider_id.in_(settled),
    )
    return LinkedInMessage.search_lead_id.not_in(settled_leads)


def open_message_conditions(
    account_id: str, campaign_key: str, search_key: Optional[str] = None
) -> list:
    """Every condition for "a search DM that can still be sent".

    One definition shared by the send queue, the tab counts and the list, so the
    three can never disagree about how much work is left.
    """
    return [
        LinkedInMessage.search_lead_id.is_not(None),
        LinkedInMessage.search_campaign_key == campaign_key,
        LinkedInMessage.status.in_([LinkedInStatus.DRAFT, LinkedInStatus.APPROVED]),
        account_lead_filter(account_id, search_key),
        unsettled_lead_filter(account_id, campaign_key),
    ]


# --------------------------------------------------------------------------
# Drafting
# --------------------------------------------------------------------------


def draft_for_leads(
    db: Session,
    *,
    account_id: str,
    search_key: str,
    campaign_key: str,
    message: str,
    invitation_note: Optional[str],
    principal_id: int,
    limit: Optional[int],
) -> int:
    """Write one draft per eligible lead. Pure string work, no provider calls."""
    leads = eligible_leads(
        db,
        account_id=account_id,
        search_key=search_key,
        campaign_key=campaign_key,
        limit=limit,
    )
    start_progress("draft", total=len(leads), campaign_key=campaign_key)
    written = 0
    for lead in leads:
        if stop_requested():
            break
        db.add(
            LinkedInMessage(
                principal_id=principal_id,
                search_lead_id=lead.id,
                search_campaign_key=campaign_key,
                body=build_dm(message=message, name=lead.name),
                invitation_note=build_invite_note(
                    note=invitation_note, message=message, name=lead.name
                ),
                status=LinkedInStatus.DRAFT,
                linkedin_provider_id=lead.provider_id,
                public_identifier=lead.public_identifier,
                network_distance=lead.network_distance,
                connected=(lead.network_distance == "1"),
            )
        )
        written += 1
        if written % 50 == 0:
            db.commit()
            write_progress(done=written)
    db.commit()
    finish_progress(
        stopped=stop_requested(),
        message=f"Prepared {written} message(s).",
    )
    return written


def launch_draft(
    *,
    account_id: str,
    search_key: str,
    campaign_key: str,
    message: str,
    invitation_note: Optional[str],
    principal_id: int,
    limit: Optional[int],
) -> bool:
    def work(db: Session) -> None:
        draft_for_leads(
            db,
            account_id=account_id,
            search_key=search_key,
            campaign_key=campaign_key,
            message=message,
            invitation_note=invitation_note,
            principal_id=principal_id,
            limit=limit,
        )

    return _run_job("draft", work)


def approve_all(
    db: Session,
    *,
    account_id: str,
    campaign_key: str,
    search_key: Optional[str] = None,
    approved_by: str = "user",
) -> int:
    """Approve every draft for this message, for THIS account's leads."""
    drafts = list(
        db.execute(
            select(LinkedInMessage).where(
                LinkedInMessage.search_lead_id.is_not(None),
                LinkedInMessage.search_campaign_key == campaign_key,
                LinkedInMessage.status == LinkedInStatus.DRAFT,
                account_lead_filter(account_id, search_key),
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
            entity_type="linkedin_search",
            actor="human",
            summary=f"Approved {len(drafts)} search message(s)",
            commit=True,
        )
    db.commit()
    return len(drafts)


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------


def _claim(
    db: Session,
    *,
    account_id: str,
    lead: LinkedInSearchLead,
    campaign_key: str,
    message_id: int,
) -> Optional[LinkedInSearchSend]:
    """Reserve this (account, lead, campaign) before sending.

    Returns None when the reservation is refused, which is the whole point: a
    duplicate click, a retried request or a second worker hits the UNIQUE index
    and gets None instead of sending a second invitation. An existing retryable
    row is re-used in place so retries never grow the table.
    """
    now = datetime.utcnow()
    existing = db.execute(
        select(LinkedInSearchSend).where(
            LinkedInSearchSend.account_id == account_id,
            LinkedInSearchSend.lead_provider_id == lead.provider_id,
            LinkedInSearchSend.campaign_key == campaign_key,
        )
    ).scalars().first()
    if existing is not None:
        if existing.status not in SearchSendStatus.RETRYABLE:
            return None
        existing.status = SearchSendStatus.CLAIMED
        existing.claimed_by = _PROCESS_TOKEN
        existing.claimed_at = now
        existing.message_id = message_id
        existing.error = None
        db.commit()
        return existing
    claim = LinkedInSearchSend(
        account_id=account_id,
        lead_provider_id=lead.provider_id,
        campaign_key=campaign_key,
        status=SearchSendStatus.CLAIMED,
        claimed_by=_PROCESS_TOKEN,
        claimed_at=now,
        message_id=message_id,
    )
    db.add(claim)
    try:
        db.commit()
    except IntegrityError:
        # Someone else claimed it between the SELECT and the INSERT. That is the
        # index doing its job, not an error worth surfacing.
        db.rollback()
        return None
    return claim


def send_one(
    db: Session,
    *,
    msg: LinkedInMessage,
    lead: LinkedInSearchLead,
    account_id: str,
    campaign_key: str,
) -> str:
    """Reach one lead under the checkpoint. Returns the outcome.

    Outcomes: ``sent`` (DM delivered) | ``invited`` (connection request sent) |
    ``skipped`` | ``failed`` | ``duplicate``.

    The reach decision is the existing LinkedIn module's, deliberately: connected
    means a direct message, everyone else gets an invitation carrying the note
    and the message is delivered when they accept.
    """
    claim = _claim(
        db,
        account_id=account_id,
        lead=lead,
        campaign_key=campaign_key,
        message_id=msg.id,
    )
    if claim is None:
        return "duplicate"

    try:
        return _reach(
            db, msg=msg, lead=lead, account_id=account_id, claim=claim
        )
    except Exception as exc:  # noqa: BLE001 - the claim must not be left dangling
        # The claim is already written, so an exception here would otherwise
        # leave a CLAIMED row owned by a process that is still alive — settled,
        # never retried, and invisible to "needs review", which looks for claims
        # whose owner is gone. Disowning it puts it in front of a human instead.
        db.rollback()
        claim.claimed_by = None
        claim.error = f"Crashed mid-send: {exc}"[:500]
        msg.error = claim.error
        db.commit()
        raise


def _reach(
    db: Session,
    *,
    msg: LinkedInMessage,
    lead: LinkedInSearchLead,
    account_id: str,
    claim: LinkedInSearchSend,
) -> str:
    """The reach attempt itself, once the claim is held."""
    provider = get_linkedin_provider(account_id)
    identifier = lead.public_identifier or lead.provider_id
    profile = provider.resolve_profile(identifier)

    # An unresolvable profile is a failure, not a skip: it is usually transport
    # or rate limiting, and must stay retryable.
    if not profile.found or not profile.provider_id:
        claim.status = SearchSendStatus.FAILED
        claim.error = profile.error or "Could not resolve LinkedIn profile"
        msg.error = claim.error
        db.commit()
        return "failed"

    msg.provider = provider.name
    msg.from_account = getattr(provider, "account_id", None) or account_id
    msg.linkedin_provider_id = profile.provider_id
    msg.public_identifier = profile.public_identifier
    msg.network_distance = profile.network_distance
    msg.connected = profile.is_connected

    if profile.is_connected:
        result = provider.send_message(provider_id=profile.provider_id, text=msg.body)
        if not result.sent:
            claim.status = (
                SearchSendStatus.SKIPPED if result.unreachable else SearchSendStatus.FAILED
            )
            claim.error = result.error or "LinkedIn send failed"
            msg.error = claim.error
            db.commit()
            return "skipped" if result.unreachable else "failed"
        msg.provider_chat_id = result.chat_id
        msg.provider_message_id = result.message_id
        msg.status = LinkedInStatus.SENT
        msg.sent_at = datetime.utcnow()
        msg.error = None
        claim.status = SearchSendStatus.SENT
        claim.reach = "dm"
        claim.sent_at = msg.sent_at
        claim.error = None
        db.commit()
        return "sent"

    invite = provider.send_invitation(
        provider_id=profile.provider_id,
        note=(msg.invitation_note or msg.body or "")[
            : settings.linkedin_invite_note_max_chars
        ],
    )
    if invite.already_connected:
        # Race: they are actually connected. Same fallback the existing LinkedIn
        # send makes — DM instead of failing the whole attempt.
        result = provider.send_message(provider_id=profile.provider_id, text=msg.body)
        if not result.sent:
            claim.status = SearchSendStatus.FAILED
            claim.error = result.error or "LinkedIn send failed"
            msg.error = claim.error
            db.commit()
            return "failed"
        msg.connected = True
        msg.provider_chat_id = result.chat_id
        msg.provider_message_id = result.message_id
        msg.status = LinkedInStatus.SENT
        msg.sent_at = datetime.utcnow()
        msg.error = None
        claim.status = SearchSendStatus.SENT
        claim.reach = "dm"
        claim.sent_at = msg.sent_at
        claim.error = None
        db.commit()
        return "sent"
    if not invite.sent:
        claim.status = SearchSendStatus.FAILED
        claim.error = invite.error or "LinkedIn invitation failed"
        msg.error = claim.error
        db.commit()
        return "failed"

    msg.provider_invitation_id = invite.invitation_id
    msg.status = LinkedInStatus.INVITE_SENT
    msg.invitation_sent_at = datetime.utcnow()
    msg.error = None
    claim.status = SearchSendStatus.INVITED
    claim.reach = "invite"
    claim.sent_at = msg.invitation_sent_at
    claim.error = None
    db.commit()
    return "invited"


def _attempts_by_lead(db: Session, *, account_id: str, campaign_key: str) -> dict[str, int]:
    """How many times each lead has already been tried under this message."""
    rows = db.execute(
        select(LinkedInSearchSend.lead_provider_id, LinkedInSearchSend.status).where(
            LinkedInSearchSend.account_id == account_id,
            LinkedInSearchSend.campaign_key == campaign_key,
        )
    ).all()
    return {provider_id: 1 for provider_id, _status in rows if provider_id}


def send_all(
    db: Session,
    *,
    account_id: str,
    campaign_key: str,
    search_key: Optional[str] = None,
    approve_first: bool = True,
) -> None:
    """Approve (optionally) then reach every open lead, paced and capped.

    Shares the account's ``linkedin_daily_send_cap`` with every other LinkedIn
    send path, because LinkedIn's limit is per account and does not care which
    module spent it. The overflow is held and goes out on the next run.
    """
    if approve_first:
        approve_all(
            db, account_id=account_id, campaign_key=campaign_key, search_key=search_key
        )

    messages = list(
        db.execute(
            select(LinkedInMessage)
            .where(*open_message_conditions(account_id, campaign_key, search_key))
            .order_by(LinkedInMessage.id)
        ).scalars().all()
    )

    cap = max(0, int(settings.linkedin_daily_send_cap))
    sent_today = linkedin_sent_today(db, account_id)
    remaining = max(0, cap - sent_today)

    # Least-tried leads first. A profile LinkedIn refuses to resolve stays
    # RETRYABLE on purpose, so it comes back in every run — ordered by message id
    # that block of old failures would sit at the FRONT and burn the day's
    # allowance again every day, which is exactly what happened in the followers
    # module before it was ordered this way.
    attempts = _attempts_by_lead(db, account_id=account_id, campaign_key=campaign_key)
    messages.sort(
        key=lambda m: (attempts.get(m.linkedin_provider_id or "", 0), m.id)
    )

    # The cap counts things that actually LEFT, not attempts, so a failure or a
    # duplicate costs an attempt rather than one of the day's places.
    target = min(remaining, len(messages))
    start_progress("send", total=target, campaign_key=campaign_key)
    write_progress(
        message=(
            f"{len(messages) - target} or more held for the next run."
            if len(messages) > target
            else None
        )
    )

    delay = max(0.0, float(settings.bulk_linkedin_send_delay_seconds))
    sent = invited = skipped = failed = duplicates = 0
    attempted = 0
    stopped = False

    for msg in messages:
        if (sent + invited) >= remaining:
            break
        if stop_requested():
            stopped = True
            break
        attempted += 1
        lead = db.get(LinkedInSearchLead, msg.search_lead_id)
        if lead is None:
            failed += 1
            write_progress(done=sent + invited, failed=failed)
            continue
        try:
            outcome = send_one(
                db,
                msg=msg,
                lead=lead,
                account_id=account_id,
                campaign_key=campaign_key,
            )
        except Exception as exc:  # noqa: BLE001 - one send must not kill the run
            db.rollback()
            outcome = "failed"
            logger.exception("Search send crashed for %s: %s", lead.provider_id, exc)
        if outcome == "sent":
            sent += 1
        elif outcome == "invited":
            invited += 1
        elif outcome == "skipped":
            skipped += 1
        elif outcome == "duplicate":
            duplicates += 1
        else:
            failed += 1
        write_progress(
            done=sent + invited,
            sent=sent,
            invited=invited,
            skipped=skipped,
            failed=failed,
        )
        # Pace only after something actually left; a skip cost the account nothing.
        if outcome in ("sent", "invited") and (sent + invited) < remaining and delay:
            if sleep_unless_stopped(delay):
                stopped = True
                break

    held = max(0, len(messages) - attempted)
    write_progress(duplicates=duplicates, held=held)
    finish_progress(
        stopped=stopped,
        message=(
            f"{invited} invitation(s) sent, {sent} DM(s) sent."
            + (f" {skipped} not reachable." if skipped else "")
            + (f" {failed} failed." if failed else "")
            + (f" {duplicates} already contacted." if duplicates else "")
            + (f" {held} left for the next run." if held else "")
            + (" Stopped early." if stopped else "")
        ),
    )


def launch_send(
    *, account_id: str, campaign_key: str, search_key: Optional[str] = None
) -> bool:
    def work(db: Session) -> None:
        send_all(
            db,
            account_id=account_id,
            campaign_key=campaign_key,
            search_key=search_key,
        )

    return _run_job("send", work)


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------

#: How far along a lead is under one message, used to pick ONE row when a lead
#: ends up with several. The list applies the same order, so the tab counts and
#: the rows underneath them can never disagree.
PROGRESS_RANK = {
    LinkedInStatus.REPLIED: 5,
    LinkedInStatus.SENT: 4,
    LinkedInStatus.INVITE_SENT: 3,
    LinkedInStatus.APPROVED: 2,
    LinkedInStatus.DRAFT: 1,
}


def campaign_stats(
    db: Session, *, account_id: str, search_key: Optional[str], campaign_key: str
) -> dict:
    """Tab counts for one search + message, counting PEOPLE not message rows."""
    rows = db.execute(
        select(
            LinkedInMessage.search_lead_id,
            LinkedInMessage.status,
            LinkedInMessage.id,
        ).where(
            LinkedInMessage.search_lead_id.is_not(None),
            LinkedInMessage.search_campaign_key == campaign_key,
            account_lead_filter(account_id, search_key),
        )
    ).all()

    best: dict[int, tuple[tuple[int, int], str]] = {}
    for lead_id, status, message_id in rows:
        rank = (PROGRESS_RANK.get(status, 0), message_id or 0)
        current = best.get(lead_id)
        if current is None or rank > current[0]:
            best[lead_id] = (rank, status)

    settled_providers = settled_lead_ids(
        db, account_id=account_id, campaign_key=campaign_key
    )
    settled_ids = set(
        db.execute(
            select(LinkedInSearchLead.id).where(
                LinkedInSearchLead.account_id == account_id,
                LinkedInSearchLead.provider_id.in_(settled_providers or [""]),
            )
        ).scalars().all()
    )

    counts = {
        "all": 0,
        "draft": 0,
        "approved": 0,
        "invite_sent": 0,
        "sent": 0,
        "replied": 0,
    }
    for lead_id, (_rank, status) in best.items():
        counts["all"] += 1
        if status in (LinkedInStatus.DRAFT, LinkedInStatus.APPROVED) and (
            lead_id in settled_ids
        ):
            continue
        if status in counts:
            counts[status] += 1

    leads_query = select(func.count()).select_from(LinkedInSearchLead).where(
        LinkedInSearchLead.account_id == account_id
    )
    if search_key:
        leads_query = leads_query.where(LinkedInSearchLead.search_key == search_key)

    cap = max(0, int(settings.linkedin_daily_send_cap))
    sent_today = linkedin_sent_today(db, account_id)
    return {
        **counts,
        "leads_total": int(db.execute(leads_query).scalar_one()),
        "eligible": len(
            eligible_leads(
                db,
                account_id=account_id,
                search_key=search_key or "",
                campaign_key=campaign_key,
            )
        )
        if search_key
        else 0,
        "contacted_ever": len(settled_providers),
        "needs_review": int(
            db.execute(
                select(func.count())
                .select_from(LinkedInSearchSend)
                .where(
                    LinkedInSearchSend.account_id == account_id,
                    LinkedInSearchSend.campaign_key == campaign_key,
                    LinkedInSearchSend.status == SearchSendStatus.CLAIMED,
                    LinkedInSearchSend.claimed_by != _PROCESS_TOKEN,
                )
            ).scalar_one()
        ),
        "cap": cap,
        "sent_today": sent_today,
        "remaining_today": max(0, cap - sent_today),
    }
