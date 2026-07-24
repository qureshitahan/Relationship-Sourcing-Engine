"""Turn a pasted block of text into structured recipients.

The user copies rows out of a spreadsheet and drops them into the campaign chat,
so the input is unpredictable: tab separated, comma separated, "Name <email>",
or prose with addresses sprinkled through it. The LLM handles that variety well,
but it must never invent a recipient — so every returned row is checked against
the email addresses that literally appear in the pasted text, and any address
the model missed falls back to a deterministic split of its own line.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from app.services.bulk_email.llm import complete_json

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Keep each extraction call small so the JSON always fits in the token budget.
_ROWS_PER_CALL = 20
_CELL_SPLIT_RE = re.compile(r"\t|,|;|\||\s{2,}")

_EXTRACT_SYSTEM = (
    "You extract contact rows from text a user pasted out of a spreadsheet, CRM "
    "export, or email thread.\n\n"
    "Return ONLY a JSON array. One object per person, with keys:\n"
    '  "name" (full name; if the text has no name, leave it empty),\n'
    '  "email" (copy it EXACTLY as it appears in the text),\n'
    '  "title" (job title, or null),\n'
    '  "company" (employer, or null),\n'
    '  "notes" (any other useful context about this person from their row, such '
    'as where they were met, their focus, or a comment; null if there is none).\n\n'
    "HARD RULES:\n"
    "- Never invent a person or an email address. Only use addresses present in the text.\n"
    "- Skip header rows, totals, and any row without an email address.\n"
    "- Do not swap columns: a value that looks like a job title is a title, not a name.\n"
    "- Return one object per email address, in the order they appear."
)


@dataclass
class ParsedRecipient:
    name: str
    email: str
    title: Optional[str] = None
    company: Optional[str] = None
    notes: Optional[str] = None


def find_emails(text: str) -> list[str]:
    """Every email address in ``text``, de-duplicated, in order of appearance."""
    seen: set[str] = set()
    out: list[str] = []
    for match in EMAIL_RE.findall(text or ""):
        key = match.lower()
        if key not in seen:
            seen.add(key)
            out.append(match)
    return out


def extract_recipients(text: str) -> list[ParsedRecipient]:
    """Parse pasted text into recipients. Returns [] when it holds no addresses."""
    lines = [ln for ln in (text or "").splitlines() if EMAIL_RE.search(ln)]
    if not lines:
        return []

    by_email: dict[str, ParsedRecipient] = {}
    for chunk in _chunks(lines, _ROWS_PER_CALL):
        for recipient in _parse_chunk(chunk):
            key = recipient.email.lower()
            if key not in by_email:
                by_email[key] = recipient
    return list(by_email.values())


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _parse_chunk(lines: list[str]) -> list[ParsedRecipient]:
    """LLM-parse a chunk, then fill any address it dropped from the raw line."""
    expected = {e.lower(): e for e in find_emails("\n".join(lines))}
    parsed = _llm_rows(lines, expected)
    covered = {r.email.lower() for r in parsed}
    for line in lines:
        for email in find_emails(line):
            if email.lower() not in covered:
                parsed.append(_row_from_line(line, email))
                covered.add(email.lower())
    return parsed


def _llm_rows(lines: list[str], expected: dict[str, str]) -> list[ParsedRecipient]:
    data = complete_json(
        _EXTRACT_SYSTEM,
        "Extract the contacts from this text:\n\n" + "\n".join(lines),
        max_tokens=4096,
    )
    if not isinstance(data, list):
        return []
    out: list[ParsedRecipient] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        email = str(item.get("email") or "").strip()
        # Guard against hallucinated or reformatted addresses.
        if email.lower() not in expected:
            continue
        out.append(
            ParsedRecipient(
                name=_clean(item.get("name")) or name_from_email(email),
                email=expected[email.lower()],
                title=_clean(item.get("title")) or None,
                company=_clean(item.get("company")) or None,
                notes=_clean(item.get("notes")) or None,
            )
        )
    return out


def _row_from_line(line: str, email: str) -> ParsedRecipient:
    """Deterministic fallback: split the row on its delimiters, in column order."""
    rest = line.replace(email, " ")
    cells = [
        cell.strip(" \t|,;:\"'<>()")
        for cell in _CELL_SPLIT_RE.split(rest)
        if cell.strip(" \t|,;:\"'<>()")
    ]
    # Drop spreadsheet row numbers and other bare numeric cells.
    cells = [c for c in cells if not c.isdigit()]
    if cells and _looks_like_name(cells[0]):
        name, remaining = cells[0], cells[1:]
    else:
        name, remaining = name_from_email(email), cells
    return ParsedRecipient(
        name=name,
        email=email,
        title=remaining[0] if len(remaining) > 0 else None,
        company=remaining[1] if len(remaining) > 1 else None,
        notes=", ".join(remaining[2:]) if len(remaining) > 2 else None,
    )


def _looks_like_name(value: str) -> bool:
    text = value.strip()
    if not text or len(text) > 60 or any(ch.isdigit() for ch in text):
        return False
    return bool(re.fullmatch(r"[A-Za-z.'\-]+(?:\s+[A-Za-z.'\-]+){0,3}", text))


def name_from_email(email: str) -> str:
    """Best-effort display name from an address ('d.salomon@x.com' -> 'D Salomon')."""
    local = (email or "").split("@", 1)[0]
    parts = [p for p in re.split(r"[._\-+]+", local) if p and not p.isdigit()]
    if not parts:
        return email
    return " ".join(p.capitalize() for p in parts)


def _clean(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in ("", "none", "null", "n/a", "-") else text
