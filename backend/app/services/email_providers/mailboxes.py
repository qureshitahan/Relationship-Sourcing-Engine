"""Multi-mailbox outreach configuration.

Outlook (Microsoft Graph) and Gmail can both be configured in the same app.
Each principal picks an ``outreach_mailbox_id``; sends/replies use that mailbox's
provider and credentials.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutreachMailbox:
    id: str
    label: str
    provider: str  # microsoft_graph | gmail | stub
    address: str
    from_name: str
    app_password: str = ""

    @property
    def configured(self) -> bool:
        provider = (self.provider or "").strip().lower()
        if provider in ("stub",):
            return True
        if provider in ("gmail", "google"):
            return bool(self.address and self.app_password)
        if provider in ("microsoft_graph", "outlook"):
            return bool(
                settings.microsoft_tenant_id
                and settings.microsoft_client_id
                and settings.microsoft_client_secret
                and self.address
            )
        return False

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "address": self.address,
            "from_name": self.from_name,
            "configured": self.configured,
        }


# Built-in catalog for this deployment. Passwords come from env (never committed).
_BUILTIN: list[dict[str, str]] = [
    {
        "id": "galaxy_outlook",
        "label": "Dalbir Bains · Galaxy Outlook",
        "provider": "microsoft_graph",
        "address": "dalbir.bains@galaxypharma.net",
        "from_name": "Dalbir Bains",
    },
    {
        "id": "tekhqs_dalbir",
        "label": "Dalbir Bains · Tekhqs Gmail",
        "provider": "gmail",
        "address": "dalbir.bains@tekhqs.ai",
        "from_name": "Dalbir Bains",
        "password_env": "GMAIL_APP_PASSWORD_TEKHQS_DALBIR",
    },
    {
        "id": "tekhqs_taha",
        "label": "Taha Qureshi · Tekhqs Gmail",
        "provider": "gmail",
        "address": "taha.qureshi@tekhqs.ai",
        "from_name": "Taha Qureshi",
        "password_env": "GMAIL_APP_PASSWORD_TEKHQS_TAHA",
    },
]


def _password_for(raw: dict[str, Any]) -> str:
    explicit = str(raw.get("app_password") or "").strip()
    if explicit:
        return explicit
    env_name = str(raw.get("password_env") or "").strip()
    if env_name:
        return (os.environ.get(env_name) or getattr(settings, env_name.lower(), "") or "").strip()
    # Legacy single-Gmail fallback when address matches.
    address = str(raw.get("address") or "").strip().lower()
    legacy_addr = (settings.gmail_address or "").strip().lower()
    if address and legacy_addr and address == legacy_addr:
        return (settings.gmail_app_password or "").strip()
    # Named fallbacks used by this project.
    mailbox_id = str(raw.get("id") or "").strip().lower()
    if mailbox_id == "tekhqs_dalbir":
        return (
            os.environ.get("GMAIL_APP_PASSWORD_TEKHQS_DALBIR")
            or settings.gmail_app_password_tekhqs_dalbir
            or ""
        ).strip()
    if mailbox_id == "tekhqs_taha":
        return (
            os.environ.get("GMAIL_APP_PASSWORD_TEKHQS_TAHA")
            or settings.gmail_app_password_tekhqs_taha
            or ""
        ).strip()
    return ""


def _from_raw(raw: dict[str, Any]) -> Optional[OutreachMailbox]:
    mailbox_id = str(raw.get("id") or "").strip()
    address = str(raw.get("address") or "").strip()
    provider = str(raw.get("provider") or "stub").strip().lower()
    if not mailbox_id or not address:
        return None
    if provider == "google":
        provider = "gmail"
    if provider == "outlook":
        provider = "microsoft_graph"
    label = str(raw.get("label") or "").strip() or f"{raw.get('from_name') or address} ({provider})"
    from_name = str(raw.get("from_name") or "").strip() or address.split("@")[0]
    return OutreachMailbox(
        id=mailbox_id,
        label=label,
        provider=provider,
        address=address,
        from_name=from_name,
        app_password=_password_for(raw),
    )


def list_outreach_mailboxes() -> list[OutreachMailbox]:
    """Return configured outreach mailboxes (Outlook + Gmail can coexist)."""
    raw_list: list[dict[str, Any]] = []
    custom = (settings.outreach_mailboxes_json or "").strip()
    if custom:
        try:
            parsed = json.loads(custom)
            if isinstance(parsed, list):
                raw_list = [x for x in parsed if isinstance(x, dict)]
        except json.JSONDecodeError:
            logger.warning("OUTREACH_MAILBOXES_JSON is invalid JSON; using built-in mailboxes")

    if not raw_list:
        raw_list = list(_BUILTIN)
        # If only legacy single Gmail is set and not already represented, append it.
        legacy = (settings.gmail_address or "").strip()
        if legacy and not any(
            str(r.get("address") or "").strip().lower() == legacy.lower() for r in raw_list
        ):
            raw_list.append(
                {
                    "id": "gmail_default",
                    "label": f"{legacy} · Gmail",
                    "provider": "gmail",
                    "address": legacy,
                    "from_name": settings.outreach_from_name or legacy.split("@")[0],
                    "app_password": settings.gmail_app_password or "",
                }
            )

    boxes: list[OutreachMailbox] = []
    seen: set[str] = set()
    for raw in raw_list:
        box = _from_raw(raw)
        if box is None or box.id in seen:
            continue
        seen.add(box.id)
        boxes.append(box)
    return boxes


def resolve_mailbox(mailbox_id: Optional[str] = None) -> OutreachMailbox:
    """Resolve a mailbox by id, or fall back to default / first / legacy provider."""
    boxes = list_outreach_mailboxes()
    wanted = (mailbox_id or settings.default_outreach_mailbox_id or "").strip()
    if wanted:
        for box in boxes:
            if box.id == wanted:
                return box

    # Prefer a configured mailbox.
    for box in boxes:
        if box.configured:
            return box
    if boxes:
        return boxes[0]

    # Last-resort stub so the app still boots.
    provider = (settings.email_provider or "stub").strip().lower()
    address = (
        settings.outreach_from_email
        or settings.gmail_address
        or settings.microsoft_send_as_user
        or "noreply@example.com"
    )
    return OutreachMailbox(
        id="legacy_default",
        label=f"{address} · {provider}",
        provider=provider if provider in ("gmail", "microsoft_graph", "stub") else "stub",
        address=address,
        from_name=settings.outreach_from_name or "Outreach",
        app_password=settings.gmail_app_password or "",
    )


def mailbox_for_principal(principal: Any | None) -> OutreachMailbox:
    mailbox_id = getattr(principal, "outreach_mailbox_id", None) if principal else None
    return resolve_mailbox(mailbox_id)
