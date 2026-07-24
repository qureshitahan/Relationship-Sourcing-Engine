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
import time
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


def research_json(
    system: str,
    user: str,
    *,
    max_tokens: int = 2048,
    max_uses: Optional[int] = None,
) -> tuple[Optional[Any], list[dict]]:
    """A JSON completion Claude may run web searches for.

    Returns the parsed value plus the sources the search actually opened, which
    the caller shows as evidence. Falls back to a plain completion (and no
    sources) when search is disabled or the call keeps failing.
    """
    if not llm_available():
        return None, []
    searches = (
        settings.bulk_lookup_web_search_max_uses if max_uses is None else max_uses
    )
    if searches <= 0:
        return complete_json(system, user, max_tokens=max_tokens), []

    resp = None
    attempts = 3
    for attempt in range(attempts):
        try:
            resp = _get_client().messages.create(
                model=settings.anthropic_model,
                max_tokens=max_tokens,
                system=system,
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": searches,
                    }
                ],
                messages=[{"role": "user", "content": user}],
            )
            break
        except Exception as exc:  # noqa: BLE001 - degrade rather than fail the batch
            if attempt < attempts - 1:
                time.sleep(2.0 * (2**attempt))
                continue
            logger.warning("Bulk lookup web search failed: %s", exc)
            inspect_anthropic_exception(exc)
            return complete_json(system, user, max_tokens=max_tokens), []

    text_parts: list[str] = []
    sources: list[dict] = []
    for block in resp.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "web_search_tool_result":
            for item in getattr(block, "content", []) or []:
                if isinstance(item, dict):
                    url, title = item.get("url"), item.get("title")
                else:
                    url = getattr(item, "url", None)
                    title = getattr(item, "title", None)
                if url:
                    sources.append({"title": title or url, "url": url})

    parsed = parse_json_value("".join(text_parts))
    if parsed is not None:
        record_provider_success("anthropic")
    return parsed, _dedupe_sources(sources)


def _dedupe_sources(sources: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for source in sources:
        if source["url"] not in seen:
            seen.add(source["url"])
            out.append(source)
    return out[:6]


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
