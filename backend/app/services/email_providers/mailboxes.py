"""Selectable outreach mailboxes (multi-sender support).

The app historically sent from ONE global mailbox (EMAIL_PROVIDER +
OUTREACH_FROM_EMAIL + provider creds). This module adds the ability to define
several mailboxes and pick one per email — without changing that default.

Mailboxes are declared in the OUTREACH_MAILBOXES env var as a JSON array, e.g.:

    OUTREACH_MAILBOXES=[
      {"id":"galaxy","label":"Dalbir (Galaxy)","provider":"microsoft_graph",
       "from_email":"dalbir.bains@galaxypharma.net","from_name":"Dalbir Bains"},
      {"id":"dalbir_tekhqs","label":"Dalbir (tekhqs)","provider":"gmail",
       "from_email":"dalbir.bains@tekhqs.ai","from_name":"Dalbir Bains",
       "gmail_app_password":"…"},
      {"id":"taha_tekhqs","label":"Taha (tekhqs)","provider":"gmail",
       "from_email":"taha.qureshi@tekhqs.ai","from_name":"Taha Qureshi",
       "gmail_app_password":"…"}
    ]

Microsoft mailboxes reuse the global MICROSOFT_* app credentials (one Azure app
can send as any mailbox in its tenant); only "send_as_user" varies (it defaults
to from_email). Gmail mailboxes carry their own App Password.

When OUTREACH_MAILBOXES is unset, the only mailbox is the legacy default built
from the existing globals — so existing deployments behave exactly as before.
A draft with no ``from_mailbox`` also always resolves to that default.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_MAILBOX_ID = "default"


@dataclass
class Mailbox:
    id: str
    label: str
    provider: str  # microsoft_graph | outlook | gmail | google | stub
    from_email: str
    from_name: str = ""
    # Microsoft: mailbox UPN to send as (defaults to from_email).
    send_as_user: Optional[str] = None
    # Gmail: App Password for this mailbox.
    gmail_app_password: Optional[str] = None


def default_mailbox() -> Mailbox:
    """The legacy single sender, built from the existing global settings.

    This reproduces the exact pre-multi-mailbox behaviour, so anything that does
    not explicitly choose a mailbox keeps sending exactly as it does today.
    """
    return Mailbox(
        id=DEFAULT_MAILBOX_ID,
        label=settings.outreach_from_email or "Default mailbox",
        provider=settings.email_provider or "stub",
        from_email=settings.outreach_from_email,
        from_name=settings.outreach_from_name,
        send_as_user=settings.microsoft_send_as_user or settings.outreach_from_email,
        gmail_app_password=settings.gmail_app_password or None,
    )


def _parse_configured() -> List[Mailbox]:
    raw = (settings.outreach_mailboxes_env or "").strip()
    if not raw:
        return []
    # Tolerate a value accidentally wrapped in surrounding quotes. In a .env file
    # python-dotenv strips these, but Azure App Settings keep them literally —
    # without this, the JSON would fail to parse and mailboxes would silently
    # fall back to the single default sender.
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        raw = raw[1:-1].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("OUTREACH_MAILBOXES is not valid JSON, ignoring: %s", exc)
        return []
    if not isinstance(data, list):
        logger.warning("OUTREACH_MAILBOXES must be a JSON array, ignoring.")
        return []

    boxes: List[Mailbox] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        from_email = str(item.get("from_email") or "").strip()
        mb_id = str(item.get("id") or from_email or f"mailbox_{i}").strip()
        if not from_email:
            logger.warning("OUTREACH_MAILBOXES entry %s missing from_email, skipping.", mb_id)
            continue
        boxes.append(
            Mailbox(
                id=mb_id,
                label=str(item.get("label") or from_email),
                provider=str(item.get("provider") or "stub").strip(),
                from_email=from_email,
                from_name=str(item.get("from_name") or ""),
                send_as_user=(item.get("send_as_user") or from_email),
                gmail_app_password=(item.get("gmail_app_password") or None),
            )
        )
    return boxes


def list_mailboxes() -> List[Mailbox]:
    """All selectable mailboxes. Falls back to the single legacy default."""
    configured = _parse_configured()
    return configured or [default_mailbox()]


def find_mailbox(mailbox_id: Optional[str]) -> Optional[Mailbox]:
    """Look up a mailbox by id, or None when it is unset/unknown."""
    if not mailbox_id:
        return None
    for mb in list_mailboxes():
        if mb.id == mailbox_id:
            return mb
    return None


def resolve_mailbox(mailbox_id: Optional[str]) -> Mailbox:
    """Resolve a draft's ``from_mailbox`` id to a Mailbox.

    Unknown/empty ids fall back to the configured default (or the legacy
    default) so a draft can never become unsendable because of a missing or
    renamed mailbox.
    """
    found = find_mailbox(mailbox_id)
    if found:
        return found
    fallback = find_mailbox(settings.default_outreach_mailbox_id)
    if fallback:
        return fallback
    return default_mailbox()


class MailboxUnassignedError(RuntimeError):
    """A principal's send-from mailbox is ambiguous, so we refuse to send.

    Raised instead of silently falling back to the global default, which would
    deliver the email from the wrong person's address.
    """

    def __init__(self, principal_name: str):
        self.principal_name = principal_name
        super().__init__(
            f'No send-from mailbox is set for "{principal_name}". Open the '
            "Principals page and choose their outreach mailbox — refusing to "
            "send from another principal's address."
        )


def mailbox_for_principal(principal: object, *, strict: bool = True) -> Mailbox:
    """The mailbox a principal's outreach must be sent from.

    Uses ``principal.outreach_mailbox_id`` when set. When it is not set we only
    fall back if there is exactly one mailbox configured (no ambiguity about
    who the sender is); otherwise ``strict`` callers get
    ``MailboxUnassignedError`` so an email never goes out under the wrong
    identity. Non-strict callers (digests, previews) take the default.
    """
    assigned = getattr(principal, "outreach_mailbox_id", None) if principal else None
    found = find_mailbox(assigned)
    if found:
        return found

    boxes = list_mailboxes()
    if len(boxes) == 1:
        return boxes[0]

    if assigned:
        logger.warning(
            "Principal mailbox %r is not in OUTREACH_MAILBOXES; treating as unassigned.",
            assigned,
        )
    if strict:
        name = getattr(principal, "name", None) or "this principal"
        raise MailboxUnassignedError(str(name))
    return resolve_mailbox(None)
