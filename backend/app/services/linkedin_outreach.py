"""Generate LinkedIn outreach content (direct message + connection note).

Reuses the proven email outreach generator (grounded in the same insight and
principal proof points), then adapts the copy for LinkedIn: a signature-free
direct message and a short connection-invitation note (<= ~280 chars).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.contact import Contact
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.services.insights.engine import generate_outreach

# Sign-offs we strip from the email body so a LinkedIn DM reads natively.
_CLOSERS = {
    "best", "best regards", "regards", "warm regards", "kind regards",
    "sincerely", "thanks", "thank you", "cheers", "warmly", "all the best",
    "talk soon", "looking forward",
}

INVITE_NOTE_LIMIT = 280  # LinkedIn allows 300; keep headroom.


@dataclass
class LinkedInContent:
    body: str
    invitation_note: str


def _strip_signature(body: str, principal: Principal) -> str:
    """Remove a trailing email signature/closer block from a message body."""
    if not body:
        return ""
    name = (principal.name or "").strip().lower()
    first = name.split()[0] if name else ""
    lines = body.rstrip().split("\n")
    while lines:
        last = lines[-1].strip()
        normalized = last.lower().rstrip(",.").strip()
        is_url = last.lower().startswith("http")
        is_contact = "@" in last or any(ch.isdigit() for ch in last) and len(last) < 40
        is_name = bool(name) and (normalized == name or (first and normalized == first))
        is_closer = normalized in _CLOSERS
        if last == "" or is_url or is_name or is_closer or is_contact:
            lines.pop()
            continue
        break
    return "\n".join(lines).rstrip()


def _first_sentences(text: str, limit: int) -> str:
    """Take whole sentences up to ``limit`` characters (never mid-word)."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    out = ""
    for chunk in flat.replace("? ", "?|").replace("! ", "!|").replace(". ", ".|").split("|"):
        candidate = (out + " " + chunk).strip() if out else chunk
        if len(candidate) > limit:
            break
        out = candidate
    if not out:  # first sentence already too long — hard cap on a word boundary.
        out = flat[:limit].rsplit(" ", 1)[0]
    return out.strip()


def generate_linkedin_content(
    db: Session,
    principal: Principal,
    contact: Optional[Contact],
    company: Optional[Company],
    insight: Optional[RelevanceInsight],
    *,
    outreach_goal: Optional[str] = None,
) -> LinkedInContent:
    """Build a LinkedIn DM and a short invitation note for a prospect."""
    result = generate_outreach(
        db, principal, contact, company, insight, outreach_goal=outreach_goal
    )
    body = _strip_signature(result.body, principal).strip()

    first_name = ""
    if contact and contact.name:
        first_name = contact.name.strip().split()[0]

    # Connection note: a warm one/two-line intro derived from the message.
    core = _first_sentences(body, INVITE_NOTE_LIMIT - (len(first_name) + 8))
    if first_name and not core.lower().startswith(("hi ", "hello ", "hey ")):
        note = f"Hi {first_name}, {core}"
    else:
        note = core
    note = note[:INVITE_NOTE_LIMIT].strip()

    return LinkedInContent(body=body, invitation_note=note)
