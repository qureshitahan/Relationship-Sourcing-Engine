"""Plain-text ↔ HTML helpers for outreach emails and signatures.

Signatures are stored as freeform text the user types, then normalized into a
readable plain-text block for drafts and a polished HTML block for the
messages recipients actually open.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

_CLOSER_RE = re.compile(
    r"^(thanks|thank you|best|best regards|regards|cheers|sincerely|"
    r"all the best|warmly)\b[,!. ]*$",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"^\+?[\d\s().-]{7,}$")
_LABEL_RE = re.compile(
    r"^(websites?|links?|phone|mobile|tel|linkedin|email|e-mail|calendar|"
    r"book a call|schedule)\s*:?\s*$",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


@dataclass
class SignatureParts:
    closer: str = "Thanks,"
    name: str = ""
    details: List[str] = field(default_factory=list)  # title, company, …
    phone: str = ""
    linkedin: str = ""
    websites: List[str] = field(default_factory=list)
    calendly: str = ""


def _normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip(".,);]")
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _display_host(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return url
    if host.startswith("www."):
        host = host[4:]
    return host or url


def parse_signature(signature: str) -> SignatureParts:
    """Split a freeform signature into structured parts."""
    parts = SignatureParts()
    lines = [ln.strip() for ln in (signature or "").splitlines() if ln.strip()]
    if not lines:
        return parts

    if _CLOSER_RE.match(lines[0]):
        parts.closer = lines[0].rstrip(".")
        if not parts.closer.endswith(","):
            # Keep a natural email closer shape.
            base = parts.closer.rstrip(",!")
            parts.closer = f"{base},"
        lines = lines[1:]

    for line in lines:
        if _LABEL_RE.match(line):
            continue
        urls = _URL_RE.findall(line)
        if urls:
            for raw in urls:
                url = _normalize_url(raw)
                low = url.lower()
                if "linkedin.com" in low and not parts.linkedin:
                    parts.linkedin = url
                elif "calendly.com" in low and not parts.calendly:
                    parts.calendly = url
                else:
                    parts.websites.append(url)
            # Keep any non-URL text on a mixed line (rare).
            leftover = _URL_RE.sub("", line).strip(" ·|-–—,")
            if leftover and not _LABEL_RE.match(leftover):
                if not parts.name:
                    parts.name = leftover
                else:
                    parts.details.append(leftover)
            continue
        if _PHONE_RE.match(line) and sum(ch.isdigit() for ch in line) >= 7:
            if not parts.phone:
                parts.phone = line
            continue
        if not parts.name:
            parts.name = line
        else:
            parts.details.append(line)
    return parts


def format_signature_plain(signature: str) -> str:
    """Readable plain-text signature for drafts and text/plain MIME parts."""
    parts = parse_signature(signature)
    if not (signature or "").strip():
        return ""

    out: List[str] = [parts.closer, ""]
    if parts.name:
        out.append(parts.name)
    out.extend(parts.details)

    contact: List[str] = []
    if parts.linkedin:
        contact.append(f"LinkedIn: {parts.linkedin}")
    if parts.phone:
        contact.append(parts.phone)
    if contact:
        if out and out[-1] != "":
            out.append("")
        out.extend(contact)

    if parts.websites:
        if out and out[-1] != "":
            out.append("")
        out.append(" · ".join(parts.websites))

    if parts.calendly:
        if out and out[-1] != "":
            out.append("")
        out.append(f"Book a call: {parts.calendly}")

    # Drop trailing blanks.
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def format_signature_html(signature: str) -> str:
    """Styled HTML signature block for the messages recipients open."""
    parts = parse_signature(signature)
    if not (signature or "").strip():
        return ""

    chunks: List[str] = [
        '<div style="margin-top:18px;padding-top:14px;'
        'border-top:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;'
        'font-size:14px;line-height:1.45;color:#334155">'
    ]
    chunks.append(
        f'<div style="color:#64748b;margin-bottom:10px">'
        f"{html.escape(parts.closer)}</div>"
    )
    if parts.name:
        chunks.append(
            f'<div style="font-weight:700;font-size:15px;color:#0f172a">'
            f"{html.escape(parts.name)}</div>"
        )
    for detail in parts.details:
        chunks.append(
            f'<div style="color:#475569;margin-top:2px">{html.escape(detail)}</div>'
        )

    row_bits: List[str] = []
    if parts.linkedin:
        row_bits.append(
            f'<a href="{html.escape(parts.linkedin, quote=True)}" '
            'style="color:#2563eb;text-decoration:none">LinkedIn</a>'
        )
    if parts.phone:
        tel = "tel:" + re.sub(r"[^\d+]", "", parts.phone)
        row_bits.append(
            f'<a href="{html.escape(tel, quote=True)}" '
            f'style="color:#334155;text-decoration:none">'
            f"{html.escape(parts.phone)}</a>"
        )
    if row_bits:
        sep = '<span style="color:#cbd5e1;padding:0 8px">·</span>'
        chunks.append(
            f'<div style="margin-top:10px;font-size:13px">{sep.join(row_bits)}</div>'
        )

    if parts.websites:
        site_bits = []
        for url in parts.websites:
            label = html.escape(_display_host(url))
            site_bits.append(
                f'<a href="{html.escape(url, quote=True)}" '
                f'style="color:#2563eb;text-decoration:none">{label}</a>'
            )
        sep = '<span style="color:#cbd5e1;padding:0 8px">·</span>'
        chunks.append(
            f'<div style="margin-top:6px;font-size:13px">{sep.join(site_bits)}</div>'
        )

    if parts.calendly:
        chunks.append(
            '<div style="margin-top:12px">'
            f'<a href="{html.escape(parts.calendly, quote=True)}" '
            'style="display:inline-block;padding:8px 14px;background:#0f172a;'
            "color:#ffffff;text-decoration:none;border-radius:6px;"
            'font-size:13px;font-weight:600">Book a 30-min call</a></div>'
        )

    chunks.append("</div>")
    return "".join(chunks)


def plain_body_to_html(
    body: str,
    *,
    signature: Optional[str] = None,
    pixel_url: Optional[str] = None,
) -> str:
    """Render a draft body as HTML, with an optional styled signature block.

    When ``signature`` is provided and present at the end of ``body``, the
    message and sign-off are split so the signature can be styled separately.
    """
    text = (body or "").rstrip()
    message = text
    sig_html = ""
    sig = (signature or "").strip()
    if sig:
        polished = format_signature_plain(sig)
        for candidate in (polished, sig):
            if not candidate:
                continue
            if text == candidate:
                message = ""
                sig_html = format_signature_html(sig)
                break
            suffix = f"\n\n{candidate}"
            if text.endswith(suffix):
                message = text[: -len(suffix)].rstrip()
                sig_html = format_signature_html(sig)
                break
        else:
            # Signature text drifted; still try a light closer-based split.
            parts = text.rsplit("\n\n", 1)
            if len(parts) == 2 and _CLOSER_RE.match(parts[1].splitlines()[0].strip()):
                message, trailing = parts
                sig_html = format_signature_html(trailing)

    escaped = html.escape(message).replace("\n", "<br>\n")
    pixel = ""
    if pixel_url:
        pixel = (
            f'<img src="{html.escape(pixel_url, quote=True)}" width="1" height="1" '
            'alt="" style="display:none;max-width:0;max-height:0;overflow:hidden" />'
        )
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        f'color:#222;line-height:1.55">{escaped}</div>'
        f"{sig_html}{pixel}"
    )
