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
from app.models.bulk_campaign import BulkCampaign, BulkLookup
from app.models.company import Company
from app.models.contact import Contact
from app.models.email_draft import EmailDraft
from app.models.enums import BulkCampaignStatus, BulkLookupStatus, EmailStatus
from app.schemas.entities import (
    BulkCampaignDetailOut,
    BulkCampaignListOut,
    BulkCampaignOut,
    BulkChatMessageOut,
    BulkLookupOut,
    BulkRecipientOut,
)
from app.schemas.requests import (
    BulkCampaignCreateRequest,
    BulkCampaignUpdateRequest,
    BulkChatRequest,
    BulkDraftRequest,
    BulkLookupDecisionRequest,
    BulkLookupEmailRequest,
    BulkLookupRequest,
    BulkSendRequest,
)
from app.services.bulk_email.chat import handle_message
from app.services.bulk_email.runner import (
    launch_drafting,
    launch_lookup,
    launch_sending,
    lookups_pending,
    recipients_needing_drafts,
)
from app.services.email_providers import list_mailboxes, resolve_mailbox

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bulk-emails", tags=["bulk-emails"])

_BUSY_STATUSES = (
    BulkCampaignStatus.DRAFTING,
    BulkCampaignStatus.SENDING,
    BulkCampaignStatus.LOOKING_UP,
)
# Lookups the user has not settled: they still cannot be emailed.
_UNRESOLVED_LOOKUPS = (
    BulkLookupStatus.PENDING,
    BulkLookupStatus.FOUND,
    BulkLookupStatus.NOT_FOUND,
    BulkLookupStatus.AMBIGUOUS,
    BulkLookupStatus.ERROR,
)


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
    lookup_rows = db.execute(
        select(BulkLookup.status, func.count())
        .where(BulkLookup.campaign_id == campaign_id)
        .group_by(BulkLookup.status)
    ).all()
    lookups = {status: count for status, count in lookup_rows}
    return {
        "recipients": recipients,
        "drafted": by_status.get(EmailStatus.DRAFT, 0),
        "approved": by_status.get(EmailStatus.APPROVED, 0)
        + by_status.get(EmailStatus.SCHEDULED, 0),
        "sent": by_status.get(EmailStatus.SENT, 0) + by_status.get(EmailStatus.REPLIED, 0),
        "replied": by_status.get(EmailStatus.REPLIED, 0),
        "lookup_pending": lookups.get(BulkLookupStatus.PENDING, 0),
        "lookup_found": lookups.get(BulkLookupStatus.FOUND, 0),
        "needs_email": sum(lookups.get(status, 0) for status in _UNRESOLVED_LOOKUPS),
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


@router.post("/{campaign_id}/lookup", response_model=BulkCampaignDetailOut, status_code=202)
def start_bulk_lookup(
    campaign_id: int,
    payload: BulkLookupRequest,
    db: Session = Depends(get_db),
):
    """Search the web, then Apollo, for the addresses this list is missing.

    Results are proposals: they are shown with their evidence and only reach a
    recipient once the user accepts them.
    """
    campaign = _get_campaign(db, campaign_id)
    if campaign.status in _BUSY_STATUSES:
        raise HTTPException(
            status_code=409, detail="This campaign is already running a job."
        )
    if not lookups_pending(db, campaign_id, retry_failed=payload.retry_failed):
        raise HTTPException(
            status_code=400, detail="There is nobody left to look up on this list."
        )
    campaign.status = BulkCampaignStatus.LOOKING_UP
    campaign.last_error = None
    db.commit()
    launch_lookup(campaign_id, retry_failed=payload.retry_failed)
    db.refresh(campaign)
    return _detail_out(db, campaign)


@router.get("/{campaign_id}/lookups", response_model=list[BulkLookupOut])
def get_bulk_lookups(campaign_id: int, db: Session = Depends(get_db)):
    """The review queue: everyone pasted without an address, and what we found."""
    _get_campaign(db, campaign_id)
    lookups = db.execute(
        select(BulkLookup)
        .where(BulkLookup.campaign_id == campaign_id)
        .order_by(BulkLookup.id)
    ).scalars().all()
    if not lookups:
        return []

    contacts = {
        c.id: c
        for c in db.execute(
            select(Contact).where(Contact.id.in_([l.contact_id for l in lookups]))
        ).scalars().all()
    }
    company_ids = {c.company_id for c in contacts.values() if c.company_id}
    companies = {
        c.id: c.name
        for c in (
            db.execute(select(Company).where(Company.id.in_(company_ids))).scalars().all()
            if company_ids
            else []
        )
    }
    out: list[BulkLookupOut] = []
    for lookup in lookups:
        contact = contacts.get(lookup.contact_id)
        out.append(
            BulkLookupOut(
                id=lookup.id,
                contact_id=lookup.contact_id,
                status=lookup.status,
                name=contact.name if contact else (lookup.resolved_name or "Unknown"),
                source_text=lookup.source_text,
                title=contact.title if contact else None,
                company_name=companies.get(contact.company_id) if contact else None,
                resolved_name=lookup.resolved_name,
                resolved_title=lookup.resolved_title,
                resolved_org=lookup.resolved_org,
                resolved_domain=lookup.resolved_domain,
                linkedin_url=lookup.linkedin_url,
                location=lookup.location,
                confidence=lookup.confidence,
                reason=lookup.reason,
                evidence=lookup.evidence,
                email=lookup.email,
                email_status=lookup.email_status,
                manual=lookup.manual,
                error=lookup.error,
                created_at=lookup.created_at,
            )
        )
    return out


@router.post("/{campaign_id}/lookups/accept", response_model=BulkCampaignDetailOut)
def accept_bulk_lookups(
    campaign_id: int,
    payload: BulkLookupDecisionRequest,
    db: Session = Depends(get_db),
):
    """Take these proposed addresses: their people become sendable recipients."""
    campaign = _get_campaign(db, campaign_id)
    accepted = 0
    for lookup in _lookups_by_id(db, campaign_id, payload.lookup_ids):
        if not lookup.email:
            continue
        contact = db.get(Contact, lookup.contact_id)
        if contact is None:
            continue
        _adopt_lookup(db, contact, lookup)
        accepted += 1
    if not accepted:
        raise HTTPException(status_code=400, detail="None of those have an address yet.")
    db.commit()
    db.refresh(campaign)
    return _detail_out(db, campaign)


@router.post("/{campaign_id}/lookups/reject", response_model=BulkCampaignDetailOut)
def reject_bulk_lookups(
    campaign_id: int,
    payload: BulkLookupDecisionRequest,
    db: Session = Depends(get_db),
):
    """Dismiss these people: they stay on the list but are never emailed."""
    campaign = _get_campaign(db, campaign_id)
    for lookup in _lookups_by_id(db, campaign_id, payload.lookup_ids):
        lookup.status = BulkLookupStatus.REJECTED
    db.commit()
    db.refresh(campaign)
    return _detail_out(db, campaign)


@router.patch("/{campaign_id}/lookups/{lookup_id}", response_model=BulkCampaignDetailOut)
def set_bulk_lookup_email(
    campaign_id: int,
    lookup_id: int,
    payload: BulkLookupEmailRequest,
    db: Session = Depends(get_db),
):
    """Type in an address the search could not find, and accept it."""
    campaign = _get_campaign(db, campaign_id)
    email = (payload.email or "").strip()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="That isn't an email address.")
    lookup = db.get(BulkLookup, lookup_id)
    if lookup is None or lookup.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Lookup not found")
    contact = db.get(Contact, lookup.contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Recipient not found")
    lookup.email = email
    lookup.email_status = "provided"
    lookup.manual = True
    _adopt_lookup(db, contact, lookup)
    db.commit()
    db.refresh(campaign)
    return _detail_out(db, campaign)


def _lookups_by_id(
    db: Session, campaign_id: int, lookup_ids: list[int]
) -> list[BulkLookup]:
    if not lookup_ids:
        raise HTTPException(status_code=400, detail="No people were selected.")
    return list(
        db.execute(
            select(BulkLookup).where(
                BulkLookup.campaign_id == campaign_id,
                BulkLookup.id.in_(lookup_ids),
            )
        ).scalars().all()
    )


def _adopt_lookup(db: Session, contact: Contact, lookup: BulkLookup) -> None:
    """Copy an approved address (and what came with it) onto the contact."""
    contact.email = lookup.email
    contact.has_email = True
    contact.email_status = lookup.email_status
    if lookup.resolved_name and not lookup.manual:
        contact.name = lookup.resolved_name
    if lookup.resolved_title and not contact.title:
        contact.title = lookup.resolved_title
    if lookup.linkedin_url and not contact.linkedin_url:
        contact.linkedin_url = lookup.linkedin_url
    if lookup.location and not contact.location:
        contact.location = lookup.location
    if lookup.resolved_org and not contact.company_id:
        contact.company_id = _company_id_for(db, lookup.resolved_org)
    lookup.status = BulkLookupStatus.ACCEPTED


def _company_id_for(db: Session, name: str) -> int:
    normalized = name.strip().lower()
    company = db.execute(
        select(Company).where(Company.normalized_name == normalized)
    ).scalars().first()
    if company is None:
        company = Company(
            name=name.strip(),
            normalized_name=normalized,
            enrichment_source="bulk_lookup",
        )
        db.add(company)
        db.flush()
    return company.id


@router.post("/{campaign_id}/cancel", response_model=BulkCampaignDetailOut)
def cancel_bulk_job(campaign_id: int, db: Session = Depends(get_db)):
    """Ask the running job to stop after the person it is working on."""
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
    for lookup in db.execute(
        select(BulkLookup).where(BulkLookup.contact_id == contact_id)
    ).scalars().all():
        db.delete(lookup)
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
    for lookup in db.execute(
        select(BulkLookup).where(BulkLookup.campaign_id == campaign_id)
    ).scalars().all():
        db.delete(lookup)
    for contact in db.execute(
        select(Contact).where(Contact.bulk_campaign_id == campaign_id)
    ).scalars().all():
        db.delete(contact)
    db.delete(campaign)
    db.commit()
