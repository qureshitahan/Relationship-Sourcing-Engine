#!/usr/bin/env python3
"""Validate Vapi voice-calling configuration before placing real calls."""
from __future__ import annotations

import sys

from app.core.config import resolve_vapi_webhook_url, settings


def main() -> int:
    print("Voice provider:", settings.voice_provider)
    ok = True

    if settings.voice_provider != "vapi":
        print("WARN  VOICE_PROVIDER is not 'vapi' — calls will use the stub provider.")
        ok = False

    if not settings.vapi_api_key:
        print("MISSING  VAPI_API_KEY")
        ok = False
    else:
        print("OK       VAPI_API_KEY is set")

    if not settings.vapi_phone_number_id:
        print("MISSING  VAPI_PHONE_NUMBER_ID (from Vapi → Phone Numbers)")
        ok = False
    else:
        print("OK       VAPI_PHONE_NUMBER_ID =", settings.vapi_phone_number_id)

    if settings.vapi_assistant_id:
        print("OK       VAPI_ASSISTANT_ID =", settings.vapi_assistant_id)
    else:
        print("INFO     Using transient assistants (no VAPI_ASSISTANT_ID)")

    webhook = resolve_vapi_webhook_url()
    if webhook:
        print("OK       Webhook URL configured")
    else:
        print("WARN     APP_PUBLIC_URL not set — transcripts will not sync back")
        ok = False

    if not settings.anthropic_api_key:
        print("INFO     ANTHROPIC_API_KEY not in .env — ensure it is in Vapi Credentials")

    print()
    if ok:
        print("Ready to place calls. Approve a call in the UI, then click Place call now.")
        return 0
    print("Fix the items above, restart the backend, then re-run this script.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
