"""Vapi voice provider — outbound calls via Vapi + ElevenLabs + Claude."""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.core.config import resolve_vapi_webhook_url, settings
from app.services.voice import build_call_first_message, build_call_system_prompt
from app.services.voice_providers.base import CallPlacementContext, PlaceCallResult, VoiceProvider

logger = logging.getLogger(__name__)

VAPI_BASE = "https://api.vapi.ai"


class VapiVoiceProvider(VoiceProvider):
    name = "vapi"

    def place_call(self, *, ctx: CallPlacementContext) -> PlaceCallResult:
        api_key = (settings.vapi_api_key or "").strip()
        phone_number_id = (settings.vapi_phone_number_id or "").strip()
        if not api_key:
            return PlaceCallResult(
                placed=False,
                provider=self.name,
                error="VAPI_API_KEY is not configured.",
            )
        if not phone_number_id:
            return PlaceCallResult(
                placed=False,
                provider=self.name,
                error="VAPI_PHONE_NUMBER_ID is not configured.",
            )

        payload = self._build_payload(ctx, phone_number_id)
        try:
            with httpx.Client(timeout=30.0, trust_env=False) as client:
                resp = client.post(
                    f"{VAPI_BASE}/call",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            logger.warning("Vapi place_call network error: %s", exc)
            return PlaceCallResult(
                placed=False,
                provider=self.name,
                error=f"Network error contacting Vapi: {exc}",
            )

        if resp.status_code >= 400:
            detail = resp.text[:500]
            logger.warning("Vapi place_call failed (%s): %s", resp.status_code, detail)
            return PlaceCallResult(
                placed=False,
                provider=self.name,
                error=f"Vapi error {resp.status_code}: {detail}",
            )

        data = resp.json() if resp.content else {}
        call_id = data.get("id") if isinstance(data, dict) else None
        return PlaceCallResult(
            placed=True,
            provider=self.name,
            provider_call_id=str(call_id) if call_id else None,
        )

    def _build_payload(self, ctx: CallPlacementContext, phone_number_id: str) -> dict[str, Any]:
        """Build outbound call payload with a transient assistant per call."""
        meta = ctx.metadata or {}
        principal = meta.get("principal")
        company = meta.get("company")
        contact = meta.get("contact")
        insight = meta.get("insight")

        system_prompt = build_call_system_prompt(principal, company, contact, insight)
        first_message = build_call_first_message(principal, contact, company)

        assistant: dict[str, Any] = {
            "name": f"Outreach call #{ctx.call_id}",
            "firstMessage": first_message,
            "model": {
                "provider": "anthropic",
                "model": settings.anthropic_model,
                "messages": [{"role": "system", "content": system_prompt}],
                "temperature": 0.6,
            },
            "voice": {
                "provider": "11labs",
                "voiceId": settings.vapi_elevenlabs_voice_id,
            },
            "serverMessages": ["end-of-call-report", "status-update"],
        }

        webhook_url = resolve_vapi_webhook_url()
        if webhook_url:
            assistant["server"] = {"url": webhook_url}

        payload: dict[str, Any] = {
            "phoneNumberId": phone_number_id,
            "customer": {"number": ctx.to_number, "name": ctx.prospect_name},
            "metadata": {
                "relationship_call_id": str(ctx.call_id),
                "contact_id": str(meta.get("contact_id") or ""),
                "principal_id": str(meta.get("principal_id") or ""),
            },
        }

        assistant_id = (settings.vapi_assistant_id or "").strip()
        if assistant_id:
            payload["assistantId"] = assistant_id
            payload["assistantOverrides"] = {
                "firstMessage": first_message,
                "variableValues": {
                    "prospect_name": ctx.prospect_name,
                    "principal_name": ctx.principal_name,
                    "company_name": ctx.company_name or "",
                },
                "model": assistant["model"],
            }
        else:
            payload["assistant"] = assistant

        return payload
