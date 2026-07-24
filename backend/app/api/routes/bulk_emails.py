"""Bulk email campaigns: chat in a recipient list + a brief, review, send.

The heavy lifting lives in ``services/bulk_email``. These handlers own the
campaign lifecycle and the read models the workspace UI polls. Draft review,
editing, single sends and reply tracking deliberately reuse ``/api/emails``,
because a bulk email is an ordinary EmailDraft.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.bulk_campaign import BulkCampaign
from app.models.company import Company
from app.models.contact import Contact
from app.models.email_draft import EmailDraft
from app.models.enums import BulkCampaignStatus, EmailStatus
from app.schemas.entities import (
    BulkCampaignDetailOut,
    BulkCampaignListOut,
    BulkCampaignOut,
    BulkChatMessageOut,
    BulkRecipientOut,
)
from app.schemas.requests import (
    BulkCampaignCreateRequest,
    BulkCampaignUpdateRequest,
    BulkChatRequest,
    BulkDraftRequest,
    BulkSendRequest,
)
from app.services.bulk_email.chat import handle_message
from app.services.bulk_email.runner import (
    launch_drafting,
    launch_sending,
    recipients_needing_drafts,
)
from app.services.email_providers import list_mailboxes, resolve_mailbox

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bulk-emails", tags=["bulk-emails"])

_BUSY_STATUSES = (BulkCampaignStatus.DRAFTING, BulkCampaignStatus.SENDING)


def _get_campaign(db: Session, campaign_id: int) -> BulkCampaign:
    campaign = db.get(BulkCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Bulk campaign not found")
    return campaign


def _counts(db: Session, campaign_id: int) -> dict:
    """Recipient + per-status email counts for one campaign, in two queries."""
    recipients = db.execute(
        select(func.count())
        .select_from(Contact)
        .where(Contact.bulk_campaign_id == campaign_id)
    ).scalar_one()
    rows = db.execute(
        select(EmailDraft.status, func.count())
        .where(EmailDraft.bulk_campaign_id == campaign_id)
        .group_by(EmailDraft.status)
    ).all()
    by_status = {status: count for status, count in rows}
    return {
        "recipients": recipients,
        "drafted": by_status.get(EmailStatus.DRAFT, 0),
        "approved": by_status.get(EmailStatus.APPROVED, 0)
        + by_status.get(EmailStatus.SCHEDULED, 0),
        "sent": by_status.get(EmailStatus.SENT, 0) + by_status.get(EmailStatus.REPLIED, 0),
        "replied": by_status.get(EmailStatus.REPLIED, 0),
    }


def _campaign_out(db: Session, campaign: BulkCampaign) -> BulkCampaignOut:
    mailbox = resolve_mailbox(campaign.mailbox_id)
    return BulkCampaignOut(
        id=campaign.id,
        name=campaign.name,
        mailbox_id=campaign.mailbox_id,
        mailbox_label=mailbox.label,
        from_email=mailbox.from_email,
        from_name=mailbox.from_name or None,
        status=campaign.status,
        purpose=campaign.purpose,
        signature=campaign.signature,
        progress_total=campaign.progress_total or 0,
        progress_done=campaign.progress_done or 0,
        last_error=campaign.last_error,
        created_at=campaign.created_at,
        **_counts(db, campaign.id),
    )


def _detail_out(db: Session, campaign: BulkCampaign) -> BulkCampaignDetailOut:
    base = _campaign_out(db, campaign)
    return BulkCampaignDetailOut(
        **base.model_dump(),
        messages=[BulkChatMessageOut.model_validate(m) for m in campaign.messages],
        recipients_pending_draft=len(recipients_needing_drafts(db, campaign.id)),
    )


@router.get("", response_model=BulkCampaignListOut)
def get_bulk_campaigns(db: Session = Depends(get_db)):
    """Every bulk campaign, newest first."""
    campaigns = db.execute(
        select(BulkCampaign).order_by(BulkCampaign.id.desc())
    ).scalars().all()
    return BulkCampaignListOut(items=[_campaign_out(db, c) for c in campaigns])


@router.post("", response_model=BulkCampaignDetailOut, status_code=201)
def create_bulk_campaign(
    payload: BulkCampaignCreateRequest, db: Session = Depends(get_db)
):
    """Create a campaign against one of the configured sending mailboxes."""
    if payload.mailbox_id not in {mb.id for mb in list_mailboxes()}:
        raise HTTPException(status_code=400, detail="Unknown sending mailbox")
    campaign = BulkCampaign(
        name=payload.name.strip(),
        mailbox_id=payload.mailbox_id,
        status=BulkCampaignStatus.COLLECTING,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return _detail_out(db, campaign)


@router.get("/{campaign_id}", response_model=BulkCampaignDetailOut)
def get_bulk_campaign(campaign_id: int, db: Session = Depends(get_db)):
    return _detail_out(db, _get_campaign(db, campaign_id))


@router.patch("/{campaign_id}", response_model=BulkCampaignDetailOut)
def update_bulk_campaign(
    campaign_id: int,
    payload: BulkCampaignUpdateRequest,
    db: Session = Depends(get_db),
):
    campaign = _get_campaign(db, campaign_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("name"):
        campaign.name = data["name"].strip()
    if "mailbox_id" in data and data["mailbox_id"]:
        if data["mailbox_id"] not in {mb.id for mb in list_mailboxes()}:
            raise HTTPException(status_code=400, detail="Unknown sending mailbox")
        campaign.mailbox_id = data["mailbox_id"]
        # Keep unsent drafts pointing at the mailbox the user just chose.
        for draft in db.execute(
            select(EmailDraft).where(
                EmailDraft.bulk_campaign_id == campaign_id,
                EmailDraft.status.in_([EmailStatus.DRAFT, EmailStatus.APPROVED]),
            )
        ).scalars().all():
            draft.from_mailbox = campaign.mailbox_id
    if "purpose" in data:
        campaign.purpose = (data["purpose"] or "").strip() or None
    if "signature" in data:
        campaign.signature = (data["signature"] or "").strip() or None
    db.commit()
    db.refresh(campaign)
    return _detail_out(db, campaign)


@router.post("/{campaign_id}/chat", response_model=BulkCampaignDetailOut)
def chat(campaign_id: int, payload: BulkChatRequest, db: Session = Depends(get_db)):
    """Send one message to the campaign assistant (recipients, brief, or both)."""
    campaign = _get_campaign(db, campaign_id)
    message = (payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    handle_message(db, campaign, message)
    db.refresh(campaign)
    return _detail_out(db, campaign)


@router.post("/{campaign_id}/draft", response_model=BulkCampaignDetailOut, status_code=202)
def draft_bulk_emails(
    campaign_id: int,
    payload: BulkDraftRequest,
    db: Session = Depends(get_db),
):
    """Write an email for every recipient who doesn't have one yet."""
    campaign = _get_campaign(db, campaign_id)
    if campaign.status in _BUSY_STATUSES:
        raise HTTPException(
            status_code=409, detail="This campaign is already drafting or sending."
        )
    if not (campaign.purpose or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Tell the assistant what these emails should say first.",
        )
    if not payload.regenerate and not recipients_needing_drafts(db, campaign_id):
        raise HTTPException(
            status_code=400, detail="Every recipient already has an email drafted."
        )
    campaign.status = BulkCampaignStatus.DRAFTING
    campaign.last_error = None
    db.commit()
    launch_drafting(campaign_id, regenerate=payload.regenerate)
    db.refresh(campaign)
    return _detail_out(db, campaign)


@router.post("/{campaign_id}/send", response_model=BulkCampaignDetailOut, status_code=202)
def send_bulk_emails(
    campaign_id: int,
    payload: BulkSendRequest,
    db: Session = Depends(get_db),
):
    """Approve and send the reviewed drafts (all of them, or a chosen subset)."""
    campaign = _get_campaign(db, campaign_id)
    if campaign.status in _BUSY_STATUSES:
        raise HTTPException(
            status_code=409, detail="This campaign is already drafting or sending."
        )
    query = select(func.count()).select_from(EmailDraft).where(
        EmailDraft.bulk_campaign_id == campaign_id,
        EmailDraft.status.in_([EmailStatus.DRAFT, EmailStatus.APPROVED]),
    )
    if payload.draft_ids:
        query = query.where(EmailDraft.id.in_(payload.draft_ids))
    if not db.execute(query).scalar_one():
        raise HTTPException(status_code=400, detail="No drafts are waiting to be sent.")
    campaign.status = BulkCampaignStatus.SENDING
    campaign.last_error = None
    db.commit()
    launch_sending(campaign_id, payload.draft_ids)
    db.refresh(campaign)
    return _detail_out(db, campaign)


@router.post("/{campaign_id}/cancel", response_model=BulkCampaignDetailOut)
def cancel_bulk_job(campaign_id: int, db: Session = Depends(get_db)):
    """Ask the running drafting/sending job to stop after the current email."""
    campaign = _get_campaign(db, campaign_id)
    if campaign.status not in _BUSY_STATUSES:
        raise HTTPException(status_code=400, detail="Nothing is running for this campaign.")
    campaign.cancel_requested = True
    db.commit()
    db.refresh(campaign)
    return _detail_out(db, campaign)


@router.get("/{campaign_id}/recipients", response_model=list[BulkRecipientOut])
def get_bulk_recipients(campaign_id: int, db: Session = Depends(get_db)):
    """Everyone on the list with the state of their email (for the tracker table)."""
    _get_campaign(db, campaign_id)
    contacts = db.execute(
        select(Contact)
        .where(Contact.bulk_campaign_id == campaign_id)
        .order_by(Contact.id)
    ).scalars().all()
    if not contacts:
        return []

    company_ids = {c.company_id for c in contacts if c.company_id}
    companies = {
        c.id: c.name
        for c in (
            db.execute(select(Company).where(Company.id.in_(company_ids))).scalars().all()
            if company_ids
            else []
        )
    }
    latest: dict[int, EmailDraft] = {}
    for draft in db.execute(
        select(EmailDraft)
        .where(EmailDraft.bulk_campaign_id == campaign_id)
        .order_by(EmailDraft.id)
    ).scalars().all():
        if draft.contact_id is not None:
            latest[draft.contact_id] = draft

    out: list[BulkRecipientOut] = []
    for contact in contacts:
        draft = latest.get(contact.id)
        out.append(
            BulkRecipientOut(
                contact_id=contact.id,
                name=contact.name,
                email=contact.email,
                title=contact.title,
                company_name=companies.get(contact.company_id),
                notes=contact.notes,
                draft_id=draft.id if draft else None,
                draft_status=draft.status if draft else None,
                subject=draft.subject if draft else None,
                sent_at=draft.sent_at if draft else None,
                replied_at=draft.replied_at if draft else None,
                reply_snippet=(draft.reply_snippet or draft.reply_body) if draft else None,
                open_count=(draft.open_count or 0) if draft else 0,
            )
        )
    return out


@router.delete("/{campaign_id}/recipients/{contact_id}", status_code=204)
def remove_bulk_recipient(
    campaign_id: int, contact_id: int, db: Session = Depends(get_db)
):
    """Drop someone from the list (and their unsent email)."""
    _get_campaign(db, campaign_id)
    contact = db.get(Contact, contact_id)
    if contact is None or contact.bulk_campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Recipient not found")
    drafts = db.execute(
        select(EmailDraft).where(
            EmailDraft.bulk_campaign_id == campaign_id,
            EmailDraft.contact_id == contact_id,
        )
    ).scalars().all()
    if any(d.status in (EmailStatus.SENT, EmailStatus.REPLIED) for d in drafts):
        raise HTTPException(
            status_code=400, detail="This person was already emailed; keep them for tracking."
        )
    for draft in drafts:
        db.delete(draft)
    db.delete(contact)
    db.commit()


@router.delete("/{campaign_id}", status_code=204)
def delete_bulk_campaign(campaign_id: int, db: Session = Depends(get_db)):
    """Delete a campaign with its recipients, emails, and chat history."""
    campaign = _get_campaign(db, campaign_id)
    if campaign.status in _BUSY_STATUSES:
        raise HTTPException(
            status_code=409, detail="Stop the running job before deleting this campaign."
        )
    for draft in db.execute(
        select(EmailDraft).where(EmailDraft.bulk_campaign_id == campaign_id)
    ).scalars().all():
        db.delete(draft)
    for contact in db.execute(
        select(Contact).where(Contact.bulk_campaign_id == campaign_id)
    ).scalars().all():
        db.delete(contact)
    db.delete(campaign)
    db.commit()
