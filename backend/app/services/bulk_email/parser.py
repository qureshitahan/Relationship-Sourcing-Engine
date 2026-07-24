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


_PEOPLE_SYSTEM = (
    "You extract people from a list a user pasted: a conference roster, meeting "
    "minutes, an attendee table, or a spreadsheet. Rows often wrap across lines, "
    "with a person's name on one line and their role on the next.\n\n"
    "Return ONLY a JSON array. One object per person, with keys:\n"
    '  "name" (their name, copied from the text),\n'
    '  "title" (their role or job title, or null),\n'
    '  "company" (their employer or organization, or null),\n'
    '  "notes" (the rest of what the text says about them, or null),\n'
    '  "uncertain_name" (true if the text itself flags the spelling as '
    'approximate or offers alternative spellings, e.g. "(approx.)" or '
    '"Smith/Smyth" — otherwise false).\n\n'
    "HARD RULES:\n"
    "- Never invent a person. Copy names from the text exactly as written, "
    "minus any parenthetical spelling hints.\n"
    "- Skip section headings, column headers, counts, and commentary that is "
    "not about one specific person.\n"
    "- A person can appear under a section heading such as an organization or a "
    "session name: use that heading for company or notes when it applies.\n"
    "- Return them in the order they appear."
)
# The roster style above wraps one person over several lines, so people are
# extracted from windows of lines rather than line by line.
_LINES_PER_PEOPLE_CALL = 60


@dataclass
class ParsedRecipient:
    name: str
    email: str
    title: Optional[str] = None
    company: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class ParsedPerson:
    """Someone named in the paste who has no email address in it."""

    name: str
    title: Optional[str] = None
    company: Optional[str] = None
    notes: Optional[str] = None
    uncertain_name: bool = False
    source_text: str = ""


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


def extract_people(text: str) -> list[ParsedPerson]:
    """Parse people the text names but gives no address for.

    Lines carrying an address belong to :func:`extract_recipients`; everything
    else is offered to the lookup queue instead of being silently dropped.
    """
    lines = [
        ln.rstrip()
        for ln in (text or "").splitlines()
        if ln.strip() and not EMAIL_RE.search(ln)
    ]
    if not lines:
        return []

    by_name: dict[str, ParsedPerson] = {}
    for chunk in _chunks(lines, _LINES_PER_PEOPLE_CALL):
        for person in _people_from_chunk(chunk):
            key = _name_key(person.name)
            if key and key not in by_name:
                by_name[key] = person
    return list(by_name.values())


def _people_from_chunk(lines: list[str]) -> list[ParsedPerson]:
    data = complete_json(
        _PEOPLE_SYSTEM,
        "Extract the people from this text:\n\n" + "\n".join(lines),
        max_tokens=8192,
    )
    if not isinstance(data, list):
        return []
    blob = "\n".join(lines)
    rows = [item for item in data if isinstance(item, dict)]
    names = [
        name
        for name in (_clean(item.get("name")) for item in rows)
        # Anti-hallucination: the name has to be in the text it came from.
        if name and _is_person_name(name) and _mentioned(name, blob)
    ]
    out: list[ParsedPerson] = []
    for item in rows:
        name = _clean(item.get("name"))
        if name not in names:
            continue
        out.append(
            ParsedPerson(
                name=name,
                title=_clean(item.get("title")) or None,
                company=_clean(item.get("company")) or None,
                notes=_clean(item.get("notes")) or None,
                uncertain_name=bool(item.get("uncertain_name")),
                source_text=_source_snippet(lines, name, names),
            )
        )
    return out


def _is_person_name(name: str) -> bool:
    """Reject placeholders like "(ER/surgery physician)" that name nobody."""
    if len(name) > 80 or any(ch.isdigit() for ch in name):
        return False
    return not any(ch in name for ch in "/()@")


def _mentioned(name: str, text: str) -> bool:
    """True when the text names this person.

    Matching is per whole word so a first name never latches onto a longer
    surname ("Steve" must not match "Stevenson").
    """
    words = [w for w in re.split(r"[^A-Za-z]+", name.lower()) if len(w) > 1]
    if not words:
        return False
    lowered = text.lower()
    return all(re.search(rf"\b{re.escape(word)}\b", lowered) for word in words)


def _source_snippet(lines: list[str], name: str, all_names: list[str]) -> str:
    """The pasted line(s) this person came from, for the review screen.

    Roster tables put someone's role on the lines below their name, so the
    following lines are included until the next person starts.
    """
    others = [other for other in all_names if other != name]
    starts = [
        index
        for index, line in enumerate(lines)
        if _mentioned(name, line)
        # A line listing someone else belongs to them, not to this person: a
        # bare "Elizabeth" must not borrow Elizabeth Hankla's row.
        and not any(_mentioned(other, line) for other in others)
    ] or [index for index, line in enumerate(lines) if _mentioned(name, line)]
    if not starts:
        return name

    index = starts[0]
    snippet = [lines[index].strip()]
    for follower in lines[index + 1 : index + 3]:
        text = follower.strip()
        if not text or any(_mentioned(other, text) for other in others):
            break
        snippet.append(text)
    return " — ".join(snippet)[:600]


def _name_key(name: str) -> str:
    return " ".join(re.split(r"[^a-z]+", (name or "").lower())).strip()


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
