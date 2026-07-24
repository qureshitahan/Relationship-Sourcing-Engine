"""Write one bulk email per recipient from the campaign brief.

Agent-campaign drafting is grounded in a researched RelevanceInsight. Bulk
campaigns have none: all the model gets is the sender, the brief the user typed
in the chat, and whatever the pasted row said about the person. So the prompt is
built the other way round — say the brief in the sender's voice, and personalize
only with facts that were actually pasted.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.services.bulk_email.llm import complete_json, llm_available

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You write short, personal emails that a busy professional sends to people "
    "they know of or have just met. You are writing AS the SENDER.\n\n"
    "VOICE: first person ('I', 'my'). Warm, direct, human. Never corporate, never "
    "salesy, never flattering.\n\n"
    "WHAT TO SAY: the CAMPAIGN BRIEF is the message the sender wants delivered to "
    "everyone. Say exactly that, in the sender's voice. Do not add offers, claims, "
    "meetings, or details the brief does not mention.\n\n"
    "PERSONALIZATION: use only what RECIPIENT actually contains (their name, title, "
    "company, notes). If a field is missing, write around it. NEVER guess where "
    "someone works, what they do, or where you met them.\n\n"
    "BODY STRUCTURE (3 to 5 short lines, under 120 words total):\n"
    "1) 'Hi <FirstName>,'\n"
    "2) The reason for writing, from the brief, made specific to this person where "
    "the recipient data honestly allows it.\n"
    "3) Optional: one line of substance from the brief.\n"
    "4) One clear, low-friction closing line or question.\n\n"
    "HARD RULES:\n"
    "- Never state when or where you met someone unless the brief says so, and use "
    "the brief's own timing (do not turn 'yesterday' into 'last week').\n"
    "- Do NOT add a sign-off, name, or signature; that is appended afterwards.\n"
    "- Never use em or en dashes. No markdown, no bullet points, no links.\n"
    "- Do not open with 'I hope this finds you well', 'I wanted to reach out', or "
    "'My name is'.\n"
    "- Subject: under 60 characters, specific, no clickbait, no 'Re:'.\n\n"
    'Respond with ONLY JSON: {"subject": "...", "body": "..."}.'
)

_BANNED_OPENERS = (
    "i hope this finds you well",
    "i hope this email finds you well",
    "hope you are well",
    "hope you're well",
    "i wanted to reach out",
    "i am reaching out",
    "i'm reaching out",
)

_CLOSERS = {
    "thanks",
    "thank you",
    "best",
    "best regards",
    "regards",
    "warm regards",
    "kind regards",
    "cheers",
    "sincerely",
    "all the best",
    "warmly",
    "talk soon",
}


def build_email(
    *,
    sender: dict,
    purpose: str,
    recipient: dict,
    signature: str,
) -> tuple[str, str]:
    """Return ``(subject, body)`` for one recipient, signature already applied."""
    subject = ""
    body = ""
    if llm_available():
        data = complete_json(
            _SYSTEM,
            "SENDER:\n"
            + json.dumps(sender, indent=2, default=str)
            + "\n\nCAMPAIGN BRIEF (what this email must say):\n"
            + purpose.strip()
            + "\n\nRECIPIENT:\n"
            + json.dumps(_compact(recipient), indent=2, default=str)
            + '\n\nReturn JSON with "subject" and "body".',
            max_tokens=1024,
        )
        if isinstance(data, dict):
            subject = clean_subject(data.get("subject"), recipient=recipient)
            body = clean_body(data.get("body"))
    if not body:
        subject, body = _fallback_email(purpose, recipient)
    return subject, apply_signature(body, signature)


def _fallback_email(purpose: str, recipient: dict) -> tuple[str, str]:
    """Plain mail-merge used when the model is unavailable, so nothing is lost."""
    first = first_name(recipient.get("name"))
    greeting = f"Hi {first}," if first else "Hi,"
    return (
        clean_subject(None, recipient=recipient),
        f"{greeting}\n\n{purpose.strip()}",
    )


def clean_body(body: object) -> str:
    """Strip dashes, filler openers, and any sign-off the model added anyway."""
    text = str(body or "").replace("—", ", ").replace("–", ", ")
    lines = [ln.strip() for ln in text.splitlines()]
    cleaned: list[str] = []
    for line in lines:
        low = line.lower().rstrip(".,!")
        if any(low.startswith(banned) for banned in _BANNED_OPENERS):
            continue
        cleaned.append(line)
    # Collapse runs of blank lines, then drop leading/trailing ones.
    out: list[str] = []
    for line in cleaned:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out).strip()


def clean_subject(subject: object, *, recipient: Optional[dict] = None) -> str:
    text = str(subject or "").replace("—", " ").replace("–", " ").replace("!", "")
    text = text.strip().strip('"').strip("'")
    for prefix in ("subject:", "re:", "fwd:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].strip()
    if not text:
        first = first_name((recipient or {}).get("name"))
        text = f"A quick note{f' for {first}' if first else ''}"
    return text[:70].strip()


def apply_signature(body: str, signature: str) -> str:
    """Replace whatever sign-off the body ends with by the campaign signature.

    Idempotent: trailing blank/closer/signature lines are stripped first, so
    re-drafting or re-applying never stacks two sign-offs.
    """
    signature = (signature or "").strip()
    lines = (body or "").rstrip().split("\n")
    signature_lines = {ln.strip().lower() for ln in signature.split("\n") if ln.strip()}
    while lines:
        last = lines[-1].strip()
        normalized = last.lower().rstrip(",").strip()
        if (
            not last
            or normalized in _CLOSERS
            or last.lower() in signature_lines
            or normalized in signature_lines
        ):
            lines.pop()
            continue
        break
    cleaned = "\n".join(lines).rstrip()
    if not signature:
        return cleaned
    return f"{cleaned}\n\n{signature}".strip()


def default_signature(sender_name: str) -> str:
    name = (sender_name or "").strip()
    return f"Thanks,\n{name}" if name else "Thanks,"


def first_name(name: object) -> str:
    text = str(name or "").strip()
    return text.split()[0] if text else ""


def _compact(data: dict) -> dict:
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}
