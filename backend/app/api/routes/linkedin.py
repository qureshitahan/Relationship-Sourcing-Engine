"""LinkedIn outreach: draft generation, approval, sending (invite/DM), replies.

LinkedIn only permits direct messages to 1st-degree connections. On send we
resolve the prospect's profile: if connected we DM immediately (status ``sent``);
otherwise we send a connection invitation (status ``invite_sent``) and the stored
message auto-delivers once the invite is accepted (see ``scan_linkedin_updates``,
driven by the background poller).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal, get_db
from app.models.company import Company
from app.models.contact import Contact
from app.models.discovery_run import DiscoveryRun
from app.models.enums import AuditAction, LinkedInStatus
from app.models.linkedin_message import LinkedInMessage
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.models.suppression import OutreachHistory
from app.schemas.entities import LinkedInInviteStats, LinkedInMessageOut, Page
from app.schemas.requests import (
    LinkedInAccountNameRequest,
    LinkedInConnectRequest,
    LinkedInGenerateRequest,
    LinkedInGenerateRunRequest,
    LinkedInReplyRequest,
    LinkedInSelectAccountRequest,
    LinkedInSendOpenRequest,
    LinkedInStatusRequest,
    LinkedInUpdateRequest,
)
from app.services.app_settings import get_setting, set_setting
from app.services.audit import log_action
from app.services import linkedin_account_names
from app.services.linkedin_outreach import generate_linkedin_content
from app.services.linkedin_providers import (
    ACTIVE_ACCOUNT_SETTING,
    get_linkedin_provider,
    public_identifier_from_url,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/linkedin", tags=["linkedin"])

_OPEN_STATUSES = [LinkedInStatus.DRAFT, LinkedInStatus.APPROVED]


class SendError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _latest_insight(db: Session, principal_id: int, contact_id: int):
    insight = db.execute(
        select(RelevanceInsight)
        .where(
            RelevanceInsight.principal_id == principal_id,
            RelevanceInsight.contact_id == contact_id,
        )
        .order_by(RelevanceInsight.created_at.desc())
    ).scalars().first()
    if insight is not None:
        return insight
    # Cost-friendly reuse: this principal hasn't researched this prospect
    # themselves (e.g. they're drafting via an existing run discovered by a
    # different principal — see generate_run_messages' principal_id override).
    # Reuse whatever research already exists rather than drafting with zero
    # context or spending a fresh Anthropic research call.
    return db.execute(
        select(RelevanceInsight)
        .where(RelevanceInsight.contact_id == contact_id)
        .order_by(RelevanceInsight.created_at.desc())
    ).scalars().first()


def _msg_out(db: Session, msg: LinkedInMessage) -> LinkedInMessageOut:
    contact = db.get(Contact, msg.contact_id) if msg.contact_id else None
    company = db.get(Company, msg.company_id) if msg.company_id else None
    principal = db.get(Principal, msg.principal_id) if msg.principal_id else None
    return LinkedInMessageOut(
        id=msg.id,
        principal_id=msg.principal_id,
        campaign_id=msg.campaign_id,
        company_id=msg.company_id,
        contact_id=msg.contact_id,
        insight_id=msg.insight_id,
        body=msg.body,
        invitation_note=msg.invitation_note,
        status=msg.status,
        provider=msg.provider,
        from_account=msg.from_account,
        network_distance=msg.network_distance,
        connected=bool(msg.connected),
        public_identifier=msg.public_identifier,
        provider_chat_id=msg.provider_chat_id,
        approved_by=msg.approved_by,
        created_at=msg.created_at,
        invitation_sent_at=msg.invitation_sent_at,
        sent_at=msg.sent_at,
        replied_at=msg.replied_at,
        reply_snippet=msg.reply_snippet,
        reply_body=msg.reply_body,
        last_reply_check_at=msg.last_reply_check_at,
        last_status_check_at=msg.last_status_check_at,
        error=msg.error,
        principal_name=principal.name if principal else None,
        contact_name=contact.name if contact else None,
        contact_title=contact.title if contact else None,
        company_name=company.name if company else None,
        linkedin_url=contact.linkedin_url if contact else None,
        discovery_run_id=contact.discovery_run_id if contact else None,
    )


def _active_account_id() -> Optional[str]:
    return get_setting(ACTIVE_ACCOUNT_SETTING) or settings.unipile_account_id or None


@router.get("/account")
def linkedin_account():
    """Report the configured LinkedIn provider + connection health for a banner."""
    provider = get_linkedin_provider()
    configured = settings.linkedin_provider == "unipile" and bool(
        settings.unipile_api_key and settings.unipile_dsn
    )
    return {
        "provider": provider.name,
        "configured": configured or provider.name == "stub",
        "account_id": _active_account_id(),
    }


#: How many by-id account lookups one request may attempt. Bounded so a tenant
#: full of retired accounts can never turn this page into a long series of calls.
_MAX_ACCOUNT_LOOKUPS = 5


def _sending_account_ids(db: Session) -> list[str]:
    """Account ids that appear in our own send history, newest activity first."""
    ids: list[str] = []
    for account_id in db.execute(
        select(LinkedInMessage.from_account)
        .where(LinkedInMessage.from_account.is_not(None))
        .group_by(LinkedInMessage.from_account)
        .order_by(func.max(LinkedInMessage.sent_at).desc())
    ).scalars().all():
        if account_id and account_id not in ids:
            ids.append(account_id)
    return ids


def _resolve_missing_names(db: Session, provider, listed: list[dict]) -> None:
    """Name accounts that sent messages but are absent from the listing.

    An account can send outreach and later drop out of ``list_accounts``, which
    left its rows in a per-account report labelled by raw id. Asking the provider
    for those ids directly fills the names in automatically, so nothing has to be
    entered by hand.

    Only attempted when the listing itself succeeded: an empty listing means the
    provider did not answer, and firing individual lookups at an unresponsive
    provider would just be slower failure.
    """
    if not listed:
        return
    getter = getattr(provider, "get_account", None)
    if getter is None:
        return
    listed_ids = {str(a.get("id")) for a in listed if isinstance(a, dict)}
    known = linkedin_account_names.entries()
    try:
        candidates = [
            account_id
            for account_id in _sending_account_ids(db)
            if account_id not in listed_ids
            and not known.get(account_id, {}).get("name")
            # An account removed from the provider never resolves, so a recent
            # failure means skip it instead of spending a request per page view.
            and linkedin_account_names.should_attempt_lookup(account_id)
        ][:_MAX_ACCOUNT_LOOKUPS]
    except Exception:  # noqa: BLE001 - a naming nicety must not fail the page
        db.rollback()
        logger.warning("could not read sending account ids", exc_info=True)
        return
    found = []
    for account_id in candidates:
        account = getter(account_id)
        if account and account.get("name"):
            found.append(account)
        else:
            linkedin_account_names.mark_lookup_failed(account_id)
    if found:
        linkedin_account_names.remember_provider_names(found)


@router.get("/accounts")
def list_accounts(db: Session = Depends(get_db)):
    """List connected LinkedIn accounts + which one is active (for the picker)."""
    provider = get_linkedin_provider()
    lister = getattr(provider, "list_accounts", None)
    accounts = lister() if lister else []
    # Keep the local name cache warm off a call the UI already makes. The listing
    # is the only place account names exist, and it returns [] on any failure, so
    # remembering names when it does succeed is what lets reports stay readable
    # afterwards. ``accounts`` itself is passed through untouched — the picker
    # keeps showing exactly what the provider reports, nothing inferred.
    linkedin_account_names.remember_provider_names(accounts)
    # Then chase the ones the listing left out, so an account that has since
    # dropped off it still shows a name rather than an id.
    _resolve_missing_names(db, provider, accounts)
    return {
        "provider": provider.name,
        "active_account_id": _active_account_id(),
        # The env default account. Messages sent before per-account stamping have
        # no from_account, so the UI attributes those to this account.
        "default_account_id": (settings.unipile_account_id or None),
        "accounts": accounts,
        # Every name known locally, including ones the provider is not returning
        # right now. Additive: callers that only read ``accounts`` are unaffected.
        "known_names": linkedin_account_names.entries(),
    }


@router.put("/account-names")
def set_account_name(payload: LinkedInAccountNameRequest):
    """Label a sending account by hand, for reports that name people not ids.

    Needed because the provider listing is the only source of names and it can
    come back empty; a typed label also outlives it, and outranks it on the next
    sync. Sending an empty name clears the label, letting the provider's own name
    take over again.
    """
    try:
        entries = linkedin_account_names.set_manual_name(payload.account_id, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"known_names": entries}


@router.post("/connect-link")
def create_connect_link(payload: LinkedInConnectRequest, db: Session = Depends(get_db)):
    """Create a Unipile hosted-auth link to connect a LinkedIn account from the UI."""
    provider = get_linkedin_provider()
    maker = getattr(provider, "create_hosted_auth_link", None)
    if maker is None:
        raise HTTPException(
            status_code=400,
            detail="Connecting accounts requires the Unipile provider (LINKEDIN_PROVIDER=unipile).",
        )
    base = (settings.app_public_url or "").strip().rstrip("/")
    success_url = f"{base}/linkedin?connected=1" if base else None
    url, error = maker(
        name=payload.name or "rse-user",
        success_redirect_url=success_url,
        failure_redirect_url=success_url,
    )
    if not url:
        raise HTTPException(status_code=502, detail=error or "Could not create link")
    return {"url": url}


@router.post("/select-account")
def select_account(payload: LinkedInSelectAccountRequest):
    """Set the active connected LinkedIn account used for sending."""
    account_id = (payload.account_id or "").strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id is required")
    set_setting(ACTIVE_ACCOUNT_SETTING, account_id)
    return {"active_account_id": account_id}


@router.get("", response_model=Page[LinkedInMessageOut])
def list_messages(
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    contact_id: Optional[int] = None,
    principal_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    discovery_run_id: Optional[int] = None,
    limit: int = Query(50, le=1000),
    offset: int = 0,
):
    # Prospect-driven messages only. Follower DMs live in the same table but are
    # owned by the Followers module and have their own page, tabs and counts —
    # excluding them here is what keeps this list byte-for-byte what it was.
    query = select(LinkedInMessage).where(LinkedInMessage.follower_id.is_(None))
    count_query = (
        select(func.count())
        .select_from(LinkedInMessage)
        .where(LinkedInMessage.follower_id.is_(None))
    )
    if discovery_run_id is not None:
        run_filter = Contact.discovery_run_id == discovery_run_id
        query = query.join(Contact, LinkedInMessage.contact_id == Contact.id).where(run_filter)
        count_query = (
            select(func.count())
            .select_from(LinkedInMessage)
            .join(Contact, LinkedInMessage.contact_id == Contact.id)
            .where(run_filter)
        )
    if status:
        query = query.where(LinkedInMessage.status == status)
        count_query = count_query.where(LinkedInMessage.status == status)
    if contact_id is not None:
        query = query.where(LinkedInMessage.contact_id == contact_id)
        count_query = count_query.where(LinkedInMessage.contact_id == contact_id)
    if principal_id is not None:
        query = query.where(LinkedInMessage.principal_id == principal_id)
        count_query = count_query.where(LinkedInMessage.principal_id == principal_id)
    if campaign_id is not None:
        query = query.where(LinkedInMessage.campaign_id == campaign_id)
        count_query = count_query.where(LinkedInMessage.campaign_id == campaign_id)
    query = query.order_by(LinkedInMessage.created_at.desc()).limit(limit).offset(offset)
    items = db.execute(query).scalars().all()
    total = db.execute(count_query).scalar_one()
    return Page[LinkedInMessageOut](
        items=[_msg_out(db, m) for m in items], total=total, limit=limit, offset=offset
    )


@router.get("/stats", response_model=LinkedInInviteStats)
def invite_stats(
    db: Session = Depends(get_db),
    contact_id: Optional[int] = None,
    principal_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    discovery_run_id: Optional[int] = None,
):
    """How many connection invitations went out, and how many were accepted.

    Acceptance has no flag of its own. An accepted invite is one whose queued
    message was delivered afterwards (``sent_at``), plus the case where the
    profile came back 1st-degree but the auto-DM itself failed (``connected``).
    Requiring ``invitation_sent_at`` keeps direct DMs to existing connections —
    which never needed an invitation — out of both numbers.
    """
    invited = LinkedInMessage.invitation_sent_at.is_not(None)
    accepted = and_(
        invited,
        or_(LinkedInMessage.sent_at.is_not(None), LinkedInMessage.connected.is_(True)),
    )

    base = select(func.count()).select_from(LinkedInMessage)
    if discovery_run_id is not None:
        base = base.join(Contact, LinkedInMessage.contact_id == Contact.id).where(
            Contact.discovery_run_id == discovery_run_id
        )
    if contact_id is not None:
        base = base.where(LinkedInMessage.contact_id == contact_id)
    if principal_id is not None:
        base = base.where(LinkedInMessage.principal_id == principal_id)
    if campaign_id is not None:
        base = base.where(LinkedInMessage.campaign_id == campaign_id)

    sent = db.execute(base.where(invited)).scalar_one()
    approved = db.execute(base.where(accepted)).scalar_one()
    return LinkedInInviteStats(
        invites_sent=sent,
        invites_accepted=approved,
        invites_pending=max(0, sent - approved),
        acceptance_rate=round(approved / sent * 100, 1) if sent else 0.0,
    )


def _existing_open_message(db: Session, principal_id: int, contact_id: int):
    return db.execute(
        select(LinkedInMessage)
        .where(
            LinkedInMessage.principal_id == principal_id,
            LinkedInMessage.contact_id == contact_id,
            LinkedInMessage.status.in_(_OPEN_STATUSES),
        )
        .order_by(LinkedInMessage.created_at.desc())
    ).scalars().first()


@router.post("/generate", response_model=LinkedInMessageOut)
def generate_message(payload: LinkedInGenerateRequest, db: Session = Depends(get_db)):
    principal = db.get(Principal, payload.principal_id)
    contact = db.get(Contact, payload.contact_id)
    if not principal or not contact:
        raise HTTPException(status_code=404, detail="Principal or prospect not found")
    if not public_identifier_from_url(contact.linkedin_url or ""):
        raise HTTPException(
            status_code=400,
            detail="Prospect has no personal LinkedIn profile URL (a /in/ link). "
            "Company pages can't be messaged.",
        )
    if contact.do_not_contact:
        raise HTTPException(status_code=400, detail="Prospect is on do-not-contact list")

    if not payload.regenerate:
        existing = _existing_open_message(db, principal.id, contact.id)
        if existing is not None:
            return _msg_out(db, existing)

    company = db.get(Company, contact.company_id) if contact.company_id else None
    insight = _latest_insight(db, principal.id, contact.id)
    content = generate_linkedin_content(
        db, principal, contact, company, insight, outreach_goal=payload.outreach_goal
    )
    msg = LinkedInMessage(
        principal_id=principal.id,
        # Inherit the campaign from the prospect. The column and its filters have
        # always existed here but nothing ever wrote to them, so every LinkedIn
        # message reported as "No campaign" and per-campaign LinkedIn performance
        # was impossible to see. The prospect is the right source: a contact row
        # belongs to exactly one campaign (see models/contact.py), so there is no
        # ambiguity and no new data to record. Left NULL when the prospect has no
        # campaign — a pasted or manually added person — rather than invented.
        campaign_id=contact.campaign_id,
        company_id=contact.company_id,
        contact_id=contact.id,
        insight_id=insight.id if insight else None,
        body=content.body,
        invitation_note=content.invitation_note,
        status=LinkedInStatus.DRAFT,
    )
    db.add(msg)
    log_action(
        db,
        AuditAction.LINKEDIN_DRAFT,
        entity_type="linkedin_message",
        summary=f"Drafted LinkedIn message for principal {principal.id} -> prospect {contact.id}",
    )
    db.commit()
    db.refresh(msg)
    return _msg_out(db, msg)


@router.post("/generate-run")
def generate_run_messages(payload: LinkedInGenerateRunRequest, db: Session = Depends(get_db)):
    """Draft LinkedIn messages for approved prospects (with a LinkedIn URL) in a run."""
    run = db.get(DiscoveryRun, payload.discovery_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    principal_id = payload.principal_id or run.principal_id
    if principal_id is None:
        raise HTTPException(status_code=400, detail="No principal for this run")
    principal = db.get(Principal, principal_id)
    if principal is None:
        raise HTTPException(status_code=404, detail="Principal not found")

    approved = list(
        db.execute(
            select(Contact)
            .where(
                Contact.discovery_run_id == payload.discovery_run_id,
                Contact.approved_for_outreach.is_(True),
            )
            .order_by(Contact.id)
        ).scalars().all()
    )
    generated = 0
    skipped = 0
    errors: list[str] = []
    for contact in approved:
        # Only personal /in/ profiles can be messaged; skip company pages / blanks.
        if not public_identifier_from_url(contact.linkedin_url or "") or contact.do_not_contact:
            skipped += 1
            continue
        if _existing_open_message(db, principal.id, contact.id) is not None:
            skipped += 1
            continue
        try:
            company = db.get(Company, contact.company_id) if contact.company_id else None
            insight = _latest_insight(db, principal.id, contact.id)
            content = generate_linkedin_content(
                db, principal, contact, company, insight,
                outreach_goal=payload.outreach_goal,
            )
            db.add(
                LinkedInMessage(
                    principal_id=principal.id,
                    # Same rule as the single-draft path above: the campaign comes
                    # from the prospect, so bulk-generated messages are reportable
                    # per campaign too.
                    campaign_id=contact.campaign_id,
                    company_id=contact.company_id,
                    contact_id=contact.id,
                    insight_id=insight.id if insight else None,
                    body=content.body,
                    invitation_note=content.invitation_note,
                    status=LinkedInStatus.DRAFT,
                )
            )
            generated += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{contact.name or contact.id}: {exc}")
    if generated:
        log_action(
            db,
            AuditAction.LINKEDIN_DRAFT,
            entity_type="discovery_run",
            summary=f"Generated {generated} LinkedIn message(s) for run {payload.discovery_run_id}",
        )
        db.commit()
    return {
        "discovery_run_id": payload.discovery_run_id,
        "candidates": len(approved),
        "generated": generated,
        "skipped": skipped,
        "errors": errors[:10],
    }


@router.patch("/{message_id}", response_model=LinkedInMessageOut)
def update_message(
    message_id: int, payload: LinkedInUpdateRequest, db: Session = Depends(get_db)
):
    msg = db.get(LinkedInMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.status in (LinkedInStatus.SENT, LinkedInStatus.REPLIED, LinkedInStatus.INVITE_SENT):
        raise HTTPException(status_code=400, detail="Cannot edit a sent/invited message")
    if payload.body is not None:
        msg.body = payload.body
    if payload.invitation_note is not None:
        msg.invitation_note = payload.invitation_note[:300]
    db.commit()
    db.refresh(msg)
    return _msg_out(db, msg)


@router.post("/{message_id}/status", response_model=LinkedInMessageOut)
def set_status(
    message_id: int, payload: LinkedInStatusRequest, db: Session = Depends(get_db)
):
    msg = db.get(LinkedInMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    msg.status = payload.status
    if payload.status == LinkedInStatus.APPROVED:
        msg.approved_by = payload.approved_by
        msg.approved_at = datetime.utcnow()
    log_action(
        db,
        AuditAction.LINKEDIN_APPROVAL,
        entity_type="linkedin_message",
        entity_id=msg.id,
        actor=payload.approved_by or "user",
        summary=f"LinkedIn status -> {payload.status}",
    )
    db.commit()
    db.refresh(msg)
    return _msg_out(db, msg)


@router.delete("/{message_id}", status_code=204)
def delete_message(message_id: int, db: Session = Depends(get_db)):
    msg = db.get(LinkedInMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.status in (LinkedInStatus.SENT, LinkedInStatus.REPLIED, LinkedInStatus.INVITE_SENT):
        raise HTTPException(status_code=400, detail="Cannot delete a sent/invited message")
    db.delete(msg)
    db.commit()


def perform_linkedin_send(
    db: Session, msg: LinkedInMessage, *, account_id: Optional[str] = None
) -> LinkedInMessage:
    """Send a message: DM if connected, else a connection invitation.

    Shared by the manual /send endpoint. ``account_id`` pins which connected
    LinkedIn account sends (used by the daily automation to alternate Dalbir /
    Farah); omitted => the active account, i.e. exactly today's behaviour.
    Commits on success; raises SendError.
    """
    if msg.status not in (LinkedInStatus.APPROVED, LinkedInStatus.INVITE_SENT):
        raise SendError("Message must be APPROVED before sending")
    contact = db.get(Contact, msg.contact_id) if msg.contact_id else None
    if not contact or not (contact.linkedin_url or "").strip():
        raise SendError("Prospect has no LinkedIn URL")
    if not public_identifier_from_url(contact.linkedin_url or ""):
        raise SendError(
            "This prospect's LinkedIn URL is a company/invalid page, not a personal "
            "profile — can't message on LinkedIn."
        )
    if contact.do_not_contact:
        raise SendError("Prospect is on do-not-contact list")

    provider = get_linkedin_provider(account_id or None)
    profile = provider.resolve_profile(contact.linkedin_url)
    if not profile.found or not profile.provider_id:
        raise SendError(profile.error or "Could not resolve LinkedIn profile", status_code=502)

    msg.provider = provider.name
    # Record which of our accounts is sending, so reply/acceptance tracking later
    # polls with the SAME account even if the active account is switched.
    msg.from_account = getattr(provider, "account_id", None) or None
    msg.linkedin_provider_id = profile.provider_id
    msg.public_identifier = profile.public_identifier
    msg.network_distance = profile.network_distance
    msg.connected = profile.is_connected

    if profile.is_connected:
        result = provider.send_message(provider_id=profile.provider_id, text=msg.body)
        if not result.sent:
            msg.error = result.error
            db.commit()
            raise SendError(result.error or "LinkedIn send failed", status_code=502)
        msg.provider_chat_id = result.chat_id
        msg.provider_message_id = result.message_id
        msg.status = LinkedInStatus.SENT
        msg.sent_at = datetime.utcnow()
        msg.error = None
        action, detail = AuditAction.LINKEDIN_SEND, "Sent LinkedIn DM"
    else:
        note = (msg.invitation_note or msg.body or "")[: settings.linkedin_invite_note_max_chars]
        invite = provider.send_invitation(provider_id=profile.provider_id, note=note)
        if invite.already_connected:
            # Race: they're actually connected — DM instead.
            result = provider.send_message(provider_id=profile.provider_id, text=msg.body)
            if not result.sent:
                msg.error = result.error
                db.commit()
                raise SendError(result.error or "LinkedIn send failed", status_code=502)
            msg.connected = True
            msg.provider_chat_id = result.chat_id
            msg.provider_message_id = result.message_id
            msg.status = LinkedInStatus.SENT
            msg.sent_at = datetime.utcnow()
            msg.error = None
            action, detail = AuditAction.LINKEDIN_SEND, "Sent LinkedIn DM (already connected)"
        elif not invite.sent:
            msg.error = invite.error
            db.commit()
            raise SendError(invite.error or "LinkedIn invitation failed", status_code=502)
        else:
            msg.provider_invitation_id = invite.invitation_id
            msg.status = LinkedInStatus.INVITE_SENT
            msg.invitation_sent_at = datetime.utcnow()
            msg.error = None
            action, detail = AuditAction.LINKEDIN_INVITE, "Sent LinkedIn connection invitation"

    db.add(
        OutreachHistory(
            company_id=msg.company_id,
            contact_id=msg.contact_id,
            channel="linkedin",
            detail=f"{detail} via {provider.name}",
        )
    )
    log_action(
        db,
        action,
        entity_type="linkedin_message",
        entity_id=msg.id,
        summary=f"{detail} to prospect {msg.contact_id}",
    )
    db.commit()
    db.refresh(msg)
    return msg


@router.post("/{message_id}/send", response_model=LinkedInMessageOut)
def send_message(message_id: int, db: Session = Depends(get_db)):
    msg = db.get(LinkedInMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    try:
        perform_linkedin_send(db, msg)
    except SendError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return _msg_out(db, msg)


@router.post("/send-open")
def send_open(payload: LinkedInSendOpenRequest, db: Session = Depends(get_db)):
    """Approve + send ALL open (draft/approved) LinkedIn messages, in the background.

    One click instead of approving each message by hand. Optional
    ``discovery_run_id`` scopes to a single run; omitted = every run. Sends from
    the currently-active account, paced (``bulk_linkedin_send_delay_seconds``) to
    protect the account, and never exceeds that account's ``linkedin_daily_send_cap``
    for today — the overflow is reported as ``held`` and can be sent tomorrow.
    ``sent_today`` is likewise that account's own total. Returns the counts
    immediately; poll the message list to watch them move.
    """
    from app.services.discovery_jobs import launch_linkedin_message_send
    from app.services.linkedin_budget import active_send_account_id, linkedin_sent_today

    query = select(LinkedInMessage).where(
        LinkedInMessage.status.in_([LinkedInStatus.DRAFT, LinkedInStatus.APPROVED]),
        # Never pick up follower DMs. They are sent by the Followers module, which
        # DMs only and checkpoints every send; routing one through here would send
        # a connection invitation instead and bypass that checkpoint entirely.
        LinkedInMessage.follower_id.is_(None),
    )
    if payload.discovery_run_id is not None:
        query = query.join(Contact, LinkedInMessage.contact_id == Contact.id).where(
            Contact.discovery_run_id == payload.discovery_run_id
        )
    messages = list(db.execute(query.order_by(LinkedInMessage.id)).scalars().all())
    matched = len(messages)

    # Daily-cap guard (invites + DMs) for the account that will do the sending —
    # LinkedIn limits per account, so a shared global budget let sends from one
    # account block every other one. All send paths still share that account's
    # budget. Only the ids that fit today are queued.
    sending_account = active_send_account_id()
    sent_today = linkedin_sent_today(db, sending_account)
    cap = max(0, int(settings.linkedin_daily_send_cap))
    remaining = max(0, cap - sent_today)
    will_send = [m.id for m in messages][:remaining]
    held = matched - len(will_send)

    launch_linkedin_message_send(will_send)
    return {
        "matched": matched,
        "queued": len(will_send),
        "held": held,
        "cap": cap,
        "sent_today": sent_today,
    }


@router.get("/send-progress")
def send_progress():
    """Live state of the bulk approve+send job, so the UI can show progress and
    offer Stop while it is running."""
    from app.services import linkedin_send_progress as progress

    return progress.read_progress()


@router.post("/stop-send")
def stop_send(db: Session = Depends(get_db)):
    """Halt the running bulk approve+send.

    The worker checks between messages, so the one already in flight completes
    and everything after it is left untouched — nothing is half-sent. Messages
    that never went out keep their draft/approved status and can be sent later.
    """
    from app.services import linkedin_send_progress as progress

    if not progress.request_stop():
        return {"stopped": False, "message": "No LinkedIn send is running."}
    log_action(
        db,
        AuditAction.LINKEDIN_SEND,
        entity_type="linkedin_bulk_send",
        actor="human",
        summary="Stopped the bulk LinkedIn send",
        commit=True,
    )
    return {
        "stopped": True,
        "message": "Stopping — the message in flight finishes, then sending halts.",
    }


@router.post("/{message_id}/reply", response_model=LinkedInMessageOut)
def reply_in_thread(
    message_id: int, payload: LinkedInReplyRequest, db: Session = Depends(get_db)
):
    """Send a follow-up message in an existing LinkedIn chat."""
    msg = db.get(LinkedInMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if not msg.provider_chat_id:
        raise HTTPException(status_code=400, detail="No LinkedIn chat to reply in yet")
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Reply body is required")
    # Reply from the SAME account that sent the thread (Dalbir/Farah), not just
    # whichever is globally active. Legacy rows (no from_account) use the active
    # default, so single-account behaviour is unchanged.
    provider = get_linkedin_provider(msg.from_account or None)
    sender = getattr(provider, "send_message_in_chat", None)
    if sender is None:
        raise HTTPException(status_code=400, detail="Provider does not support replies")
    result = sender(chat_id=msg.provider_chat_id, text=body)
    if not result.sent:
        raise HTTPException(status_code=502, detail=result.error or "Reply failed")
    log_action(
        db,
        AuditAction.LINKEDIN_SEND,
        entity_type="linkedin_message",
        entity_id=msg.id,
        summary=f"Replied in LinkedIn chat to prospect {msg.contact_id}",
    )
    db.commit()
    db.refresh(msg)
    return _msg_out(db, msg)


SCAN_PROGRESS_KEY = "linkedin_scan_progress"


def _set_scan_progress(
    db: Session, *, status: str, total: int, done: int, accepted: int, replied: int
) -> None:
    """Persist scan progress (and flush pending message changes) so the UI can
    poll a live progress bar. Committing here doubles as the per-message commit,
    so a long scan saves its work incrementally instead of all-or-nothing."""
    from app.models.app_setting import AppSetting

    payload = json.dumps(
        {
            "status": status,
            "total": total,
            "done": done,
            "accepted": accepted,
            "replied": replied,
        }
    )
    row = db.get(AppSetting, SCAN_PROGRESS_KEY)
    if row is None:
        db.add(AppSetting(key=SCAN_PROGRESS_KEY, value=payload))
    else:
        row.value = payload
    db.commit()


def scan_linkedin_updates(db: Session, *, track_progress: bool = False) -> dict:
    """Poll: auto-DM accepted invites, and detect replies to sent messages.

    Shared by the /check-updates route and the background poller. Degrades
    gracefully when the provider does not support tracking (e.g. stub).

    ``track_progress`` (manual check-replies button): writes a live progress
    record after each message and commits incrementally. Left False (the 15-min
    poller), the behaviour is unchanged — a single commit at the end, no progress.
    """
    default_provider = get_linkedin_provider()
    if not default_provider.supports_tracking():
        if track_progress:
            _set_scan_progress(db, status="done", total=0, done=0, accepted=0, replied=0)
        return {"supported": False, "accepted": 0, "replied": 0}

    # Poll each message with the account that SENT it (msg.from_account). Legacy
    # rows with no stamped account fall back to the active/default provider, so
    # single-account behaviour is byte-for-byte unchanged. Providers are cached
    # per account to avoid rebuilding one per message.
    _provider_cache: dict = {}

    def provider_for(msg) -> object:
        acct = (getattr(msg, "from_account", None) or "").strip()
        if not acct:
            return default_provider
        cached = _provider_cache.get(acct)
        if cached is None:
            cached = get_linkedin_provider(account_id=acct)
            _provider_cache[acct] = cached
        return cached

    now = datetime.utcnow()
    accepted = 0
    replied = 0
    # Accounts whose provider call just failed to connect (not merely "not
    # found") — skip their remaining messages this pass instead of hammering an
    # unreachable endpoint once per message, which previously flooded the log
    # and delayed the scan by one failed network round-trip per message.
    unreachable_accounts: set[str] = set()

    def _account_key(msg) -> str:
        return (getattr(msg, "from_account", None) or "default").strip() or "default"

    pending = db.execute(
        select(LinkedInMessage).where(LinkedInMessage.status == LinkedInStatus.INVITE_SENT)
    ).scalars().all()
    sent_all = db.execute(
        select(LinkedInMessage).where(LinkedInMessage.status == LinkedInStatus.SENT)
    ).scalars().all()
    sent = [m for m in sent_all if m.provider_chat_id]  # only these are pollable
    total = len(pending) + len(sent)
    done = 0
    if track_progress:
        _set_scan_progress(
            db, status="running", total=total, done=0, accepted=0, replied=0
        )

    # 1) Invitations awaiting acceptance -> auto-send the queued message.
    for msg in pending:
        account_key = _account_key(msg)
        if account_key in unreachable_accounts:
            done += 1
            continue
        provider = provider_for(msg)
        msg.last_status_check_at = now
        identifier = msg.public_identifier or msg.linkedin_provider_id
        if identifier:
            profile = provider.resolve_profile(identifier)
            if profile.network_error:
                logger.warning(
                    "LinkedIn account %r unreachable, skipping its remaining "
                    "messages this pass: %s",
                    account_key, profile.error,
                )
                unreachable_accounts.add(account_key)
                done += 1
                continue
            if profile.found:
                msg.network_distance = profile.network_distance
                if profile.is_connected:
                    msg.connected = True
                    result = provider.send_message(
                        provider_id=profile.provider_id or msg.linkedin_provider_id,
                        text=msg.body,
                    )
                    if result.sent:
                        msg.provider_chat_id = result.chat_id
                        msg.provider_message_id = result.message_id
                        msg.status = LinkedInStatus.SENT
                        msg.sent_at = now
                        msg.error = None
                        accepted += 1
                        log_action(
                            db,
                            AuditAction.LINKEDIN_SEND,
                            entity_type="linkedin_message",
                            entity_id=msg.id,
                            summary=f"Auto-sent LinkedIn DM after invite accepted (prospect {msg.contact_id})",
                        )
                    else:
                        msg.error = result.error
        done += 1
        if track_progress:
            _set_scan_progress(
                db, status="running", total=total, done=done,
                accepted=accepted, replied=replied,
            )

    # 2) Sent messages -> detect a reply.
    for msg in sent:
        account_key = _account_key(msg)
        if account_key in unreachable_accounts:
            done += 1
            continue
        provider = provider_for(msg)
        msg.last_reply_check_at = now
        result = provider.check_reply(
            chat_id=msg.provider_chat_id,
            provider_id=msg.linkedin_provider_id or "",
            since=msg.sent_at or msg.created_at,
        )
        if result.network_error:
            logger.warning(
                "LinkedIn account %r unreachable, skipping its remaining "
                "messages this pass: %s",
                account_key, result.error,
            )
            unreachable_accounts.add(account_key)
            done += 1
            continue
        if result.found:
            msg.status = LinkedInStatus.REPLIED
            msg.replied_at = result.received_at or now
            msg.reply_snippet = result.snippet
            msg.reply_body = result.body or result.snippet
            replied += 1
            contact = db.get(Contact, msg.contact_id) if msg.contact_id else None
            if contact and contact.status not in ("connected", "closed"):
                contact.status = "connected"
            log_action(
                db,
                AuditAction.LINKEDIN_APPROVAL,
                entity_type="linkedin_message",
                entity_id=msg.id,
                summary=f"Detected LinkedIn reply from prospect {msg.contact_id}",
            )
        done += 1
        if track_progress:
            _set_scan_progress(
                db, status="running", total=total, done=done,
                accepted=accepted, replied=replied,
            )

    if track_progress:
        _set_scan_progress(
            db, status="done", total=total, done=done,
            accepted=accepted, replied=replied,
        )
    else:
        db.commit()
    return {"supported": True, "accepted": accepted, "replied": replied}


# Only one scan at a time: repeated clicks (or an overlap with the 15-min poller)
# must not stack concurrent Unipile polling / DB writes.
_scan_lock = threading.Lock()


def _scan_worker() -> None:
    if not _scan_lock.acquire(blocking=False):
        logger.info("LinkedIn scan already running; skipping duplicate trigger")
        return
    try:
        db = SessionLocal()
        try:
            scan_linkedin_updates(db, track_progress=True)
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - never let the background scan crash silently
        logger.exception("Background LinkedIn scan failed")
    finally:
        _scan_lock.release()


def launch_linkedin_scan() -> None:
    threading.Thread(target=_scan_worker, name="linkedin-scan", daemon=True).start()


@router.post("/check-updates")
def check_updates():
    """Kick off a poll for accepted invitations and new replies, in the background.

    Polling hits Unipile once per pending message; with many messages that runs
    for minutes, so it must NOT block the HTTP request (the browser and Azure's
    gateway both time out well before it finishes). We start it on a daemon
    thread and return immediately; results land as messages update, and the
    15-minute background poller runs the same scan on its own schedule.
    """
    provider = get_linkedin_provider()
    if not provider.supports_tracking():
        return {
            "started": False,
            "supported": False,
            "message": "LinkedIn tracking is not configured (stub provider).",
        }
    # Reset progress synchronously so the UI shows a fresh bar (not a stale 'done'
    # from a previous run) the instant it starts polling scan-progress.
    set_setting(
        SCAN_PROGRESS_KEY,
        json.dumps({"status": "starting", "total": 0, "done": 0, "accepted": 0, "replied": 0}),
    )
    launch_linkedin_scan()
    return {
        "started": True,
        "supported": True,
        "message": (
            "Checking LinkedIn in the background — accepted invites and new replies "
            "will appear here shortly (this can take a minute or two with many messages)."
        ),
    }


@router.get("/scan-progress")
def scan_progress():
    """Live progress of the most recent manual reply-check, for a progress bar."""
    raw = get_setting(SCAN_PROGRESS_KEY)
    if raw:
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            pass
    return {"status": "idle", "total": 0, "done": 0, "accepted": 0, "replied": 0}
