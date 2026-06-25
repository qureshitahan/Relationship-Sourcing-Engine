"""Email sending providers (Milestone 4).

Pluggable senders behind a common interface, selected by EMAIL_PROVIDER. The
`stub` provider does not actually send — it logs and returns a fake message id —
so the approval/send workflow can be built safely before wiring a real provider.
"""
from app.core.config import settings
from app.services.email_providers.base import EmailProvider, SendResult
from app.services.email_providers.microsoft_graph import MicrosoftGraphEmailProvider
from app.services.email_providers.stub import StubEmailProvider

_PROVIDERS = {
    "stub": StubEmailProvider,
    "microsoft_graph": MicrosoftGraphEmailProvider,
    "outlook": MicrosoftGraphEmailProvider,
}


def get_email_provider() -> EmailProvider:
    provider_cls = _PROVIDERS.get(settings.email_provider, StubEmailProvider)
    return provider_cls()


__all__ = ["EmailProvider", "SendResult", "get_email_provider"]
