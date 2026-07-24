"""The bulk campaign chat: read a message, update the campaign, answer honestly.

One user turn can do several things at once ("here are 100 people I met at FTA
<paste>, tell them it was great meeting them, draft it"). So each message is
handled in three passes: pull out any recipients, ask the model what the person
is actually instructing, then apply it.

The assistant's reply is assembled here rather than taken wholesale from the
model: the model contributes the conversational sentence, and this module
appends the facts about what really changed, so the chat can never claim it
added recipients or drafted emails when it did not.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bulk_campaign import BulkCampaign, BulkChatMessage
from app.models.company import Company
from app.models.contact import Contact
from app.models.enums import BulkCampaignStatus, ProspectStatus
from app.services.bulk_email.llm import complete_json, llm_available
from app.services.bulk_email.parser import ParsedRecipient, extract_recipients
from app.services.bulk_email.runner import launch_drafting, recipients_needing_drafts

logger = logging.getLogger(__name__)

# Pasted lists can be enormous; the intent call only needs a sample of them.
_INTENT_TEXT_LIMIT = 2500

_INTENT_SYSTEM = (
    "You are the assistant inside a bulk email tool. The user pastes lists of "
    "people (usually copied out of a spreadsheet) and describes, in plain "
    "language, the email they want sent to all of them.\n\n"
    "Your only job is to interpret their latest message and maintain the campaign "
    "brief. You never write or send the emails yourself; a separate step does "
    "that, and the app tells the user what happened.\n\n"
    "Return ONLY JSON with these keys:\n"
    '- "reply": one or two short sentences to the user. NEVER claim you added '
    "recipients, wrote drafts, or sent anything. If the brief or the recipient "
    "list is still missing, ask for it. Otherwise confirm what you understood.\n"
    '- "purpose": the complete, updated brief describing what these emails should '
    "say, merging the existing brief with any new instruction. Write it as "
    "instructions for the email writer, keeping the user's context, tone and ask. "
    "Preserve the concrete details they gave exactly: when and where they met, "
    "event names, dates, and the specific ask. Null if this message added nothing "
    "about the content.\n"
    '- "signature": the exact sign-off the user asked to sign emails with, or null.\n'
    '- "action": "draft" if the user is asking for the emails to be written now, '
    'otherwise "none".'
)

_DRAFT_HINT_RE = re.compile(
    r"\b(draft|write|compose|generate|prepare)\b.{0,30}\b(email|emails|them|these|it)\b",
    re.IGNORECASE,
)


@dataclass
class Intent:
    reply: str = ""
    purpose: Optional[str] = None
    signature: Optional[str] = None
    action: str = "none"


@dataclass
class ChatResult:
    reply: str
    recipients_added: int = 0
    duplicates: int = 0
    purpose_updated: bool = False
    drafting_started: bool = False
    errors: list[str] = field(default_factory=list)


def handle_message(db: Session, campaign: BulkCampaign, message: str) -> ChatResult:
    """Apply one user message to the campaign and return what changed."""
    text = (message or "").strip()
    db.add(BulkChatMessage(campaign_id=campaign.id, role="user", content=text))
    db.commit()

    parsed = extract_recipients(text)
    added, duplicates = _save_recipients(db, campaign, parsed)

    intent = _interpret(db, campaign, text)
    purpose_updated = False
    if intent.purpose and intent.purpose.strip() != (campaign.purpose or "").strip():
        campaign.purpose = intent.purpose.strip()
        purpose_updated = True
    if intent.signature:
        campaign.signature = intent.signature.strip()
    db.commit()

    result = ChatResult(
        reply=intent.reply,
        recipients_added=added,
        duplicates=duplicates,
        purpose_updated=purpose_updated,
    )
    _maybe_start_drafting(db, campaign, intent, result)
    result.reply = _compose_reply(db, campaign, text, intent, result)

    db.add(
        BulkChatMessage(
            campaign_id=campaign.id,
            role="assistant",
            content=result.reply,
            meta={
                "recipients_added": result.recipients_added,
                "duplicates": result.duplicates,
                "purpose_updated": result.purpose_updated,
                "drafting_started": result.drafting_started,
            },
        )
    )
    db.commit()
    return result


def _save_recipients(
    db: Session, campaign: BulkCampaign, parsed: list[ParsedRecipient]
) -> tuple[int, int]:
    """Create a Contact per new address; existing ones are enriched, not cloned."""
    if not parsed:
        return 0, 0
    existing = {
        (c.email or "").lower(): c
        for c in db.execute(
            select(Contact).where(Contact.bulk_campaign_id == campaign.id)
        ).scalars().all()
    }
    added = 0
    duplicates = 0
    for row in parsed:
        key = row.email.lower()
        current = existing.get(key)
        if current is not None:
            duplicates += 1
            # A later paste may carry detail the first one lacked.
            current.title = current.title or row.title
            current.notes = current.notes or row.notes
            if row.company and not current.company_id:
                current.company_id = _company_id_for(db, row.company)
            continue
        contact = Contact(
            name=row.name or row.email,
            title=row.title,
            email=row.email,
            has_email=True,
            email_status="provided",
            notes=row.notes,
            source="bulk_paste",
            bulk_campaign_id=campaign.id,
            company_id=_company_id_for(db, row.company) if row.company else None,
            status=ProspectStatus.APPROVED,
            approved_for_outreach=True,
        )
        db.add(contact)
        existing[key] = contact
        added += 1
    db.commit()
    return added, duplicates


def _company_id_for(db: Session, name: str) -> Optional[int]:
    """Reuse an existing organization by name, or record the pasted one."""
    label = (name or "").strip()
    if not label:
        return None
    normalized = label.lower()
    company = db.execute(
        select(Company).where(Company.normalized_name == normalized)
    ).scalars().first()
    if company is None:
        company = Company(
            name=label,
            normalized_name=normalized,
            enrichment_source="bulk_paste",
        )
        db.add(company)
        db.flush()
    return company.id


def _interpret(db: Session, campaign: BulkCampaign, text: str) -> Intent:
    if not llm_available():
        return _heuristic_intent(campaign, text)
    recipients = db.execute(
        select(func.count())
        .select_from(Contact)
        .where(Contact.bulk_campaign_id == campaign.id)
    ).scalar_one()
    data = complete_json(
        _INTENT_SYSTEM,
        f"CAMPAIGN: {campaign.name}\n"
        f"RECIPIENTS ALREADY ADDED: {recipients}\n"
        f"CURRENT BRIEF: {(campaign.purpose or '(none yet)').strip()}\n\n"
        f"USER MESSAGE:\n{_trim(text)}",
        max_tokens=1024,
    )
    if not isinstance(data, dict):
        return _heuristic_intent(campaign, text)
    action = str(data.get("action") or "none").strip().lower()
    return Intent(
        reply=str(data.get("reply") or "").strip(),
        purpose=_optional(data.get("purpose")),
        signature=_optional(data.get("signature")),
        action="draft" if action == "draft" else "none",
    )


def _heuristic_intent(campaign: BulkCampaign, text: str) -> Intent:
    """Keyword fallback so the chat still works without the model."""
    wants_draft = bool(_DRAFT_HINT_RE.search(text))
    # Anything that isn't part of the pasted list is treated as the brief, unless
    # the whole message was just the instruction to start drafting.
    prose = "\n".join(
        line for line in text.splitlines() if line.strip() and not _is_table_row(line)
    ).strip()
    if wants_draft and len(prose) < 40:
        prose = ""
    merged = "\n\n".join(p for p in [(campaign.purpose or "").strip(), prose] if p)
    return Intent(
        reply="",
        purpose=merged or None,
        signature=None,
        action="draft" if wants_draft else "none",
    )


def _is_table_row(line: str) -> bool:
    """True for pasted spreadsheet rows and their header, which are not a brief."""
    return "@" in line or "\t" in line or len(line.split("|")) > 2


def _maybe_start_drafting(
    db: Session, campaign: BulkCampaign, intent: Intent, result: ChatResult
) -> None:
    if intent.action != "draft":
        return
    if campaign.status in (BulkCampaignStatus.DRAFTING, BulkCampaignStatus.SENDING):
        result.errors.append("A job is already running for this campaign.")
        return
    if not (campaign.purpose or "").strip():
        result.errors.append("I still need to know what the emails should say.")
        return
    if not recipients_needing_drafts(db, campaign.id):
        result.errors.append("Every recipient already has an email drafted.")
        return
    # Flip the status before handing off so the response never reports the
    # campaign as idle while the job is spinning up.
    campaign.status = BulkCampaignStatus.DRAFTING
    campaign.last_error = None
    db.commit()
    launch_drafting(campaign.id)
    result.drafting_started = True


def _compose_reply(
    db: Session,
    campaign: BulkCampaign,
    text: str,
    intent: Intent,
    result: ChatResult,
) -> str:
    """The model's sentence, followed by exactly what the app actually did."""
    lines: list[str] = []
    if intent.reply:
        lines.append(intent.reply)

    facts: list[str] = []
    if result.recipients_added:
        facts.append(
            f"Added {result.recipients_added} recipient"
            f"{'s' if result.recipients_added != 1 else ''}."
        )
    if result.duplicates:
        facts.append(f"Skipped {result.duplicates} already on the list.")
    if result.purpose_updated:
        facts.append("Saved the brief for these emails.")
    if "@" in text and not result.recipients_added and not result.duplicates:
        facts.append(
            "I couldn't read any contact rows out of that. Each row needs an email address."
        )

    if result.drafting_started:
        facts.append("Writing the drafts now, they'll appear below as they're ready.")
    else:
        facts.extend(result.errors)
        facts.append(_next_step(db, campaign))

    lines.extend(f for f in facts if f)
    return "\n".join(lines).strip() or "Got it."


def _next_step(db: Session, campaign: BulkCampaign) -> str:
    recipients = db.execute(
        select(func.count())
        .select_from(Contact)
        .where(Contact.bulk_campaign_id == campaign.id)
    ).scalar_one()
    has_purpose = bool((campaign.purpose or "").strip())
    if not recipients and not has_purpose:
        return "Paste your list of people, then tell me what the email should say."
    if not recipients:
        return "Paste the people you want to email (each row needs an email address)."
    if not has_purpose:
        return "Now tell me what you want to say to them."
    pending = len(recipients_needing_drafts(db, campaign.id))
    if pending:
        return f"Say 'draft the emails' when you're ready and I'll write all {pending}."
    return "All drafts are written, review them below."


def _trim(text: str) -> str:
    if len(text) <= _INTENT_TEXT_LIMIT:
        return text
    omitted = len(text) - _INTENT_TEXT_LIMIT
    return f"{text[:_INTENT_TEXT_LIMIT]}\n... [{omitted} more characters of pasted rows omitted]"


def _optional(value: object) -> Optional[str]:
    text = str(value or "").strip()
    return text or None
