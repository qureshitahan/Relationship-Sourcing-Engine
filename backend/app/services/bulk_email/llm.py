"""Small Anthropic JSON helper for the bulk email assistant.

The insight provider is shaped around principal/prospect research. Bulk email
work only needs plain JSON completions (with a bigger output budget than the
1k-token insight calls, since one call can return dozens of parsed rows), so
this wraps the client directly.

Every helper returns ``None`` instead of raising: each caller has a
deterministic fallback, so a missing key or a bad API day degrades the quality
of the output rather than breaking the feature.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.core.config import settings
from app.services.provider_health import (
    inspect_anthropic_exception,
    record_provider_success,
)

logger = logging.getLogger(__name__)

_client = None


def llm_available() -> bool:
    return bool(settings.anthropic_api_key)


def _get_client():
    global _client
    if _client is None:
        import anthropic  # imported lazily so the package stays optional

        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def complete_json(system: str, user: str, *, max_tokens: int = 2048) -> Optional[Any]:
    """One JSON completion. Returns the parsed value, or None on any failure."""
    if not llm_available():
        return None
    try:
        resp = _get_client().messages.create(
            model=settings.anthropic_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        parsed = parse_json_value(text)
        if parsed is not None:
            record_provider_success("anthropic")
        return parsed
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        logger.warning("Bulk email LLM call failed: %s", exc)
        inspect_anthropic_exception(exc)
        return None


def parse_json_value(text: str) -> Any:
    """Parse a JSON object/array, tolerating prose or fences around it."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None
