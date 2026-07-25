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

from app.models.bulk_campaign import BulkCampaign, BulkChatMessage, BulkLookup
from app.models.company import Company
from app.models.contact import Contact
from app.models.enums import BulkCampaignStatus, BulkLookupStatus, ProspectStatus
from app.services.bulk_email.llm import complete_json, llm_available
from app.services.bulk_email.parser import (
    ParsedPerson,
    ParsedRecipient,
    extract_people,
    extract_recipients,
)
from app.services.bulk_email.runner import (
    launch_drafting,
    launch_lookup,
    lookups_pending,
    recipients_needing_drafts,
    sender_context,
)

logger = logging.getLogger(__name__)

# Pasted lists can be enormous; the intent call only needs a sample of them.
_INTENT_TEXT_LIMIT = 2500

_INTENT_SYSTEM = (
    "You are the assistant inside a bulk email tool. The user pastes lists of "
    "people (usually copied out of a spreadsheet), or just names them in "
    "conversation, and describes in plain language the email they want sent.\n\n"
    "Your only job is to interpret their latest message: maintain the campaign "
    "brief and pick out who they want to email. You never write or send the "
    "emails yourself; a separate step does that, and the app tells the user what "
    "happened. The app can search the web for an address the user does not have, "
    "so never ask them for an email address they have already said they lack.\n\n"
    "Return ONLY a single JSON object — never a bare array — of exactly this "
    'shape: {"reply": string, "purpose": string|null, "recipients": array, '
    '"signature": string|null, "action": string}\n\n'
    "The keys mean:\n"
    '- "reply": one or two short sentences to the user. NEVER claim you added '
    "recipients, wrote drafts, or sent anything. If the brief or the recipient "
    "list is still missing, ask for it. Otherwise confirm what you understood.\n"
    '- "purpose": the complete, updated brief describing what these emails should '
    "say, merging the existing brief with any new instruction. Write it as "
    "instructions for the email writer, keeping the user's context, tone and ask. "
    "Preserve the concrete details they gave exactly: when and where they met, "
    "event names, dates, and the specific ask. Null if this message added nothing "
    "about the content. Never put a recipient's name or employer in here (those "
    'belong in "recipients"), and never put the sender\'s name or sign-off in '
    "here (the app adds the signature).\n"
    '- "recipients": array of people this message names as someone to email '
    "WITHOUT giving their address, each as an object with \"name\", \"title\" and "
    '"company" (use null for anything not stated). Empty array if the message '
    "names nobody new. Never include the sender, the person signing the emails, "
    "or someone mentioned only as context (e.g. a colleague who made an "
    "introduction). If a message gives a correction such as \"the company is "
    'Acme", repeat the person with the corrected detail.\n'
    '- "signature": the exact sign-off the user asked to sign emails with, or null.\n'
    '- "action": "draft" if the user is asking for the emails to be written now, '
    '"lookup" if they are asking you to find the email addresses of people whose '
    'addresses they did not give, otherwise "none".'
)

_DRAFT_HINT_RE = re.compile(
    r"\b(draft|write|compose|generate|prepare)\b.{0,30}\b(email|emails|them|these|it)\b",
    re.IGNORECASE,
)
_LOOKUP_HINT_RE = re.compile(
    r"\b(find|look\s?up|search\s+for|get|source)\b.{0,30}"
    r"\b(email|emails|address|addresses|contact details)\b",
    re.IGNORECASE,
)
# Only a message that looks like a pasted list goes through the dedicated
# people-extraction call; people named in conversation come back from the intent
# call instead, which runs on every message anyway.
_MIN_LIST_LINES = 4
_MIN_LIST_CHARS = 200
# Adding a couple of people conversationally can start their lookup right away;
# a pasted crowd waits for an explicit go-ahead.
_AUTO_LOOKUP_MAX = 10


@dataclass
class Intent:
    reply: str = ""
    purpose: Optional[str] = None
    signature: Optional[str] = None
    action: str = "none"
    # People the message named as recipients without giving an address.
    recipients: list[ParsedPerson] = field(default_factory=list)


@dataclass
class ChatResult:
    reply: str
    recipients_added: int = 0
    duplicates: int = 0
    purpose_updated: bool = False
    drafting_started: bool = False
    # People captured from the paste who still have no address.
    needs_lookup: int = 0
    lookup_started: bool = False
    errors: list[str] = field(default_factory=list)


def handle_message(db: Session, campaign: BulkCampaign, message: str) -> ChatResult:
    """Apply one user message to the campaign and return what changed."""
    text = (message or "").strip()
    db.add(BulkChatMessage(campaign_id=campaign.id, role="user", content=text))
    db.commit()

    parsed = extract_recipients(text)
    added, duplicates = _save_recipients(db, campaign, parsed)

    intent = _interpret(db, campaign, text)
    needs_lookup = _save_people_needing_email(
        db, campaign, text, parsed, intent.recipients
    )
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
        needs_lookup=needs_lookup,
    )
    _maybe_start_lookup(db, campaign, text, intent, result)
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


def _save_people_needing_email(
    db: Session,
    campaign: BulkCampaign,
    text: str,
    with_email: list[ParsedRecipient],
    mentioned: list[ParsedPerson],
) -> int:
    """Queue everyone this message wants emailed but gives no address for.

    Covers both ways people arrive: named in conversation ("email Taha Qureshi
    at tekhqs") and pasted in bulk. Either way they become contacts with no
    address plus a lookup row, so they show up in the campaign immediately and
    can be searched for on demand.
    """
    # Paste extraction goes first: it keeps each person's own row as their
    # context, which is better than the whole message a mention carries.
    people = extract_people(text) if _looks_like_a_list(text) else []
    people += mentioned
    if not people:
        return 0

    seen = {_name_key(r.name) for r in with_email}
    sender = _name_key(sender_context(campaign).get("name") or "")
    if sender:
        seen.add(sender)
    for contact in db.execute(
        select(Contact).where(Contact.bulk_campaign_id == campaign.id)
    ).scalars().all():
        seen.add(_name_key(contact.name))

    added = 0
    for person in people:
        key = _name_key(person.name)
        if not key or key in seen:
            continue
        seen.add(key)
        contact = Contact(
            name=person.name,
            title=person.title,
            has_email=False,
            notes=person.notes or person.source_text or None,
            source="bulk_paste",
            bulk_campaign_id=campaign.id,
            company_id=_company_id_for(db, person.company) if person.company else None,
            status=ProspectStatus.APPROVED,
            approved_for_outreach=True,
        )
        db.add(contact)
        db.flush()
        db.add(
            BulkLookup(
                campaign_id=campaign.id,
                contact_id=contact.id,
                status=BulkLookupStatus.PENDING,
                source_text=person.source_text or person.name,
                # Carried over so the review screen can warn before a credit is
                # spent chasing a name the user already marked as approximate.
                reason=(
                    "The pasted list flags this spelling as approximate."
                    if person.uncertain_name
                    else None
                ),
            )
        )
        added += 1
    db.commit()
    return added


def _looks_like_a_list(text: str) -> bool:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return len(lines) >= _MIN_LIST_LINES and len(text) >= _MIN_LIST_CHARS


def _awaiting_names(db: Session, campaign_id: int, limit: int = 12) -> str:
    """Who is already queued for a lookup, so the assistant doesn't re-ask."""
    pending = lookups_pending(db, campaign_id)
    if not pending:
        return "(nobody)"
    names = [
        (db.get(Contact, lookup.contact_id).name or "?") for lookup in pending[:limit]
    ]
    more = len(pending) - len(names)
    return ", ".join(names) + (f", and {more} more" if more > 0 else "")


def _people_count(db: Session, campaign_id: int) -> int:
    return db.execute(
        select(func.count())
        .select_from(Contact)
        .where(Contact.bulk_campaign_id == campaign_id)
    ).scalar_one()


def _name_key(name: str) -> str:
    return " ".join(re.split(r"[^a-z]+", (name or "").lower())).strip()


def _company_id_for(db: Session, name: str) -> Optional[int]:
    """Reuse an existing organization by name, or record the pasted one.

    Company matching is best-effort: a corrupted organizations table must not
    take the whole chat turn down when we already know the person's name.
    """
    label = (name or "").strip()
    if not label:
        return None
    normalized = label.lower()
    try:
        # SAVEPOINT so a failed company lookup cannot unwind contacts already
        # staged in this same turn.
        with db.begin_nested():
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
    except Exception:
        logger.exception("Could not attach company %r — continuing without it", label)
        return None


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
        f"SENDING AS: {sender_context(campaign).get('name')}\n"
        f"RECIPIENTS ALREADY ADDED: {recipients}\n"
        f"ALREADY AWAITING AN ADDRESS: {_awaiting_names(db, campaign.id)}\n"
        f"CURRENT BRIEF: {(campaign.purpose or '(none yet)').strip()}\n\n"
        f"USER MESSAGE:\n{_trim(text)}",
        max_tokens=1024,
    )
    if isinstance(data, list):
        # Asked for several keys at once, the model sometimes answers with just
        # the recipients array. Keep those and fall back for the rest.
        intent = _heuristic_intent(campaign, text)
        intent.recipients = _mentioned_people(data, text)
        return intent
    if not isinstance(data, dict):
        return _heuristic_intent(campaign, text)
    action = str(data.get("action") or "none").strip().lower()
    return Intent(
        reply=str(data.get("reply") or "").strip(),
        purpose=_optional(data.get("purpose")),
        signature=_optional(data.get("signature")),
        action=action if action in ("draft", "lookup") else "none",
        recipients=_mentioned_people(data.get("recipients"), text),
    )


def _mentioned_people(value: object, text: str) -> list[ParsedPerson]:
    """People the model picked out of a conversational message."""
    if not isinstance(value, list):
        return []
    people: list[ParsedPerson] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _optional(item.get("name"))
        if not name:
            continue
        people.append(
            ParsedPerson(
                name=name,
                title=_optional(item.get("title")),
                company=_optional(item.get("company")),
                # The sentence they came from is the only context the writer and
                # the review screen have for someone named in passing.
                source_text=text[:400],
            )
        )
    return people


def _heuristic_intent(campaign: BulkCampaign, text: str) -> Intent:
    """Keyword fallback so the chat still works without the model."""
    wants_draft = bool(_DRAFT_HINT_RE.search(text))
    wants_lookup = bool(_LOOKUP_HINT_RE.search(text))
    # Anything that isn't part of the pasted list is treated as the brief, unless
    # the whole message was just the instruction to start drafting.
    prose = "\n".join(
        line for line in text.splitlines() if line.strip() and not _is_table_row(line)
    ).strip()
    if (wants_draft or wants_lookup) and len(prose) < 40:
        prose = ""
    merged = "\n\n".join(p for p in [(campaign.purpose or "").strip(), prose] if p)
    if wants_draft:
        action = "draft"
    elif wants_lookup:
        action = "lookup"
    else:
        action = "none"
    return Intent(reply="", purpose=merged or None, signature=None, action=action)


def _is_table_row(line: str) -> bool:
    """True for pasted spreadsheet rows and their header, which are not a brief."""
    return "@" in line or "\t" in line or len(line.split("|")) > 2


def _maybe_start_lookup(
    db: Session, campaign: BulkCampaign, text: str, intent: Intent, result: ChatResult
) -> None:
    if intent.action != "lookup":
        return
    if campaign.status == BulkCampaignStatus.LOOKING_UP:
        result.errors.append("I'm already out searching for the missing addresses.")
        return
    if _busy(campaign):
        result.errors.append("A job is already running for this campaign.")
        return
    pending = lookups_pending(db, campaign.id)
    if not pending:
        result.errors.append(
            "There's nobody waiting on an address."
            if _people_count(db, campaign.id)
            else "Tell me who to email first — a name and where they work is enough."
        )
        return
    # Searching costs money per person, so a big queue is confirmed rather than
    # started off the back of a message that merely mentioned addresses.
    if len(pending) > _AUTO_LOOKUP_MAX and not _LOOKUP_HINT_RE.search(text):
        result.errors.append(
            f"That's {len(pending)} people to look up. Say 'find their emails' "
            "and I'll start searching."
        )
        return
    campaign.status = BulkCampaignStatus.LOOKING_UP
    campaign.last_error = None
    db.commit()
    launch_lookup(campaign.id)
    result.lookup_started = True


def _busy(campaign: BulkCampaign) -> bool:
    return campaign.status in (
        BulkCampaignStatus.DRAFTING,
        BulkCampaignStatus.SENDING,
        BulkCampaignStatus.LOOKING_UP,
    )


def _maybe_start_drafting(
    db: Session, campaign: BulkCampaign, intent: Intent, result: ChatResult
) -> None:
    if intent.action != "draft":
        return
    if _busy(campaign) or result.lookup_started:
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
    if result.needs_lookup:
        facts.append(
            f"Added {result.needs_lookup} "
            f"{'person' if result.needs_lookup == 1 else 'people'} with no email "
            "address yet."
        )
    if result.purpose_updated:
        facts.append("Saved the brief for these emails.")
    if (
        "@" in text
        and not result.recipients_added
        and not result.duplicates
        and not result.needs_lookup
    ):
        facts.append(
            "I couldn't read any contact rows out of that. Each row needs an email address."
        )

    if result.lookup_started:
        facts.append(
            "Searching the web for their addresses now. Nothing is emailed to "
            "them until you approve each one."
        )
    elif result.drafting_started:
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
        .where(
            Contact.bulk_campaign_id == campaign.id,
            Contact.email.is_not(None),
            Contact.email != "",
        )
    ).scalar_one()
    awaiting = len(lookups_pending(db, campaign.id))
    has_purpose = bool((campaign.purpose or "").strip())
    if campaign.status == BulkCampaignStatus.LOOKING_UP:
        return (
            "The addresses I find will appear under 'Needs email' for you to "
            "approve before anything is drafted."
        )
    if awaiting:
        one = awaiting == 1
        found = "find their email" if one else "find their emails"
        return (
            f"Say '{found}' and I'll search the web for "
            f"{'that missing address' if one else f'those {awaiting} missing addresses'}"
            " for you to approve."
        )
    if not recipients:
        who = (
            "Tell me who to email — paste a list, or just give me a name and where "
            "they work and I'll find the address."
        )
        return who if has_purpose else f"{who} Then tell me what the email should say."
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
