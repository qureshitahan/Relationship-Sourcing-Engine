"""Background drafting and sending for bulk campaigns.

Both jobs run in daemon threads (the same pattern as agent runs and the email
scheduler) because a hundred LLM calls or a hundred sends must not block the
request. Progress is written back onto the campaign row so the UI can poll it.

Drafting fans out across a few threads: each email is an independent LLM call
with no database access, so only the resulting rows are written back on the job
thread, keeping one session per thread.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.bulk_campaign import BulkCampaign
from app.models.company import Company
from app.models.contact import Contact
from app.models.email_draft import EmailDraft
from app.models.enums import AuditAction, BulkCampaignStatus, EmailStatus
from app.services.audit import log_action
from app.services.bulk_email.drafter import build_email, default_signature
from app.services.email_providers import resolve_mailbox

logger = logging.getLogger(__name__)

# Concurrent drafting calls. Small enough to stay well inside Anthropic rate
# limits when several campaigns draft at once.
DRAFT_WORKERS = 4
# Spacing between sends so a hundred-recipient campaign doesn't look like a
# burst of spam to the receiving mail servers.
SEND_DELAY_SECONDS = 2.0

# Draft states that still count as "this person already has an email".
_LIVE_DRAFT_STATUSES = (
    EmailStatus.DRAFT,
    EmailStatus.APPROVED,
    EmailStatus.SCHEDULED,
    EmailStatus.SENT,
    EmailStatus.REPLIED,
)


def recipients_needing_drafts(db: Session, campaign_id: int) -> list[Contact]:
    """Campaign recipients that have no email written for them yet."""
    contacts = db.execute(
        select(Contact)
        .where(Contact.bulk_campaign_id == campaign_id)
        .order_by(Contact.id)
    ).scalars().all()
    drafted = set(
        db.execute(
            select(EmailDraft.contact_id).where(
                EmailDraft.bulk_campaign_id == campaign_id,
                EmailDraft.status.in_(_LIVE_DRAFT_STATUSES),
            )
        ).scalars().all()
    )
    return [c for c in contacts if c.id not in drafted]


def sender_context(campaign: BulkCampaign) -> dict:
    mailbox = resolve_mailbox(campaign.mailbox_id)
    return {
        "name": mailbox.from_name or mailbox.from_email,
        "email": mailbox.from_email,
    }


def campaign_signature(campaign: BulkCampaign) -> str:
    if (campaign.signature or "").strip():
        return campaign.signature.strip()
    return default_signature(sender_context(campaign)["name"])


def launch_drafting(campaign_id: int, *, regenerate: bool = False) -> None:
    """Start writing this campaign's emails in the background."""
    threading.Thread(
        target=_draft_all,
        args=(campaign_id, regenerate),
        name=f"bulk-draft-{campaign_id}",
        daemon=True,
    ).start()


def launch_sending(campaign_id: int, draft_ids: Optional[list[int]] = None) -> None:
    """Approve and send this campaign's reviewed drafts in the background."""
    threading.Thread(
        target=_send_all,
        args=(campaign_id, list(draft_ids) if draft_ids else None),
        name=f"bulk-send-{campaign_id}",
        daemon=True,
    ).start()


def recipient_payload(db: Session, contact: Contact) -> dict:
    """Everything the writer is allowed to know about one recipient."""
    company = db.get(Company, contact.company_id) if contact.company_id else None
    return {
        "name": contact.name,
        "email": contact.email,
        "title": contact.title,
        "company": company.name if company else None,
        "notes": contact.notes,
    }


def _draft_all(campaign_id: int, regenerate: bool) -> None:
    db = SessionLocal()
    try:
        campaign = db.get(BulkCampaign, campaign_id)
        if campaign is None:
            return
        purpose = (campaign.purpose or "").strip()
        if not purpose:
            _finish(db, campaign, BulkCampaignStatus.COLLECTING,
                    error="Tell the assistant what these emails should say first.")
            return

        if regenerate:
            _delete_unsent_drafts(db, campaign_id)
        pending = recipients_needing_drafts(db, campaign_id)
        if not pending:
            _finish(db, campaign, BulkCampaignStatus.READY)
            return

        sender = sender_context(campaign)
        signature = campaign_signature(campaign)
        mailbox_id = campaign.mailbox_id
        payloads = [(c.id, c.company_id, recipient_payload(db, c)) for c in pending]

        campaign.status = BulkCampaignStatus.DRAFTING
        campaign.progress_total = len(payloads)
        campaign.progress_done = 0
        campaign.cancel_requested = False
        campaign.last_error = None
        db.commit()

        written = 0
        failures: list[str] = []
        pool = ThreadPoolExecutor(max_workers=DRAFT_WORKERS)
        try:
            futures = {
                pool.submit(
                    build_email,
                    sender=sender,
                    purpose=purpose,
                    recipient=payload,
                    signature=signature,
                ): (contact_id, company_id, payload)
                for contact_id, company_id, payload in payloads
            }
            for future in as_completed(futures):
                contact_id, company_id, payload = futures[future]
                try:
                    subject, body = future.result()
                except Exception as exc:  # noqa: BLE001 - one bad draft must not stop the batch
                    logger.warning("Bulk draft failed for contact %s: %s", contact_id, exc)
                    failures.append(f"{payload.get('name') or contact_id}: {exc}")
                    continue
                db.add(
                    EmailDraft(
                        bulk_campaign_id=campaign_id,
                        contact_id=contact_id,
                        company_id=company_id,
                        subject=subject,
                        body=body,
                        status=EmailStatus.DRAFT,
                        from_mailbox=mailbox_id,
                    )
                )
                written += 1
                campaign.progress_done = written
                db.commit()
                if _cancelled(db, campaign):
                    break
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        if written:
            log_action(
                db,
                AuditAction.EMAIL_DRAFT,
                entity_type="bulk_campaign",
                entity_id=campaign_id,
                summary=f"Drafted {written} bulk email(s) for campaign {campaign_id}",
            )
        _finish(
            db,
            campaign,
            BulkCampaignStatus.READY,
            error="; ".join(failures[:5]) if failures else None,
        )
    except Exception as exc:  # noqa: BLE001 - never let the thread die silently
        logger.exception("Bulk drafting failed for campaign %s", campaign_id)
        _fail(db, campaign_id, str(exc))
    finally:
        db.close()


def _send_all(campaign_id: int, draft_ids: Optional[list[int]]) -> None:
    from app.api.routes.emails import SendError, perform_send

    db = SessionLocal()
    try:
        campaign = db.get(BulkCampaign, campaign_id)
        if campaign is None:
            return
        query = select(EmailDraft).where(
            EmailDraft.bulk_campaign_id == campaign_id,
            EmailDraft.status.in_([EmailStatus.DRAFT, EmailStatus.APPROVED]),
        )
        if draft_ids:
            query = query.where(EmailDraft.id.in_(draft_ids))
        drafts = db.execute(query.order_by(EmailDraft.id)).scalars().all()
        if not drafts:
            _finish(db, campaign, BulkCampaignStatus.READY)
            return

        campaign.status = BulkCampaignStatus.SENDING
        campaign.progress_total = len(drafts)
        campaign.progress_done = 0
        campaign.cancel_requested = False
        campaign.last_error = None
        db.commit()

        sent = 0
        failures: list[str] = []
        for index, draft in enumerate(drafts):
            if _cancelled(db, campaign):
                break
            if draft.status == EmailStatus.DRAFT:
                draft.status = EmailStatus.APPROVED
                draft.approved_by = "bulk-send"
                draft.approved_at = datetime.utcnow()
                db.commit()
            try:
                perform_send(db, draft)
                sent += 1
            except SendError as exc:
                db.rollback()
                contact = db.get(Contact, draft.contact_id) if draft.contact_id else None
                failures.append(f"{(contact.name if contact else draft.id)}: {exc.message}")
            except Exception as exc:  # noqa: BLE001 - keep sending the rest
                db.rollback()
                logger.exception("Bulk send failed for draft %s", draft.id)
                failures.append(f"Draft {draft.id}: {exc}")
            campaign.progress_done = index + 1
            db.commit()
            if index < len(drafts) - 1:
                time.sleep(SEND_DELAY_SECONDS)

        remaining = db.execute(
            select(EmailDraft).where(
                EmailDraft.bulk_campaign_id == campaign_id,
                EmailDraft.status.in_([EmailStatus.DRAFT, EmailStatus.APPROVED]),
            )
        ).scalars().first()
        _finish(
            db,
            campaign,
            BulkCampaignStatus.READY if remaining else BulkCampaignStatus.SENT,
            error=(
                f"{len(failures)} email(s) could not be sent: " + "; ".join(failures[:5])
                if failures
                else None
            ),
        )
        logger.info("Bulk campaign %s sent %s email(s)", campaign_id, sent)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bulk sending failed for campaign %s", campaign_id)
        _fail(db, campaign_id, str(exc))
    finally:
        db.close()


def _delete_unsent_drafts(db: Session, campaign_id: int) -> None:
    for draft in db.execute(
        select(EmailDraft).where(
            EmailDraft.bulk_campaign_id == campaign_id,
            EmailDraft.status.in_([EmailStatus.DRAFT, EmailStatus.APPROVED]),
        )
    ).scalars().all():
        db.delete(draft)
    db.commit()


def _cancelled(db: Session, campaign: BulkCampaign) -> bool:
    db.refresh(campaign)
    return bool(campaign.cancel_requested)


def _finish(
    db: Session,
    campaign: BulkCampaign,
    status: str,
    *,
    error: Optional[str] = None,
) -> None:
    campaign.status = status
    campaign.progress_total = 0
    campaign.progress_done = 0
    campaign.cancel_requested = False
    campaign.last_error = error
    db.commit()


def _fail(db: Session, campaign_id: int, message: str) -> None:
    db.rollback()
    campaign = db.get(BulkCampaign, campaign_id)
    if campaign is not None:
        _finish(db, campaign, BulkCampaignStatus.READY, error=message)
