"""Stub voice provider — never places a real call."""
from __future__ import annotations

from app.services.voice_providers.base import CallPlacementContext, PlaceCallResult, VoiceProvider


class StubVoiceProvider(VoiceProvider):
    name = "stub"

    def place_call(self, *, ctx: CallPlacementContext) -> PlaceCallResult:
        return PlaceCallResult(
            placed=False,
            provider=self.name,
            error="Stub provider: calling disabled. Set VOICE_PROVIDER=vapi to enable.",
        )
