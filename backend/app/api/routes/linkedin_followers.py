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
def followers_status(db: Session = Depends(get_db), message: Optional[str] = None):
    """Connection status + roster/campaign counts for the page header.

    Safe to call with no message and no account: it reports what is missing
    instead of failing, so the page can render its own setup state.
    """
    provider = get_linkedin_provider()
    lister = getattr(provider, "list_accounts", None)
    accounts = lister() if lister else []
    account_id = service.active_account_id()
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
    return service.read_progress()


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
    eligible = service.eligible_followers(
        db, account_id=account_id, campaign_key=campaign_key, limit=limit
    )
    if not eligible:
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
    _resolve_account(payload.account_id)
    campaign_key = _resolve_campaign(payload.message)
    approved = service.approve_all(db, campaign_key=campaign_key)
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
    open_count = int(
        db.execute(
            select(func.count())
            .select_from(LinkedInMessage)
            .where(
                LinkedInMessage.follower_id.is_not(None),
                LinkedInMessage.follower_campaign_key == campaign_key,
                LinkedInMessage.status.in_(
                    [LinkedInStatus.DRAFT, LinkedInStatus.APPROVED]
                ),
            )
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
            if msg.follower_id is not None:
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

    def _matches(follower: LinkedInFollower) -> bool:
        if not status:
            return True
        msg = messages.get(follower.id)
        if status == "pending":
            return msg is None
        return msg is not None and msg.status == status

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
