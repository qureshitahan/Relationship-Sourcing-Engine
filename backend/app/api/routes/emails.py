"""Email draft generation, editing, approval, and (gated) sending."""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.company import Company
from app.models.contact import Contact
from app.models.email_draft import EmailDraft
from app.models.enums import AuditAction, EmailStatus
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.models.suppression import OutreachHistory
from app.schemas.entities import EmailDraftOut, Page
from app.models.discovery_run import DiscoveryRun
from app.schemas.requests import (
    EmailGenerateRequest,
    EmailGenerateRunRequest,
    EmailRegenerateRunRequest,
    EmailReplyRequest,
    EmailScheduleRequest,
    EmailStatusRequest,
    EmailUpdateRequest,
    FollowupGenerateRequest,
)
from app.services.outreach_goal import outreach_goal_for_run
from app.services.audit import log_action
from app.services.email_providers import (
    Mailbox,
    MailboxUnassignedError,
    find_mailbox,
    get_email_provider,
    list_mailboxes,
    mailbox_for_principal,
    provider_for_mailbox,
    resolve_mailbox,
)
from app.services.campaign_control import campaign_is_paused
from app.services.email_html import plain_body_to_html
from app.services.inbound_text import clean_inbound_reply, reply_snippet
from app.services.insights.engine import (
    apply_signature,
    build_signature,
    generate_followup,
    generate_insight,
    generate_outreach,
    generate_outreach_batch,
    generate_reply,
    principal_signature_ready,
    resolve_linkedin_url,
)
from app.services.outreach_eligibility import outreach_draft_blockers
from app.services.provider_health import active_warnings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emails", tags=["emails"])


def _clean_reply_field(text: Optional[str]) -> Optional[str]:
    """Normalize stored reply text for API consumers (safe on already-clean text)."""
    if text is None:
        return None
    cleaned = clean_inbound_reply(text)
    return cleaned or text


def _principal_mailbox_id(principal: Principal) -> Optional[str]:
    """The principal's mailbox id for stamping on a new draft.

    Returns None when it can't be determined; the send path then blocks with a
    clear message rather than the draft silently inheriting a global default.
    """
    try:
        return mailbox_for_principal(principal).id
    except MailboxUnassignedError:
        return None


def mailbox_for_draft(db: Session, draft: EmailDraft) -> Mailbox:
    """The mailbox a draft must be sent from.

    An explicit ``from_mailbox`` (per-draft picker, bulk campaign) always wins.
    Otherwise the principal's own mailbox is used — never a global default, which
    would deliver a principal's email from somebody else's address.
    """
    explicit = find_mailbox(draft.from_mailbox)
    if explicit:
        return explicit
    principal = db.get(Principal, draft.principal_id) if draft.principal_id else None
    if principal is not None:
        return mailbox_for_principal(principal)
    return resolve_mailbox(draft.from_mailbox)


def _draft_out(db: Session, draft: EmailDraft) -> EmailDraftOut:
    """Build API response with recipient/sender context for the review UI."""
    contact = db.get(Contact, draft.contact_id) if draft.contact_id else None
    company = db.get(Company, draft.company_id) if draft.company_id else None
    principal = db.get(Principal, draft.principal_id) if draft.principal_id else None
    try:
        mailbox = mailbox_for_draft(db, draft)
    except MailboxUnassignedError:
        # Surface the gap in the UI instead of implying a sender we won't use.
        mailbox = Mailbox(
            id="", label="", provider="", from_email="", from_name=""
        )
    return EmailDraftOut(
        id=draft.id,
        principal_id=draft.principal_id,
        bulk_campaign_id=draft.bulk_campaign_id,
        company_id=draft.company_id,
        contact_id=draft.contact_id,
        insight_id=draft.insight_id,
        subject=draft.subject,
        body=draft.body,
        status=draft.status,
        provider=draft.provider,
        provider_message_id=draft.provider_message_id,
        approved_by=draft.approved_by,
        created_at=draft.created_at,
        scheduled_at=draft.scheduled_at,
        outlook_scheduled=bool(draft.outlook_scheduled),
        sent_at=draft.sent_at,
        replied_at=draft.replied_at,
        reply_snippet=_clean_reply_field(draft.reply_snippet),
        reply_body=_clean_reply_field(draft.reply_body),
        last_reply_check_at=draft.last_reply_check_at,
        open_count=draft.open_count or 0,
        first_opened_at=draft.first_opened_at,
        last_opened_at=draft.last_opened_at,
        from_mailbox=draft.from_mailbox,
        from_name=mailbox.from_name or None,
        principal_name=principal.name if principal else None,
        from_email=mailbox.from_email or None,
        contact_name=contact.name if contact else None,
        contact_email=contact.email if contact else None,
        contact_title=contact.title if contact else None,
        company_name=company.name if company else None,
        discovery_run_id=contact.discovery_run_id if contact else None,
    )


@router.get("/mailboxes")
def get_mailboxes():
    """List selectable sending mailboxes for the 'Send from' picker."""
    return {
        "mailboxes": [
            {
                "id": mb.id,
                "label": mb.label,
                "from_email": mb.from_email,
                "from_name": mb.from_name,
                "provider": mb.provider,
            }
            for mb in list_mailboxes()
        ]
    }


@router.get("", response_model=Page[EmailDraftOut])
def list_emails(
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    contact_id: Optional[int] = None,
    principal_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    bulk_campaign_id: Optional[int] = None,
    discovery_run_id: Optional[int] = None,
    limit: int = Query(50, le=1000),
    offset: int = 0,
):
    query = select(EmailDraft)
    count_query = select(func.count()).select_from(EmailDraft)
    if discovery_run_id is not None:
        run_filter = Contact.discovery_run_id == discovery_run_id
        query = query.join(Contact, EmailDraft.contact_id == Contact.id).where(run_filter)
        count_query = (
            select(func.count())
            .select_from(EmailDraft)
            .join(Contact, EmailDraft.contact_id == Contact.id)
            .where(run_filter)
        )
    if status:
        query = query.where(EmailDraft.status == status)
        count_query = count_query.where(EmailDraft.status == status)
    if contact_id is not None:
        query = query.where(EmailDraft.contact_id == contact_id)
        count_query = count_query.where(EmailDraft.contact_id == contact_id)
    if principal_id is not None:
        query = query.where(EmailDraft.principal_id == principal_id)
        count_query = count_query.where(EmailDraft.principal_id == principal_id)
    if campaign_id is not None:
        query = query.where(EmailDraft.campaign_id == campaign_id)
        count_query = count_query.where(EmailDraft.campaign_id == campaign_id)
    if bulk_campaign_id is not None:
        query = query.where(EmailDraft.bulk_campaign_id == bulk_campaign_id)
        count_query = count_query.where(EmailDraft.bulk_campaign_id == bulk_campaign_id)
    query = query.order_by(EmailDraft.created_at.desc()).limit(limit).offset(offset)
    items = db.execute(query).scalars().all()
    total = db.execute(count_query).scalar_one()
    return Page[EmailDraftOut](
        items=[_draft_out(db, d) for d in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/generate", response_model=EmailDraftOut)
def generate_draft(payload: EmailGenerateRequest, db: Session = Depends(get_db)):
    principal = db.get(Principal, payload.principal_id)
    contact = db.get(Contact, payload.contact_id)
    if not principal or not contact:
        raise HTTPException(status_code=404, detail="Principal or prospect not found")

    if not principal_signature_ready(principal):
        raise HTTPException(
            status_code=400,
            detail=(
                "Principal needs full name, LinkedIn URL, and phone number "
                "before drafting emails."
            ),
        )

    blockers = outreach_draft_blockers(
        db, principal_id=principal.id, contact=contact
    )
    if blockers:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot draft email: {', '.join(blockers)}",
        )

    if not payload.regenerate:
        existing = db.execute(
            select(EmailDraft)
            .where(
                EmailDraft.principal_id == principal.id,
                EmailDraft.contact_id == contact.id,
                EmailDraft.status.in_([EmailStatus.DRAFT, EmailStatus.APPROVED]),
            )
            .order_by(EmailDraft.created_at.desc())
        ).scalars().first()
        if existing is not None:
            return _draft_out(db, existing)

    company = db.get(Company, contact.company_id) if contact.company_id else None
    insight = db.get(RelevanceInsight, payload.insight_id) if payload.insight_id else None
    if insight is None:
        insight = db.execute(
            select(RelevanceInsight).where(
                RelevanceInsight.principal_id == principal.id,
                RelevanceInsight.contact_id == contact.id,
            )
        ).scalar_one_or_none()

    content = generate_outreach(db, principal, contact, company, insight)

    if not payload.regenerate:
        existing = db.execute(
            select(EmailDraft)
            .where(
                EmailDraft.principal_id == principal.id,
                EmailDraft.contact_id == contact.id,
                EmailDraft.status.in_([EmailStatus.DRAFT, EmailStatus.APPROVED]),
            )
            .order_by(EmailDraft.created_at.desc())
        ).scalars().first()
        if existing is not None:
            return _draft_out(db, existing)

    draft = EmailDraft(
        principal_id=principal.id,
        company_id=contact.company_id,
        contact_id=contact.id,
        insight_id=insight.id if insight else None,
        subject=content.subject,
        body=content.body,
        from_mailbox=_principal_mailbox_id(principal),
        status=EmailStatus.DRAFT,
    )
    db.add(draft)
    log_action(
        db,
        AuditAction.EMAIL_DRAFT,
        entity_type="email_draft",
        summary=f"Drafted outreach for principal {principal.id} -> prospect {contact.id}",
    )
    db.commit()
    db.refresh(draft)
    return _draft_out(db, draft)


def _refresh_bulk_draft(db: Session, draft: EmailDraft) -> EmailDraft:
    """Rewrite one bulk email from its campaign brief (no principal, no insight)."""
    from app.models.bulk_campaign import BulkCampaign
    from app.services.bulk_email.drafter import build_email
    from app.services.bulk_email.runner import (
        campaign_signature,
        recipient_payload,
        sender_context,
    )

    campaign = db.get(BulkCampaign, draft.bulk_campaign_id)
    contact = db.get(Contact, draft.contact_id) if draft.contact_id else None
    if campaign is None or contact is None:
        raise HTTPException(status_code=404, detail="Bulk campaign or recipient not found")
    if not (campaign.purpose or "").strip():
        raise HTTPException(
            status_code=400, detail="This bulk campaign has no brief to write from yet."
        )
    draft.subject, draft.body = build_email(
        sender=sender_context(campaign),
        purpose=campaign.purpose,
        recipient=recipient_payload(db, contact),
        signature=campaign_signature(campaign),
    )
    return draft


def _refresh_draft_content(db: Session, draft: EmailDraft) -> EmailDraft:
    """Regenerate subject/body for an editable draft (per-person, insight-grounded)."""
    if draft.status in (EmailStatus.SENT, EmailStatus.REPLIED, EmailStatus.SCHEDULED):
        raise HTTPException(
            status_code=400,
            detail="Cannot regenerate sent, replied, or scheduled emails.",
        )
    if draft.bulk_campaign_id:
        return _refresh_bulk_draft(db, draft)
    principal = db.get(Principal, draft.principal_id) if draft.principal_id else None
    contact = db.get(Contact, draft.contact_id) if draft.contact_id else None
    if not principal or not contact:
        raise HTTPException(status_code=404, detail="Principal or prospect not found")
    company = db.get(Company, contact.company_id) if contact.company_id else None
    insight = _latest_insight(db, principal.id, contact.id)
    content = generate_outreach(db, principal, contact, company, insight)
    draft.subject = content.subject
    draft.body = content.body
    if not draft.insight_id and insight:
        draft.insight_id = insight.id
    return draft


@router.post("/{draft_id}/regenerate", response_model=EmailDraftOut)
def regenerate_draft(draft_id: int, db: Session = Depends(get_db)):
    """Replace an existing draft's subject/body with a fresh LLM pass."""
    draft = db.get(EmailDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    _refresh_draft_content(db, draft)
    log_action(
        db,
        AuditAction.EMAIL_DRAFT,
        entity_type="email_draft",
        entity_id=draft.id,
        summary=f"Regenerated draft {draft.id} for prospect {draft.contact_id}",
    )
    db.commit()
    db.refresh(draft)
    return _draft_out(db, draft)


@router.post("/generate-run")
def generate_run_drafts(payload: EmailGenerateRunRequest, db: Session = Depends(get_db)):
    """Draft first-touch emails for approved prospects in a run (skips existing drafts)."""
    run = db.get(DiscoveryRun, payload.discovery_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Discovery run not found")

    principal_id = payload.principal_id or run.principal_id
    if principal_id is None:
        raise HTTPException(status_code=400, detail="No principal for this run")
    principal = db.get(Principal, principal_id)
    if principal is None:
        raise HTTPException(status_code=404, detail="Principal not found")
    if not principal_signature_ready(principal):
        raise HTTPException(
            status_code=400,
            detail=(
                "Principal needs full name, LinkedIn URL, and phone number "
                "before drafting emails."
            ),
        )

    approved_contacts = list(
        db.execute(
            select(Contact)
            .where(
                Contact.discovery_run_id == payload.discovery_run_id,
                Contact.approved_for_outreach.is_(True),
            )
            .order_by(Contact.id)
        ).scalars().all()
    )
    if not approved_contacts:
        return {
            "discovery_run_id": payload.discovery_run_id,
            "candidates": 0,
            "generated": 0,
            "skipped": 0,
            "errors": [],
        }

    contact_ids = [c.id for c in approved_contacts]
    existing_draft_contact_ids = set(
        db.execute(
            select(EmailDraft.contact_id)
            .where(
                EmailDraft.principal_id == principal.id,
                EmailDraft.contact_id.in_(contact_ids),
                EmailDraft.status.in_(
                    [EmailStatus.DRAFT, EmailStatus.APPROVED, EmailStatus.SCHEDULED]
                ),
            )
        ).scalars().all()
    )
    to_draft = [c for c in approved_contacts if c.id not in existing_draft_contact_ids]
    skipped = len(approved_contacts) - len(to_draft)
    errors: list[str] = []

    eligible: list[Contact] = []
    for contact in to_draft:
        blockers = outreach_draft_blockers(
            db, principal_id=principal.id, contact=contact
        )
        if blockers:
            skipped += 1
            errors.append(
                f"{contact.name or contact.id}: {', '.join(blockers)}"
            )
        else:
            eligible.append(contact)
    to_draft = eligible

    if not to_draft:
        return {
            "discovery_run_id": payload.discovery_run_id,
            "candidates": len(approved_contacts),
            "generated": 0,
            "skipped": skipped,
            "errors": errors[:10],
        }

    goal = (payload.outreach_goal or "").strip() or outreach_goal_for_run(run.criteria)
    generated = 0
    try:
        pairs = generate_outreach_batch(db, principal, to_draft, outreach_goal=goal)
        result_by_contact = {c.id: r for c, r in pairs}
        for contact in to_draft:
            result = result_by_contact.get(contact.id)
            if not result:
                errors.append(f"{contact.name or contact.id}: no generated content")
                continue
            insight = _latest_insight(db, principal.id, contact.id)
            draft = EmailDraft(
                principal_id=principal.id,
                company_id=contact.company_id,
                contact_id=contact.id,
                insight_id=insight.id if insight else None,
                subject=result.subject,
                body=result.body,
                from_mailbox=_principal_mailbox_id(principal),
                status=EmailStatus.DRAFT,
            )
            db.add(draft)
            generated += 1
        if generated:
            log_action(
                db,
                AuditAction.EMAIL_DRAFT,
                entity_type="discovery_run",
                summary=(
                    f"Generated {generated} draft(s) for run {payload.discovery_run_id}"
                ),
            )
            db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        errors.append(str(exc))
        logger.warning("Generate run drafts failed for run %s: %s", payload.discovery_run_id, exc)

    return {
        "discovery_run_id": payload.discovery_run_id,
        "candidates": len(approved_contacts),
        "generated": generated,
        "skipped": skipped,
        "errors": errors[:10],
        "provider_warnings": active_warnings(),
    }


@router.post("/regenerate-run")
def regenerate_run_drafts(payload: EmailRegenerateRunRequest, db: Session = Depends(get_db)):
    """Re-draft all editable emails for prospects from a discovery run (per-person)."""
    allowed = payload.only_statuses or [EmailStatus.DRAFT]
    query = (
        select(EmailDraft)
        .join(Contact, EmailDraft.contact_id == Contact.id)
        .where(Contact.discovery_run_id == payload.discovery_run_id)
        .where(EmailDraft.status.in_(allowed))
    )
    if payload.principal_id is not None:
        query = query.where(EmailDraft.principal_id == payload.principal_id)
    drafts = db.execute(query.order_by(EmailDraft.id)).scalars().all()

    if not drafts:
        return {
            "discovery_run_id": payload.discovery_run_id,
            "candidates": 0,
            "regenerated": 0,
            "errors": [],
        }

    by_principal: dict[int, list[EmailDraft]] = {}
    for draft in drafts:
        if draft.principal_id:
            by_principal.setdefault(draft.principal_id, []).append(draft)

    regenerated = 0
    errors: list[str] = []
    for principal_id, principal_drafts in by_principal.items():
        principal = db.get(Principal, principal_id)
        if not principal:
            errors.append(f"Principal #{principal_id} not found")
            continue
        contact_ids = [d.contact_id for d in principal_drafts if d.contact_id]
        contacts = (
            db.execute(select(Contact).where(Contact.id.in_(contact_ids))).scalars().all()
            if contact_ids
            else []
        )
        contact_by_id = {c.id: c for c in contacts}
        ordered_contacts = [
            contact_by_id[d.contact_id]
            for d in principal_drafts
            if d.contact_id and d.contact_id in contact_by_id
        ]
        try:
            pairs = generate_outreach_batch(db, principal, ordered_contacts)
            result_by_contact = {c.id: r for c, r in pairs}
            for draft in principal_drafts:
                if not draft.contact_id:
                    continue
                result = result_by_contact.get(draft.contact_id)
                if not result:
                    errors.append(f"Draft #{draft.id}: no generated content")
                    continue
                draft.subject = result.subject
                draft.body = result.body
                regenerated += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Principal #{principal_id} batch failed: {exc}")
            logger.warning("Regenerate run batch for principal %s failed: %s", principal_id, exc)

    if regenerated:
        db.commit()
    return {
        "discovery_run_id": payload.discovery_run_id,
        "candidates": len(drafts),
        "regenerated": regenerated,
        "errors": errors[:10],
        "provider_warnings": active_warnings(),
    }


def _latest_insight(db: Session, principal_id: int, contact_id: int):
    return db.execute(
        select(RelevanceInsight)
        .where(
            RelevanceInsight.principal_id == principal_id,
            RelevanceInsight.contact_id == contact_id,
        )
        .order_by(RelevanceInsight.created_at.desc())
    ).scalars().first()


def _sent_count(db: Session, contact_id: int) -> int:
    return db.execute(
        select(func.count())
        .select_from(EmailDraft)
        .where(
            EmailDraft.contact_id == contact_id,
            EmailDraft.status.in_([EmailStatus.SENT, EmailStatus.REPLIED]),
        )
    ).scalar_one()


def _build_followup(db: Session, previous: EmailDraft, approve: bool) -> EmailDraft:
    """Create (but don't commit) a follow-up draft based on a prior sent email."""
    principal = db.get(Principal, previous.principal_id) if previous.principal_id else None
    contact = db.get(Contact, previous.contact_id) if previous.contact_id else None
    if not principal or not contact:
        raise SendError("Principal or prospect not found for follow-up", status_code=404)
    company = db.get(Company, contact.company_id) if contact.company_id else None
    insight = _latest_insight(db, principal.id, contact.id)
    step = _sent_count(db, contact.id) + 1
    content = generate_followup(
        db,
        principal,
        contact,
        company,
        insight,
        {"subject": previous.subject, "body": previous.body},
        step=step,
    )
    draft = EmailDraft(
        principal_id=principal.id,
        company_id=contact.company_id,
        contact_id=contact.id,
        insight_id=insight.id if insight else None,
        subject=content.subject,
        body=content.body,
        # Follow up from the same mailbox as the original, so the thread stays
        # with one sender identity.
        from_mailbox=previous.from_mailbox or mailbox_for_principal(principal).id,
        status=EmailStatus.APPROVED if approve else EmailStatus.DRAFT,
    )
    if approve:
        draft.approved_by = "auto-followup"
        draft.approved_at = datetime.utcnow()
    db.add(draft)
    log_action(
        db,
        AuditAction.EMAIL_DRAFT,
        entity_type="email_draft",
        summary=f"Drafted follow-up #{step} for prospect {contact.id}",
    )
    return draft


@router.post("/followups/generate")
def generate_followups(payload: FollowupGenerateRequest, db: Session = Depends(get_db)):
    """Agentic follow-ups: draft a nudge for everyone silent for >= ``days``.

    For each contact with a sent email older than the cutoff and no reply, and
    no pending unsent follow-up already waiting, generate a short follow-up draft
    (or pre-approved email if ``approve`` is set). Returns a summary + new drafts.
    """
    days = max(0, payload.days)
    limit = max(1, payload.limit or 25)
    cutoff = datetime.utcnow() - timedelta(days=days)

    sent_drafts = db.execute(
        select(EmailDraft)
        .where(
            EmailDraft.status == EmailStatus.SENT,
            EmailDraft.sent_at.isnot(None),
        )
        .order_by(EmailDraft.sent_at.desc())
    ).scalars().all()

    # Keep only the most recent sent email per contact.
    latest_by_contact: dict[int, EmailDraft] = {}
    for d in sent_drafts:
        if d.contact_id is None:
            continue
        if payload.principal_id and d.principal_id != payload.principal_id:
            continue
        latest_by_contact.setdefault(d.contact_id, d)

    created: list[EmailDraft] = []
    candidates = 0
    skipped_pending = 0
    for contact_id, last in latest_by_contact.items():
        if last.sent_at is None or last.sent_at > cutoff:
            continue
        replied = db.execute(
            select(func.count())
            .select_from(EmailDraft)
            .where(
                EmailDraft.contact_id == contact_id,
                EmailDraft.status == EmailStatus.REPLIED,
            )
        ).scalar_one()
        if replied:
            continue
        contact = db.get(Contact, contact_id)
        if not contact or not contact.email or contact.do_not_contact:
            continue
        candidates += 1
        pending = db.execute(
            select(func.count())
            .select_from(EmailDraft)
            .where(
                EmailDraft.contact_id == contact_id,
                EmailDraft.status.in_(
                    [EmailStatus.DRAFT, EmailStatus.APPROVED, EmailStatus.SCHEDULED]
                ),
                EmailDraft.created_at > last.sent_at,
            )
        ).scalar_one()
        if pending:
            skipped_pending += 1
            continue
        if len(created) >= limit:
            continue
        try:
            created.append(_build_followup(db, last, payload.approve))
        except SendError:
            continue

    db.commit()
    for d in created:
        db.refresh(d)
    return {
        "created": len(created),
        "candidates": candidates,
        "skipped_pending": skipped_pending,
        "days": days,
        "drafts": [_draft_out(db, d) for d in created],
    }


@router.post("/{draft_id}/followup", response_model=EmailDraftOut)
def create_followup(draft_id: int, db: Session = Depends(get_db)):
    """Draft a single follow-up to one already-sent email.

    Idempotent: if an unsent follow-up already exists for this person (created
    after the last send), return it instead of creating a duplicate.
    """
    previous = db.get(EmailDraft, draft_id)
    if not previous:
        raise HTTPException(status_code=404, detail="Draft not found")
    if previous.status not in (EmailStatus.SENT, EmailStatus.REPLIED):
        raise HTTPException(
            status_code=400, detail="Follow-ups are only for emails already sent"
        )
    if previous.contact_id is not None and previous.sent_at is not None:
        existing = db.execute(
            select(EmailDraft)
            .where(
                EmailDraft.contact_id == previous.contact_id,
                EmailDraft.status.in_(
                    [EmailStatus.DRAFT, EmailStatus.APPROVED, EmailStatus.SCHEDULED]
                ),
                EmailDraft.created_at > previous.sent_at,
            )
            .order_by(EmailDraft.created_at.desc())
        ).scalars().first()
        if existing is not None:
            return _draft_out(db, existing)
    try:
        draft = _build_followup(db, previous, approve=False)
    except SendError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.commit()
    db.refresh(draft)
    return _draft_out(db, draft)


@router.get("/track/{draft_id}.gif")
def track_open(draft_id: int, t: str = "", db: Session = Depends(get_db)):
    """Open-tracking pixel. Returns a 1x1 GIF and records the open if the token matches.

    Always returns the image (even on a bad token) so we never break the email.
    """
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    }
    draft = db.get(EmailDraft, draft_id)
    if draft and t and hmac.compare_digest(t, _open_token(draft_id)):
        now = datetime.utcnow()
        draft.open_count = (draft.open_count or 0) + 1
        if draft.first_opened_at is None:
            draft.first_opened_at = now
        draft.last_opened_at = now
        db.commit()
    return Response(content=_TRACKING_PIXEL, media_type="image/gif", headers=headers)


@router.post("/{draft_id}/draft-reply", response_model=EmailDraftOut)
def draft_contextual_reply(draft_id: int, db: Session = Depends(get_db)):
    """Draft an intelligent reply to an inbound email — does NOT send.

    Uses the original outreach + their reply text so the draft acknowledges
    what they said (pain point, hesitation, interest) and aims at booking a
    short call. Creates a normal DRAFT you can edit, then Approve & send.
    """
    previous = db.get(EmailDraft, draft_id)
    if not previous:
        raise HTTPException(status_code=404, detail="Draft not found")
    if previous.status != EmailStatus.REPLIED:
        raise HTTPException(
            status_code=400,
            detail="Draft reply is only available after the prospect has replied",
        )
    inbound = (previous.reply_body or previous.reply_snippet or "").strip()
    if not inbound:
        raise HTTPException(
            status_code=400,
            detail="No reply text on file for this conversation yet",
        )
    principal = db.get(Principal, previous.principal_id) if previous.principal_id else None
    contact = db.get(Contact, previous.contact_id) if previous.contact_id else None
    if not principal or not contact:
        raise HTTPException(status_code=404, detail="Principal or prospect not found")

    # Reuse an existing unsent reply draft for this thread instead of stacking.
    if previous.sent_at is not None:
        existing = db.execute(
            select(EmailDraft)
            .where(
                EmailDraft.contact_id == previous.contact_id,
                EmailDraft.status.in_(
                    [EmailStatus.DRAFT, EmailStatus.APPROVED, EmailStatus.SCHEDULED]
                ),
                EmailDraft.created_at > previous.sent_at,
                EmailDraft.subject.ilike("re:%"),
            )
            .order_by(EmailDraft.created_at.desc())
        ).scalars().first()
        if existing is not None:
            return _draft_out(db, existing)

    company = db.get(Company, contact.company_id) if contact.company_id else None
    insight = _latest_insight(db, principal.id, contact.id)
    content = generate_reply(
        db,
        principal,
        contact,
        company,
        insight,
        {"subject": previous.subject, "body": previous.body},
        inbound,
    )
    draft = EmailDraft(
        principal_id=principal.id,
        company_id=contact.company_id,
        contact_id=contact.id,
        insight_id=insight.id if insight else None,
        campaign_id=previous.campaign_id,
        bulk_campaign_id=previous.bulk_campaign_id,
        subject=content.subject,
        body=content.body,
        from_mailbox=previous.from_mailbox or _principal_mailbox_id(principal),
        status=EmailStatus.DRAFT,
    )
    db.add(draft)
    log_action(
        db,
        AuditAction.EMAIL_DRAFT,
        entity_type="email_draft",
        summary=f"Drafted contextual reply for prospect {contact.id}",
    )
    db.commit()
    db.refresh(draft)
    return _draft_out(db, draft)


@router.post("/{draft_id}/reply", response_model=EmailDraftOut)
def reply_to_thread(draft_id: int, payload: EmailReplyRequest, db: Session = Depends(get_db)):
    """Send a reply to the prospect from inside the app, in the same thread.

    Creates a new sent email record (subject 'Re: ...') so it shows up in the
    conversation timeline alongside the original outreach and their reply.
    """
    previous = db.get(EmailDraft, draft_id)
    if not previous:
        raise HTTPException(status_code=404, detail="Draft not found")
    contact = db.get(Contact, previous.contact_id) if previous.contact_id else None
    if not contact or not contact.email:
        raise HTTPException(status_code=400, detail="No prospect email to reply to")
    principal = db.get(Principal, previous.principal_id) if previous.principal_id else None
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Reply body is required")
    if principal is not None:
        body = apply_signature(body, principal)

    subject = (previous.subject or "").strip()
    while subject.lower().startswith("re:"):
        subject = subject[3:].strip()
    subject = f"Re: {subject}" if subject else "Re:"

    draft = EmailDraft(
        principal_id=previous.principal_id,
        company_id=previous.company_id,
        contact_id=previous.contact_id,
        insight_id=previous.insight_id,
        subject=subject,
        body=body,
        # Reply from the same mailbox the original was sent from.
        from_mailbox=previous.from_mailbox,
        status=EmailStatus.APPROVED,
        approved_by="user",
        approved_at=datetime.utcnow(),
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    try:
        perform_send(db, draft)
    except SendError as exc:
        # Keep the drafted reply so the user doesn't lose their text.
        draft.status = EmailStatus.DRAFT
        db.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return _draft_out(db, draft)


@router.delete("/{draft_id}", status_code=204)
def delete_draft(draft_id: int, db: Session = Depends(get_db)):
    """Remove an unsent draft (e.g. accidental duplicate)."""
    draft = db.get(EmailDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status in (EmailStatus.SENT, EmailStatus.REPLIED):
        raise HTTPException(status_code=400, detail="Cannot delete a sent email")
    db.delete(draft)
    db.commit()


@router.post("/apply-signature")
def apply_signature_to_drafts(db: Session = Depends(get_db)):
    """Append the principal's standard signature to all editable drafts.

    Idempotent — re-running won't stack signatures. Skips sent/replied emails so
    we never rewrite something already delivered.
    """
    drafts = db.execute(
        select(EmailDraft).where(
            EmailDraft.status.in_(
                [EmailStatus.DRAFT, EmailStatus.APPROVED, EmailStatus.SCHEDULED]
            )
        )
    ).scalars().all()
    updated = 0
    skipped = 0
    principals: dict[int, Principal] = {}
    for draft in drafts:
        if not draft.principal_id:
            skipped += 1
            continue
        principal = principals.get(draft.principal_id)
        if principal is None:
            principal = db.get(Principal, draft.principal_id)
            if principal is None:
                skipped += 1
                continue
            principals[draft.principal_id] = principal
        if not principal_signature_ready(principal):
            skipped += 1
            continue
        new_body = apply_signature(draft.body, principal)
        if new_body != draft.body:
            draft.body = new_body
            updated += 1
    db.commit()
    sent_skipped = db.execute(
        select(func.count())
        .select_from(EmailDraft)
        .where(EmailDraft.status.in_([EmailStatus.SENT, EmailStatus.REPLIED]))
    ).scalar_one()
    return {
        "updated": updated,
        "skipped": skipped,
        "total": len(drafts),
        "sent_skipped": sent_skipped,
        "principals_incomplete": sum(
            1
            for p in principals.values()
            if not principal_signature_ready(p)
        ),
        "linkedin_url": resolve_linkedin_url(next(iter(principals.values())))
        if principals
        else None,
    }


@router.patch("/{draft_id}", response_model=EmailDraftOut)
def update_draft(draft_id: int, payload: EmailUpdateRequest, db: Session = Depends(get_db)):
    draft = db.get(EmailDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status == EmailStatus.SENT:
        raise HTTPException(status_code=400, detail="Cannot edit a sent email")
    if payload.subject is not None:
        draft.subject = payload.subject
    if payload.body is not None:
        draft.body = payload.body
    if payload.from_mailbox is not None:
        draft.from_mailbox = payload.from_mailbox or None
    db.commit()
    db.refresh(draft)
    return _draft_out(db, draft)


@router.post("/{draft_id}/status", response_model=EmailDraftOut)
def set_status(draft_id: int, payload: EmailStatusRequest, db: Session = Depends(get_db)):
    """Update draft status (approve, mark replied/bounced/not_interested, etc.)."""
    draft = db.get(EmailDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    draft.status = payload.status
    if payload.status == EmailStatus.APPROVED:
        draft.approved_by = payload.approved_by
        draft.approved_at = datetime.utcnow()
    log_action(
        db,
        AuditAction.EMAIL_APPROVAL,
        entity_type="email_draft",
        entity_id=draft.id,
        actor=payload.approved_by or "user",
        summary=f"Email status -> {payload.status}",
    )
    db.commit()
    db.refresh(draft)
    return _draft_out(db, draft)


class SendError(Exception):
    """Raised when a draft cannot be sent. ``status_code`` shapes the HTTP error."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# A 43-byte transparent 1x1 GIF returned by the open-tracking pixel.
_TRACKING_PIXEL = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b"
)


def _open_token(draft_id: int) -> str:
    """Sign the open-tracking pixel URL so it can't be trivially forged/enumerated."""
    secret = (
        settings.tracking_secret
        or settings.microsoft_client_secret
        or "lgw-open-tracking"
    ).encode()
    return hmac.new(secret, f"open:{draft_id}".encode(), hashlib.sha256).hexdigest()[:16]


def _pixel_url(draft_id: int) -> Optional[str]:
    base = (settings.app_public_url or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/api/emails/track/{draft_id}.gif?t={_open_token(draft_id)}"


def _draft_html(db: Session, draft: EmailDraft, *, pixel_url: Optional[str] = None) -> str:
    """HTML body for outbound mail — polished signature + optional open pixel."""
    signature = None
    if draft.principal_id:
        principal = db.get(Principal, draft.principal_id)
        if principal:
            signature = build_signature(principal) or None
            # Prefer the raw custom text for parsing (richer structure).
            custom = (principal.email_signature or "").strip()
            if custom:
                signature = custom
    return plain_body_to_html(draft.body or "", signature=signature, pixel_url=pixel_url)


def _html_with_pixel(body: str, pixel_url: Optional[str]) -> str:
    """Legacy helper — prefer ``_draft_html`` when a draft is available."""
    return plain_body_to_html(body, pixel_url=pixel_url)


def perform_send(db: Session, draft: EmailDraft) -> EmailDraft:
    """Send an approved/scheduled draft via the configured provider.

    Shared by the manual /send endpoint and the background scheduler. Commits
    the draft on success. Raises ``SendError`` on any validation/transport
    failure (the caller decides how to surface it).
    """
    if draft.status not in (EmailStatus.APPROVED, EmailStatus.SCHEDULED):
        raise SendError("Email must be APPROVED before sending")
    if campaign_is_paused(db, draft.campaign_id):
        raise SendError("This campaign is paused. Resume it before sending.")
    if draft.outlook_scheduled:
        raise SendError(
            "This email is queued in Outlook for deferred delivery. Unschedule it first."
        )

    contact = db.get(Contact, draft.contact_id) if draft.contact_id else None
    if not contact or not contact.email:
        raise SendError("No prospect email to send to")
    if contact.do_not_contact:
        raise SendError("Prospect is on do-not-contact list")

    # Send as the principal who owns this outreach — never a global default.
    try:
        mailbox = mailbox_for_draft(db, draft)
    except MailboxUnassignedError as exc:
        raise SendError(str(exc))

    # Always send a polished HTML alternative so signatures look professional.
    # Keep draft.body as plain text for editing; Gmail multipart includes both.
    pixel = _pixel_url(draft.id) if settings.track_opens else None
    html_body = _draft_html(db, draft, pixel_url=pixel)

    if not mailbox.from_email:
        raise SendError(
            f'Mailbox "{mailbox.id}" has no from_email configured. Fix '
            "OUTREACH_MAILBOXES before sending."
        )

    provider = provider_for_mailbox(mailbox)
    result = provider.send(
        to_email=contact.email,
        subject=draft.subject,
        body=draft.body,
        from_email=mailbox.from_email,
        from_name=mailbox.from_name,
        html_body=html_body,
    )
    if not result.sent:
        raise SendError(result.error or "Send failed", status_code=502)

    # Record which mailbox actually sent it, so history can't be ambiguous.
    draft.from_mailbox = mailbox.id
    draft.status = EmailStatus.SENT
    draft.provider = result.provider
    draft.provider_message_id = result.message_id
    draft.conversation_id = result.conversation_id
    draft.internet_message_id = result.internet_message_id
    draft.scheduled_at = None
    draft.sent_at = datetime.utcnow()
    db.add(
        OutreachHistory(
            company_id=draft.company_id,
            contact_id=draft.contact_id,
            channel="email",
            detail=f"Sent via {result.provider}",
        )
    )
    log_action(
        db,
        AuditAction.EMAIL_SEND,
        entity_type="email_draft",
        entity_id=draft.id,
        summary=f"Sent via {result.provider} ({result.message_id})",
    )
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/{draft_id}/send", response_model=EmailDraftOut)
def send_email(draft_id: int, db: Session = Depends(get_db)):
    """Send an APPROVED draft via the configured provider.

    Requires explicit prior approval (human-in-the-loop). The default stub
    provider does not actually transmit anything.
    """
    draft = db.get(EmailDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    try:
        perform_send(db, draft)
    except SendError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return _draft_out(db, draft)


@router.post("/{draft_id}/schedule", response_model=EmailDraftOut)
def schedule_email(
    draft_id: int, payload: EmailScheduleRequest, db: Session = Depends(get_db)
):
    """Approve (if needed) and schedule a draft to send at a future UTC time.

    When the mail provider supports server-side deferred delivery (Microsoft
    Outlook/Graph), the message is handed to Outlook immediately and delivered
    at the scheduled time even if this app is offline. Otherwise it falls back
    to the in-process scheduler (only fires while the backend is running).
    """
    draft = db.get(EmailDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status in (EmailStatus.SENT, EmailStatus.REPLIED):
        raise HTTPException(status_code=400, detail="Email already sent")
    if campaign_is_paused(db, draft.campaign_id):
        raise HTTPException(
            status_code=409,
            detail="This campaign is paused. Resume it before scheduling sends.",
        )

    contact = db.get(Contact, draft.contact_id) if draft.contact_id else None
    if not contact or not contact.email:
        raise HTTPException(
            status_code=400, detail="Reveal the prospect's email before scheduling"
        )
    if contact.do_not_contact:
        raise HTTPException(status_code=400, detail="Prospect is on do-not-contact list")

    when = payload.scheduled_at
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    if when <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="Scheduled time must be in the future")

    if not draft.approved_at:
        draft.approved_at = datetime.utcnow()
    draft.approved_by = payload.approved_by

    try:
        mailbox = mailbox_for_draft(db, draft)
    except MailboxUnassignedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    provider = provider_for_mailbox(mailbox)
    outlook = False
    if provider.supports_scheduled_send():
        # Hand off to Outlook for server-side deferred delivery.
        pixel = _pixel_url(draft.id) if settings.track_opens else None
        html_body = _draft_html(db, draft, pixel_url=pixel)
        result = provider.send(
            to_email=contact.email,
            subject=draft.subject,
            body=draft.body,
            from_email=mailbox.from_email,
            from_name=mailbox.from_name,
            html_body=html_body,
            send_at=when,
        )
        if not result.sent:
            raise HTTPException(
                status_code=502,
                detail=result.error or "Outlook could not schedule the send.",
            )
        outlook = True
        draft.provider = result.provider
        draft.provider_message_id = result.remote_message_id or result.message_id
        draft.conversation_id = result.conversation_id
        draft.internet_message_id = result.internet_message_id

    draft.status = EmailStatus.SCHEDULED
    draft.scheduled_at = when
    draft.outlook_scheduled = outlook
    # Pin the sender now so a later config change can't reroute a queued send.
    draft.from_mailbox = mailbox.id
    log_action(
        db,
        AuditAction.EMAIL_APPROVAL,
        entity_type="email_draft",
        entity_id=draft.id,
        actor=payload.approved_by or "user",
        summary=(
            f"Scheduled via Outlook for {when.isoformat()}Z"
            if outlook
            else f"Scheduled (in-app) for {when.isoformat()}Z"
        ),
    )
    db.commit()
    db.refresh(draft)
    return _draft_out(db, draft)


@router.post("/{draft_id}/unschedule", response_model=EmailDraftOut)
def unschedule_email(draft_id: int, db: Session = Depends(get_db)):
    """Cancel a pending scheduled send, reverting the draft to APPROVED.

    For Outlook-deferred sends, this also asks Outlook to delete the queued
    message so it is not delivered.
    """
    draft = db.get(EmailDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status != EmailStatus.SCHEDULED:
        raise HTTPException(status_code=400, detail="Draft is not scheduled")

    if draft.outlook_scheduled and draft.provider_message_id:
        provider = provider_for_mailbox(resolve_mailbox(draft.from_mailbox))
        cancelled = provider.cancel_scheduled(
            remote_message_id=draft.provider_message_id
        )
        if not cancelled:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Could not cancel the message in Outlook. It may already be "
                    "sending. Check the mailbox Outbox/Sent Items."
                ),
            )
        draft.provider_message_id = None
        draft.conversation_id = None
        draft.internet_message_id = None

    draft.status = EmailStatus.APPROVED
    draft.scheduled_at = None
    draft.outlook_scheduled = False
    db.commit()
    db.refresh(draft)
    return _draft_out(db, draft)


def scan_replies(db: Session) -> dict:
    """Poll the mailbox for replies to sent emails (shared by the route + poller).

    Requires the email provider to support reply tracking (Microsoft Graph with
    Mail.Read). Marks matched drafts REPLIED, records a snippet/body, advances the
    prospect to CONNECTED, and triggers deep research on reply. Degrades gracefully
    if Mail.Read is not granted.
    """
    # Reply tracking works per-mailbox: a Gmail-sent email is checked via Gmail
    # IMAP, a Graph-sent one via Graph. Supported if ANY mailbox supports it.
    if not any(
        provider_for_mailbox(mb).supports_reply_tracking() for mb in list_mailboxes()
    ):
        return {
            "checked": 0,
            "replied": 0,
            "supported": False,
            "message": (
                "Reply tracking needs Microsoft Graph (Mail.Read) or Gmail (IMAP). "
                "Configure at least one sending mailbox with reply access."
            ),
        }

    # Only sent emails that have not already been marked replied.
    sent_drafts = db.execute(
        select(EmailDraft).where(
            EmailDraft.status == EmailStatus.SENT,
            EmailDraft.sent_at.isnot(None),
        )
    ).scalars().all()

    checked = 0
    replied = 0
    error: Optional[str] = None
    for draft in sent_drafts:
        contact = db.get(Contact, draft.contact_id) if draft.contact_id else None
        if not contact or not contact.email or not draft.sent_at:
            continue
        provider = provider_for_mailbox(resolve_mailbox(draft.from_mailbox))
        if not provider.supports_reply_tracking():
            continue
        checked += 1
        result = provider.check_reply(
            to_email=contact.email,
            since=draft.sent_at,
            conversation_id=draft.conversation_id,
        )
        draft.last_reply_check_at = datetime.utcnow()
        if result.error:
            error = result.error
            continue
        if result.found:
            draft.status = EmailStatus.REPLIED
            draft.replied_at = result.received_at or datetime.utcnow()
            raw = result.body or result.snippet or ""
            cleaned = clean_inbound_reply(raw)
            draft.reply_body = cleaned or raw
            draft.reply_snippet = reply_snippet(cleaned or raw)
            if contact.status not in ("connected", "closed"):
                contact.status = "connected"
            replied += 1
            log_action(
                db,
                AuditAction.EMAIL_APPROVAL,
                entity_type="email_draft",
                entity_id=draft.id,
                summary=f"Detected reply from {contact.email}",
            )
            # Deep research happens NOW (on reply), not up front: this is where
            # we spend LLM budget to qualify the people who actually engaged.
            if draft.principal_id:
                principal = db.get(Principal, draft.principal_id)
                if principal:
                    try:
                        generate_insight(db, principal, contact=contact)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Post-reply research failed for contact %s: %s",
                            contact.id,
                            exc,
                        )
    db.commit()
    return {
        "checked": checked,
        "replied": replied,
        "supported": True,
        "error": error,
        "message": (
            f"Checked {checked} sent email(s); {replied} new repl{'y' if replied == 1 else 'ies'} found."
            if not error
            else error
        ),
    }


@router.post("/check-replies")
def check_replies(db: Session = Depends(get_db)):
    """Manually poll the mailbox for replies to sent emails."""
    return scan_replies(db)
