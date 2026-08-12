"""LinkedIn outreach providers (Unipile).

Pluggable senders behind a common interface, selected by LINKEDIN_PROVIDER. The
``stub`` provider does not send — it returns fake ids — so the approval/send
workflow can run safely before a real Unipile account is wired.
"""
from typing import Optional

from app.core.config import settings
from app.services.linkedin_providers.base import (
    FollowerPage,
    FollowerRecord,
    InviteResult,
    LinkedInProfile,
    LinkedInProvider,
    ReplyResult,
    SendResult,
    public_identifier_from_url,
)
from app.services.linkedin_providers.stub import StubLinkedInProvider
from app.services.linkedin_providers.unipile import UnipileLinkedInProvider

_PROVIDERS = {
    "stub": StubLinkedInProvider,
    "unipile": UnipileLinkedInProvider,
}


ACTIVE_ACCOUNT_SETTING = "linkedin_account_id"


def get_linkedin_provider(account_id: Optional[str] = None) -> LinkedInProvider:
    """The active LinkedIn provider.

    ``account_id`` pins a specific connected account (used when polling replies
    for a message sent from a particular account). When omitted, the active
    account is used: the user-selected one from the UI, else the env default —
    i.e. exactly the original single-account behaviour.
    """
    provider_cls = _PROVIDERS.get(settings.linkedin_provider, StubLinkedInProvider)
    if provider_cls is UnipileLinkedInProvider:
        # Honor a user-selected connected account (set from the UI), else the env.
        from app.services.app_settings import get_setting

        resolved = (
            account_id
            or get_setting(ACTIVE_ACCOUNT_SETTING)
            or settings.unipile_account_id
        )
        return UnipileLinkedInProvider(account_id=resolved)
    return provider_cls()


__all__ = [
    "LinkedInProvider",
    "LinkedInProfile",
    "SendResult",
    "InviteResult",
    "ReplyResult",
    "FollowerPage",
    "FollowerRecord",
    "ACTIVE_ACCOUNT_SETTING",
    "get_linkedin_provider",
    "public_identifier_from_url",
]
