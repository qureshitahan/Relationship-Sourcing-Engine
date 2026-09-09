"""Followers LinkedIn: DM the followers of a connected LinkedIn account.

A separate router from ``/linkedin`` on purpose. It reuses that module's account
picker (``/linkedin/accounts``, ``/linkedin/select-account``) so "which account
am I acting as" has one answer app-wide, and it reuses the same provider, the
same ``LinkedInMessage`` table and the same per-account daily cap — but its own
audience, its own send path (DM only, never a connection invitation) and its own
durable checkpoint. See ``services/linkedin_followers`` for the guarantees.

Long-running work (sync, draft, send) runs on a background thread and reports
through ``GET /progress``. Sending is one provider call per follower, paced ~20s
apart, which outlives both the browser's timeout and the gateway's. Drafting is
pure string formatting (no model call) so it is fast, but it stays on the same
background path for a consistent progress UI over thousands of followers.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.enums import AuditAction, LinkedInStatus
from app.models.linkedin_follower import (
    FollowerSendStatus,
    LinkedInFollower,
    LinkedInFollowerSend,
)
from app.models.linkedin_message import LinkedInMessage
from app.schemas.entities import Page
from app.schemas.requests import (
    FollowerActionRequest,
    FollowerDraftRequest,
    FollowerSyncRequest,
)
from app.services import linkedin_followers as service
from app.services.audit import log_action
from app.services.linkedin_providers import get_linkedin_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/linkedin-followers", tags=["linkedin-followers"])


class FollowerOut(BaseModel):
    """A follower plus its outreach state for the requested campaign."""

    id: int
    account_id: str
    provider_id: str
    name: Optional[str] = None
    headline: Optional[str] = None
    profile_url: Optional[str] = None
    picture_url: Optional[str] = None
    # Message state for this campaign (None = not drafted yet).
    message_id: Optional[int] = None
    message_status: Optional[str] = None
    body: Optional[str] = None
    # Checkpoint state for this campaign (None = never attempted).
    send_status: Optional[str] = None
    reach: Optional[str] = None
    sent_at: Optional[str] = None
    error: Optional[str] = None
    replied_at: Optional[str] = None
    reply_snippet: Optional[str] = None


def _resolve_account(account_id: Optional[str]) -> str:
    resolved = (account_id or "").strip() or service.active_account_id()
    if not resolved:
        raise HTTPException(
            status_code=400,
            detail="No LinkedIn account is selected. Connect one and pick it first.",
        )
    return resolved


def _resolve_campaign(message: str) -> str:
    key = service.campaign_key_for(message)
    if not key:
        raise HTTPException(
            status_code=400,
            detail="A message is required — it is the text that gets sent.",
        )
    return key


@router.get("/status")
def followers_status(
    db: Session = Depends(get_db),
    message: Optional[str] = None,
    account_id: Optional[str] = None,
):
    """Connection status + roster/campaign counts for the page header.

    Safe to call with no message and no account: it reports what is missing
    instead of failing, so the page can render its own setup state.

    ``account_id`` omitted keeps the old answer — the app-wide selected account.
    A caller that names one gets counts for THAT account, which is what lets a
    tab report on the account it is showing even after someone else switches the
    shared selection (the list endpoint has always accepted this).
    """
    provider = get_linkedin_provider()
    lister = getattr(provider, "list_accounts", None)
    accounts = lister() if lister else []
    account_id = (account_id or "").strip() or service.active_account_id()
    active = next((a for a in accounts if a.get("id") == account_id), None)

    payload: dict = {
        "provider": provider.name,
        "configured": provider.name == "stub"
        or bool(settings.unipile_api_key and settings.unipile_dsn),
        "supports_followers": provider.supports_followers(),
        "active_account_id": account_id,
        "active_account_name": (active or {}).get("name"),
        "active_account_status": (active or {}).get("status"),
        "default_account_id": settings.unipile_account_id or None,
        "accounts": accounts,
        "campaign_key": None,
        "stats": None,
    }
    if not account_id:
        return payload

    payload["followers_total"] = int(
        db.execute(
            select(func.count())
            .select_from(LinkedInFollower)
            .where(LinkedInFollower.account_id == account_id)
        ).scalar_one()
    )
    # Roster-wide progress, independent of any campaign, so the page can answer
    # "how many of my followers have I reached" before a message is even typed.
    payload["contacted_all_time"] = int(
        db.execute(
            select(func.count(func.distinct(LinkedInFollowerSend.follower_provider_id)))
            .where(
                LinkedInFollowerSend.account_id == account_id,
                LinkedInFollowerSend.status == FollowerSendStatus.SENT,
            )
        ).scalar_one()
    )
    key = service.campaign_key_for(message)
    if key:
        payload["campaign_key"] = key
        payload["stats"] = service.campaign_stats(
            db, account_id=account_id, campaign_key=key
        )
    return payload


@router.get("/progress")
def followers_progress():
    """Live state of the running sync/draft/send job (poll this for the bar)."""
    state = service.read_progress()
    # A job whose process died leaves "running" in the row for good. Report that
    # honestly here — this is what the bar and every disabled button read — rather
    # than in read_progress(), so a live worker's own writes are never rewritten.
    return service.stale_progress(state) or state


@router.post("/sync")
def sync(payload: FollowerSyncRequest):
    """Refresh the follower roster in the background."""
    account_id = _resolve_account(payload.account_id)
    provider = get_linkedin_provider(account_id)
    if not provider.supports_followers():
        raise HTTPException(
            status_code=400,
            detail="This LinkedIn provider cannot list followers. "
            "Set LINKEDIN_PROVIDER=unipile with a connected account.",
        )
    if not service.launch_sync(account_id=account_id):
        return {"started": False, "message": "A followers job is already running."}
    return {
        "started": True,
        "account_id": account_id,
        "message": "Refreshing your followers in the background.",
    }


@router.post("/draft-all")
def draft_all(payload: FollowerDraftRequest, db: Session = Depends(get_db)):
    """Draft a DM for every follower not yet drafted for this message."""
    account_id = _resolve_account(payload.account_id)
    campaign_key = _resolve_campaign(payload.message)
    limit = payload.limit if (payload.limit or 0) > 0 else None
    # A target names how many the campaign should END UP with, so pressing the
    # button again tops up rather than doubling. Already there? Nothing to do.
    if (payload.target or 0) > 0:
        # Followers, not message rows. A follower can hold several rows for the
        # same message, so the row count overstated how many PEOPLE were drafted
        # and the target silently went dead early ("already 654" over 507 people).
        existing = service.campaign_people_drafted(
            db, account_id=account_id, campaign_key=campaign_key
        )
        needed = max(0, int(payload.target) - existing)
        if needed == 0:
            return {
                "started": False,
                "candidates": 0,
                "campaign_key": campaign_key,
                "message": f"Already {existing} drafted for this message — "
                "raise the number, or use Append to add more.",
            }
        limit = needed
    eligible = service.eligible_followers(
        db, account_id=account_id, campaign_key=campaign_key, limit=limit
    )
    if not eligible:
        # "Everyone is drafted" and "nobody is synced" both surface as an empty
        # candidate list, and the first wording sent people hunting for drafts
        # that were never possible. Separate them: an account whose roster has
        # not been pulled yet needs a network refresh, not a bigger number.
        synced = int(
            db.execute(
                select(func.count())
                .select_from(LinkedInFollower)
                .where(LinkedInFollower.account_id == account_id)
            ).scalar_one()
        )
        if synced == 0:
            return {
                "started": False,
                "candidates": 0,
                "campaign_key": campaign_key,
                "message": "Nothing synced yet for this account — "
                'click "Refresh network" first.',
            }
        return {
            "started": False,
            "candidates": 0,
            "campaign_key": campaign_key,
            "message": "Every follower already has a draft for this message — "
            'click "Refresh followers" to pick up new ones.',
        }
    started = service.launch_draft(
        account_id=account_id,
        campaign_key=campaign_key,
        message=payload.message,
        principal_id=payload.principal_id,
        limit=limit,
    )
    if not started:
        return {
            "started": False,
            "candidates": len(eligible),
            "campaign_key": campaign_key,
            "message": "A followers job is already running.",
        }
    return {
        "started": True,
        "candidates": len(eligible),
        "campaign_key": campaign_key,
        "message": f"Writing {len(eligible)} DM(s) in the background.",
    }


@router.post("/approve-all")
def approve_all(payload: FollowerActionRequest, db: Session = Depends(get_db)):
    """Approve every drafted DM for this message campaign (no provider calls)."""
    account_id = _resolve_account(payload.account_id)
    campaign_key = _resolve_campaign(payload.message)
    approved = service.approve_all(
        db, account_id=account_id, campaign_key=campaign_key
    )
    return {"approved": approved, "campaign_key": campaign_key}


@router.post("/send-all")
def send_all(payload: FollowerActionRequest, db: Session = Depends(get_db)):
    """Approve + send every open DM for this message, in the background.

    Paced and capped per account exactly like the prospect bulk send, and every
    send passes the checkpoint first, so an already-contacted follower is skipped
    even if this is clicked twice.
    """
    account_id = _resolve_account(payload.account_id)
    campaign_key = _resolve_campaign(payload.message)
    # Counted with the SAME rule the send queue applies. Without the settled
    # filter this counted drafts the queue would refuse, so the page announced
    # "Sending up to 97 DM(s)" for a run that could only ever attempt 3.
    # Distinct followers: duplicate rows for one follower are not extra DMs --
    # the second is refused by the checkpoint -- so counting rows here promised
    # more sends than could ever land, the same unit mismatch as the tab counts.
    open_count = int(
        db.execute(
            select(func.count(func.distinct(LinkedInMessage.follower_id)))
            .select_from(LinkedInMessage)
            .where(*service.open_message_conditions(account_id, campaign_key))
        ).scalar_one()
    )
    if not open_count:
        return {
            "started": False,
            "matched": 0,
            "campaign_key": campaign_key,
            "message": 'Nothing to send — click "Draft all" first.',
        }
    if not service.launch_send(
        account_id=account_id, campaign_key=campaign_key, message=payload.message
    ):
        return {
            "started": False,
            "matched": open_count,
            "campaign_key": campaign_key,
            "message": "A followers job is already running.",
        }
    return {
        "started": True,
        "matched": open_count,
        "campaign_key": campaign_key,
        "message": f"Sending up to {open_count} DM(s) in the background.",
    }


@router.post("/stop")
def stop(db: Session = Depends(get_db)):
    """Halt the running job between items.

    The message in flight finishes, so nothing is left half-sent: its checkpoint
    row is resolved before the worker looks at the stop flag again.
    """
    if not service.request_stop():
        # Nothing to ask, but the record may still be claiming to run with no
        # worker behind it — the one case where Stop could previously do nothing
        # at all, forever. Retiring it here is what frees the page.
        if service.clear_stale_progress():
            return {
                "stopped": True,
                "message": "That job had already stopped when the server "
                "restarted — cleared it. Anything it saved is kept.",
            }
        return {"stopped": False, "message": "No followers job is running."}
    log_action(
        db,
        AuditAction.LINKEDIN_SEND,
        entity_type="linkedin_followers",
        actor="human",
        summary="Stopped the followers job",
        commit=True,
    )
    return {
        "stopped": True,
        "message": "Stopping — the message in flight finishes, then it halts.",
    }


#: How far along a message row is, for picking one row per follower when a
#: follower ended up with several. Defined in the service and reused here so the
#: tab counts and the rows this list returns can never rank a follower
#: differently -- that disagreement is exactly what made Sent read 597 over 503
#: people.
_PROGRESS_RANK = service.PROGRESS_RANK


def _progress_rank(msg: LinkedInMessage) -> tuple[int, int]:
    """Rank, then id, so two rows at the same stage resolve to the newer one."""
    return (_PROGRESS_RANK.get(msg.status, 0), msg.id or 0)


@router.get("", response_model=Page[FollowerOut])
def list_followers(
    db: Session = Depends(get_db),
    message: Optional[str] = None,
    status: Optional[str] = Query(
        None, description="draft | approved | sent | replied | pending"
    ),
    account_id: Optional[str] = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
):
    """Followers of the selected account, joined to their state for this message.

    Everything returned comes from the database, so the tabs read the same after
    a page refresh or a server restart as they did before it.
    """
    resolved_account = _resolve_account(account_id)
    campaign_key = service.campaign_key_for(message)

    messages: dict[int, LinkedInMessage] = {}
    sends: dict[str, LinkedInFollowerSend] = {}
    if campaign_key:
        for msg in db.execute(
            select(LinkedInMessage).where(
                LinkedInMessage.follower_id.is_not(None),
                LinkedInMessage.follower_campaign_key == campaign_key,
            )
        ).scalars().all():
            if msg.follower_id is None:
                continue
            # A follower can hold more than one draft for the same message (the
            # leftover duplicates above). Keeping whichever arrived last let a
            # stale APPROVED row outrank the row that actually sent, so someone
            # already messaged showed up under Approved and vanished from Sent.
            # Keep the furthest-along row instead — what happened outranks what
            # was merely queued.
            current = messages.get(msg.follower_id)
            if current is None or _progress_rank(msg) > _progress_rank(current):
                messages[msg.follower_id] = msg
        for row in db.execute(
            select(LinkedInFollowerSend).where(
                LinkedInFollowerSend.account_id == resolved_account,
                LinkedInFollowerSend.campaign_key == campaign_key,
            )
        ).scalars().all():
            sends[row.follower_provider_id] = row

    followers = list(
        db.execute(
            select(LinkedInFollower)
            .where(LinkedInFollower.account_id == resolved_account)
            .order_by(LinkedInFollower.id)
        ).scalars().all()
    )

    # Followers this message has already settled with: sent, or claimed and
    # awaiting review. Read from the checkpoints already loaded above.
    settled_ids = {
        pid
        for pid, row in sends.items()
        if row.status not in FollowerSendStatus.RETRYABLE
    }

    def _matches(follower: LinkedInFollower) -> bool:
        if not status:
            return True
        msg = messages.get(follower.id)
        if status == "pending":
            return msg is None
        if msg is None or msg.status != status:
            return False
        # A leftover draft for someone already settled can never be sent, and its
        # tab count no longer includes it, so the list must not show it either.
        if status in (LinkedInStatus.DRAFT, LinkedInStatus.APPROVED):
            return follower.provider_id not in settled_ids
        return True

    filtered = [f for f in followers if _matches(f)]
    total = len(filtered)
    window = filtered[offset : offset + limit]

    items: list[FollowerOut] = []
    for follower in window:
        msg = messages.get(follower.id)
        send = sends.get(follower.provider_id)
        items.append(
            FollowerOut(
                id=follower.id,
                account_id=follower.account_id,
                provider_id=follower.provider_id,
                name=follower.name,
                headline=follower.headline,
                profile_url=follower.profile_url,
                picture_url=follower.picture_url,
                message_id=msg.id if msg else None,
                message_status=msg.status if msg else None,
                body=msg.body if msg else None,
                send_status=send.status if send else None,
                reach=send.reach if send else None,
                sent_at=send.sent_at.isoformat() if send and send.sent_at else None,
                error=(send.error if send else None) or (msg.error if msg else None),
                replied_at=msg.replied_at.isoformat() if msg and msg.replied_at else None,
                reply_snippet=msg.reply_snippet if msg else None,
            )
        )
    return Page[FollowerOut](items=items, total=total, limit=limit, offset=offset)


@router.get("/checkpoints")
def list_checkpoints(
    db: Session = Depends(get_db),
    message: Optional[str] = None,
    account_id: Optional[str] = None,
    limit: int = Query(200, le=1000),
):
    """The raw checkpoint rows — the audit trail of who was contacted, when.

    Exposed so "why was this follower skipped today?" is answerable without
    reading the database by hand.
    """
    resolved_account = _resolve_account(account_id)
    query = select(LinkedInFollowerSend).where(
        LinkedInFollowerSend.account_id == resolved_account
    )
    campaign_key = service.campaign_key_for(message)
    if campaign_key:
        query = query.where(LinkedInFollowerSend.campaign_key == campaign_key)
    rows = list(
        db.execute(
            query.order_by(LinkedInFollowerSend.id.desc()).limit(limit)
        ).scalars().all()
    )
    return {
        "account_id": resolved_account,
        "campaign_key": campaign_key or None,
        "items": [
            {
                "id": r.id,
                "follower_provider_id": r.follower_provider_id,
                "campaign_key": r.campaign_key,
                "campaign_goal": r.campaign_goal,
                "message_id": r.message_id,
                "status": r.status,
                "reach": r.reach,
                "attempts": r.attempts,
                "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                "claimed_at": r.claimed_at.isoformat() if r.claimed_at else None,
                "error": r.error,
                # True when a worker died mid-send: the outcome is unknown, so it
                # is never retried automatically.
                "needs_review": r.status == FollowerSendStatus.CLAIMED,
            }
            for r in rows
        ],
    }
