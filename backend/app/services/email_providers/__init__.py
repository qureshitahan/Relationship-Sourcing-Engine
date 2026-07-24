"""Email sending providers (Milestone 4).

Pluggable senders behind a common interface, selected by EMAIL_PROVIDER. The
`stub` provider does not actually send — it logs and returns a fake message id —
so the approval/send workflow can be built safely before wiring a real provider.
"""
from app.core.config import settings
from app.services.email_providers.base import EmailProvider, SendResult
from app.services.email_providers.gmail import GmailEmailProvider
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


def get_email_provider() -> EmailProvider:
    provider_cls = _PROVIDERS.get(settings.email_provider, StubEmailProvider)
    return provider_cls()


def provider_for_mailbox(mailbox: "Mailbox") -> EmailProvider:
    """Build the right provider, configured for a specific mailbox's creds."""
    provider = (mailbox.provider or "").lower()
    if provider in ("microsoft_graph", "outlook"):
        return MicrosoftGraphEmailProvider(
            send_as_user=mailbox.send_as_user or mailbox.from_email
        )
    if provider in ("gmail", "google"):
        return GmailEmailProvider(
            address=mailbox.from_email,
            app_password=mailbox.gmail_app_password,
        )
    return StubEmailProvider()


from app.services.email_providers.mailboxes import (  # noqa: E402  (avoid cycle)
    Mailbox,
    default_mailbox,
    list_mailboxes,
    resolve_mailbox,
)

__all__ = [
    "EmailProvider",
    "SendResult",
    "get_email_provider",
    "provider_for_mailbox",
    "Mailbox",
    "list_mailboxes",
    "resolve_mailbox",
    "default_mailbox",
]
