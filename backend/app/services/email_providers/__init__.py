"""Email sending providers (Milestone 4).

Pluggable senders behind a common interface. With multi-mailbox support, each
principal can use Outlook (Microsoft Graph) or Gmail — both can be configured
in the same app; selection is per principal via outreach_mailbox_id.
"""
from __future__ import annotations

from typing import Any, Optional

from app.core.config import settings
from app.services.email_providers.base import EmailProvider, SendResult
from app.services.email_providers.gmail import GmailEmailProvider
from app.services.email_providers.mailboxes import (
    OutreachMailbox,
    list_outreach_mailboxes,
    mailbox_for_principal,
    resolve_mailbox,
)
from app.services.email_providers.microsoft_graph import MicrosoftGraphEmailProvider
from app.services.email_providers.stub import StubEmailProvider

_PROVIDERS = {
    "stub": StubEmailProvider,
    "microsoft_graph": MicrosoftGraphEmailProvider,
    "outlook": MicrosoftGraphEmailProvider,
    # Gmail / Google Workspace (SMTP send + IMAP reply tracking).
    "gmail": GmailEmailProvider,
    "google": GmailEmailProvider,
}


def provider_for_mailbox(mailbox: OutreachMailbox) -> EmailProvider:
    """Instantiate the right provider wired to this mailbox's credentials."""
    provider = (mailbox.provider or "").strip().lower()
    if provider in ("gmail", "google"):
        return GmailEmailProvider(
            address=mailbox.address, app_password=mailbox.app_password
        )
    if provider in ("microsoft_graph", "outlook"):
        return MicrosoftGraphEmailProvider(send_as_user=mailbox.address)
    return StubEmailProvider()


def get_email_provider(
    mailbox_id: Optional[str] = None,
    *,
    principal: Any | None = None,
) -> EmailProvider:
    """Return a provider for a mailbox id, principal, or legacy EMAIL_PROVIDER."""
    if principal is not None:
        return provider_for_mailbox(mailbox_for_principal(principal))
    if mailbox_id:
        return provider_for_mailbox(resolve_mailbox(mailbox_id))

    # Legacy: no mailbox selected — honour EMAIL_PROVIDER + global env.
    provider_cls = _PROVIDERS.get(settings.email_provider, StubEmailProvider)
    if provider_cls is GmailEmailProvider:
        return GmailEmailProvider()
    if provider_cls is MicrosoftGraphEmailProvider:
        return MicrosoftGraphEmailProvider()
    return provider_cls()


__all__ = [
    "EmailProvider",
    "SendResult",
    "OutreachMailbox",
    "get_email_provider",
    "provider_for_mailbox",
    "list_outreach_mailboxes",
    "resolve_mailbox",
    "mailbox_for_principal",
]
