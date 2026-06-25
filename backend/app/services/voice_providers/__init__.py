"""Voice calling providers — pluggable backends selected by VOICE_PROVIDER."""
from app.core.config import settings
from app.services.voice_providers.base import (
    CallPlacementContext,
    PlaceCallResult,
    VoiceProvider,
)
from app.services.voice_providers.stub import StubVoiceProvider
from app.services.voice_providers.vapi import VapiVoiceProvider

_PROVIDERS = {
    "stub": StubVoiceProvider,
    "vapi": VapiVoiceProvider,
}


def get_voice_provider() -> VoiceProvider:
    provider_cls = _PROVIDERS.get(settings.voice_provider, StubVoiceProvider)
    return provider_cls()


__all__ = [
    "VoiceProvider",
    "PlaceCallResult",
    "CallPlacementContext",
    "get_voice_provider",
]
