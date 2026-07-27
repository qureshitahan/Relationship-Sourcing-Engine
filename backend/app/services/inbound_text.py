"""Normalize inbound reply text for storage and display.

Mail clients (especially Outlook → plain text) leave signature artifacts that
look broken in our UI: ``[photo]<url>``, ``Name<mailto:…>``, long maps links,
legal footers, and quoted thread history. Strip that noise so operators see
what the person actually wrote.
"""
from __future__ import annotations

import re
from typing import Optional

# Outlook/HTML-to-text "linked" forms: Label<url> or [label]<url>
_ANGLE_LINK_RE = re.compile(
    r"(?:\[[^\]]{0,80}\]|[^\s<>]{0,80})<(?:https?://|mailto:|tel:)[^>]+>",
    re.IGNORECASE,
)
# Bare angle-bracket URLs left after a label was already consumed.
_BARE_ANGLE_URL_RE = re.compile(
    r"<(?:https?://|mailto:|tel:)[^>]+>",
    re.IGNORECASE,
)
# Standalone image / social icon lines that survived as [photo], [youtube], …
_ICON_ONLY_RE = re.compile(
    r"^\s*\[(?:photo|image|logo|cid:[^\]]+|youtube|facebook|twitter|x|"
    r"instagram|linkedin|tiktok)\]\s*$",
    re.IGNORECASE,
)
# Long Google Maps / tracking URLs on their own line.
_MAPS_URL_RE = re.compile(
    r"https?://(?:maps\.google\.|www\.google\.[^/\s]+/maps|goo\.gl/maps)\S+",
    re.IGNORECASE,
)
_HTTP_ONLY_LINE_RE = re.compile(r"^\s*https?://\S+\s*$", re.IGNORECASE)

# Cut the message once a classic signature / quote marker appears.
_SIG_CUT_RE = re.compile(
    r"(?m)^(?:"
    r"--\s*$"
    r"|_{2,}\s*$"
    r"|-{2,}\s*$"
    r"|sent from my (?:iphone|ipad|android|mobile)"
    r"|get outlook for "
    r"|confidentiality notice"
    r"|this (?:e-?mail|message) (?:and any attachments )?(?:is|are) confidential"
    r"|the information contained in this (?:e-?mail|message)"
    r"|on .+ wrote:\s*$"
    r"|from:\s+.+\s*$"
    r")",
    re.IGNORECASE,
)

# Phone / email lines that are pure contact-card residue after angle stripping.
_CONTACT_CARD_RE = re.compile(
    r"^(?:main|mobile|office|direct|phone|tel|fax|email|e-mail|web|website|"
    r"www)\b.*$",
    re.IGNORECASE,
)


def clean_inbound_reply(text: Optional[str], *, max_chars: int = 8000) -> str:
    """Return the human message with signature/quote chrome removed."""
    if not text:
        return ""

    # Normalize newlines and odd whitespace from MIME decoding.
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    body = body.replace("\xa0", " ")
    body = _ANGLE_LINK_RE.sub("", body)
    body = _BARE_ANGLE_URL_RE.sub("", body)
    body = _MAPS_URL_RE.sub("", body)

    cut = _SIG_CUT_RE.search(body)
    if cut and cut.start() > 40:
        # Only cut if there's real content before the marker.
        body = body[: cut.start()]

    cleaned_lines: list[str] = []
    blank_run = 0
    for raw in body.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            blank_run += 1
            if blank_run <= 1 and cleaned_lines:
                cleaned_lines.append("")
            continue
        blank_run = 0
        if _ICON_ONLY_RE.match(stripped):
            continue
        if _HTTP_ONLY_LINE_RE.match(stripped):
            continue
        if _CONTACT_CARD_RE.match(stripped) and len(cleaned_lines) >= 2:
            # Contact-card lines after the message body — stop here.
            break
        # Drop quoted reply lines once we've started seeing them in bulk.
        if stripped.startswith(">") and len(cleaned_lines) >= 2:
            break
        cleaned_lines.append(line)

    # Trim a trailing name / title signature block that follows a blank line
    # after the real message (common Outlook plain-text pattern).
    cleaned_lines = _trim_trailing_sig_block(cleaned_lines)

    # Trim trailing blanks.
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()

    out = "\n".join(cleaned_lines).strip()
    # Collapse accidental double spaces left by angle-link removal.
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    if len(out) > max_chars:
        out = out[: max_chars - 1].rstrip() + "…"
    return out


_NAME_LIKE_RE = re.compile(
    r"^[A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){0,3}$"
)
_TITLE_LIKE_RE = re.compile(
    r"(?:vice president|president|director|manager|partner|officer|"
    r"founder|ceo|coo|cfo|cto|svp|evp|md|principal)\b",
    re.IGNORECASE,
)


def _trim_trailing_sig_block(lines: list[str]) -> list[str]:
    """Drop a short name/title block after the last blank line of the message."""
    if len(lines) < 4:
        return lines
    # Find the last blank separator with content on both sides.
    last_blank = -1
    for i, line in enumerate(lines):
        if not line.strip():
            last_blank = i
    if last_blank < 2:
        return lines
    head = lines[:last_blank]
    tail = [ln for ln in lines[last_blank + 1 :] if ln.strip()]
    if not head or not (1 <= len(tail) <= 4):
        return lines
    # Head must look like a real message (has a sentence), tail like a card.
    head_text = " ".join(ln.strip() for ln in head if ln.strip())
    if "." not in head_text and "?" not in head_text and "!" not in head_text:
        return lines
    if not _NAME_LIKE_RE.match(tail[0].strip()):
        return lines
    if len(tail) >= 2 and not _TITLE_LIKE_RE.search(tail[1]):
        # Still allow a bare name-only signature.
        if any(len(t) > 60 or "." in t for t in tail[1:]):
            return lines
    return head


def reply_snippet(text: Optional[str], *, limit: int = 280) -> str:
    """Short preview of a cleaned reply for list views."""
    cleaned = clean_inbound_reply(text, max_chars=limit + 200)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"
