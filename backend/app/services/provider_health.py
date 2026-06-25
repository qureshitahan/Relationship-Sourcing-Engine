"""Track live API provider health (credits, auth, degraded fallbacks).

Errors are recorded when Apollo or Anthropic calls fail so the UI can warn
instead of silently falling back to stub templates or empty discovery results.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_lock = Lock()

# issue_type values exposed to the API / UI
STATUS_OK = "ok"
STATUS_NO_CREDITS = "no_credits"
STATUS_AUTH_ERROR = "auth_error"
STATUS_MISSING_KEY = "missing_key"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_DEGRADED = "degraded"
STATUS_ERROR = "error"

_CREDIT_RE = re.compile(
    r"credit balance is too low|insufficient[_ ]credits?|out of credits|"
    r"no credits remaining|not enough credits|credit limit|billing|payment required|"
    r"purchase credits|add credits|exceeded.*credit",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"invalid api[_ ]key|authentication failed|unauthorized|invalid x-api-key|"
    r"api key.*invalid|permission denied|access denied",
    re.IGNORECASE,
)
_RATE_RE = re.compile(
    r"rate limit|too many requests|overloaded|429|529|timeout|timed out",
    re.IGNORECASE,
)


@dataclass
class ProviderState:
    provider: str
    status: str = STATUS_OK
    message: Optional[str] = None
    last_error_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    using_stub: bool = False
    configured: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


_states: Dict[str, ProviderState] = {
    "apollo": ProviderState(provider="apollo"),
    "anthropic": ProviderState(provider="anthropic"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _classify_error_text(text: str, *, status_code: Optional[int] = None) -> str:
    blob = (text or "").strip()
    if status_code == 402:
        return STATUS_NO_CREDITS
    if status_code in (401, 403):
        return STATUS_AUTH_ERROR
    if _CREDIT_RE.search(blob):
        return STATUS_NO_CREDITS
    if _AUTH_RE.search(blob):
        return STATUS_AUTH_ERROR
    if status_code == 429 or _RATE_RE.search(blob):
        return STATUS_RATE_LIMITED
    return STATUS_ERROR


def record_provider_failure(
    provider: str,
    error: Any,
    *,
    status_code: Optional[int] = None,
    using_stub: bool = False,
) -> None:
    """Record a failed API call for ``provider`` (apollo | anthropic)."""
    text = str(error) if error is not None else ""
    issue = _classify_error_text(text, status_code=status_code)
    message = text[:500] if text else "Unknown provider error"
    if issue == STATUS_NO_CREDITS:
        if provider == "anthropic":
            message = (
                "Anthropic API credits exhausted. Add funds at console.anthropic.com "
                "— outreach and research are using stub templates until restored."
            )
        elif provider == "apollo":
            message = (
                "Apollo API credits exhausted or billing blocked. Top up at apollo.io "
                "— discovery and email reveal will return empty results until restored."
            )
    with _lock:
        state = _states.setdefault(provider, ProviderState(provider=provider))
        state.status = STATUS_DEGRADED if using_stub else issue
        state.message = message
        state.last_error_at = _now()
        state.using_stub = using_stub


def record_provider_success(provider: str) -> None:
    """Clear a provider's error state after a successful live API call."""
    with _lock:
        state = _states.setdefault(provider, ProviderState(provider=provider))
        state.status = STATUS_OK
        state.message = None
        state.using_stub = False
        state.last_success_at = _now()


def mark_using_stub(provider: str, *, reason: str) -> None:
    """Record that live provider was skipped and stub output was used."""
    record_provider_failure(provider, reason, using_stub=True)


def _apollo_configured() -> bool:
    return bool((settings.apollo_api_key or "").strip())


def _anthropic_configured() -> bool:
    return bool((settings.anthropic_api_key or "").strip())


def _apollo_expected() -> bool:
    return settings.discovery_provider.lower() == "apollo" or (
        settings.enrichment_provider.lower() == "apollo"
    )


def _anthropic_expected() -> bool:
    return settings.llm_provider.lower() == "anthropic"


def _snapshot_state(provider: str) -> ProviderState:
    with _lock:
        state = _states.get(provider) or ProviderState(provider=provider)
        return ProviderState(
            provider=state.provider,
            status=state.status,
            message=state.message,
            last_error_at=state.last_error_at,
            last_success_at=state.last_success_at,
            using_stub=state.using_stub,
            configured=state.configured,
            extra=dict(state.extra),
        )


def _apply_config_gates(snapshot: ProviderState, *, expected: bool, configured: bool) -> ProviderState:
    snapshot.configured = configured
    if expected and not configured and snapshot.status == STATUS_OK:
        snapshot.status = STATUS_MISSING_KEY
        snapshot.message = (
            f"No API key configured for {snapshot.provider}. "
            f"Set the key in backend/.env and restart the server."
        )
    return snapshot


def get_provider_health(*, probe: bool = False) -> dict:
    """Return health for Apollo and Anthropic (optionally run live probes)."""
    if probe:
        _probe_apollo()
        _probe_anthropic()

    apollo = _apply_config_gates(
        _snapshot_state("apollo"),
        expected=_apollo_expected(),
        configured=_apollo_configured(),
    )
    anthropic = _apply_config_gates(
        _snapshot_state("anthropic"),
        expected=_anthropic_expected(),
        configured=_anthropic_configured(),
    )

    providers = [_state_to_dict(apollo), _state_to_dict(anthropic)]
    blocking = [
        p
        for p in providers
        if p["status"]
        in (STATUS_NO_CREDITS, STATUS_AUTH_ERROR, STATUS_MISSING_KEY, STATUS_DEGRADED)
    ]
    return {
        "providers": providers,
        "has_blocking_issues": len(blocking) > 0,
        "warnings": [_warning_line(p) for p in blocking],
    }


def active_warnings() -> List[str]:
    """Short warning strings for API responses (regenerate, discovery, etc.)."""
    health = get_provider_health(probe=False)
    return health.get("warnings") or []


def _state_to_dict(state: ProviderState) -> dict:
    return {
        "provider": state.provider,
        "label": "Apollo" if state.provider == "apollo" else "Anthropic",
        "configured": state.configured,
        "expected": _apollo_expected() if state.provider == "apollo" else _anthropic_expected(),
        "status": state.status,
        "message": state.message,
        "using_stub": state.using_stub,
        "last_error_at": state.last_error_at.isoformat() if state.last_error_at else None,
        "last_success_at": state.last_success_at.isoformat() if state.last_success_at else None,
        "extra": state.extra or None,
    }


def _warning_line(p: dict) -> str:
    label = p.get("label") or p.get("provider", "Provider")
    msg = p.get("message") or p.get("status", "issue")
    return f"{label}: {msg}"


def inspect_apollo_response(status_code: int, body: str) -> Optional[str]:
    """If response indicates credits/auth failure, record and return issue type."""
    if status_code < 400:
        return None
    issue = _classify_error_text(body, status_code=status_code)
    if issue == STATUS_ERROR:
        # Ignore generic client errors (bad params, empty search, etc.)
        if status_code not in (402,) and not _CREDIT_RE.search(body or ""):
            return None
    if issue in (STATUS_NO_CREDITS, STATUS_AUTH_ERROR, STATUS_RATE_LIMITED, STATUS_ERROR):
        record_provider_failure("apollo", body, status_code=status_code)
        return issue
    return None


def inspect_anthropic_exception(exc: Exception) -> Optional[str]:
    """Classify and record an Anthropic client error."""
    status = getattr(exc, "status_code", None)
    issue = _classify_error_text(str(exc), status_code=status)
    if issue in (STATUS_NO_CREDITS, STATUS_AUTH_ERROR, STATUS_RATE_LIMITED, STATUS_ERROR):
        record_provider_failure("anthropic", exc, status_code=status)
    return issue


def _probe_apollo() -> None:
    if not _apollo_configured():
        return
    headers = {"x-api-key": settings.apollo_api_key.strip(), "Content-Type": "application/json"}
    base = (settings.apollo_base_url or "https://api.apollo.io/api/v1").rstrip("/")
    try:
        with httpx.Client(timeout=20.0, trust_env=False) as client:
            health = client.get(f"{base}/auth/health", headers=headers)
            if health.status_code >= 400:
                inspect_apollo_response(health.status_code, health.text)
                return
            try:
                usage = client.get(f"{base}/usage_stats/api_usage_stats", headers=headers)
                if usage.status_code >= 400:
                    inspect_apollo_response(usage.status_code, usage.text)
                    return
                data = usage.json() if usage.text else {}
                credits_left = _extract_apollo_credits(data)
                with _lock:
                    state = _states["apollo"]
                    state.extra["credits_hint"] = credits_left
                if credits_left is not None and credits_left <= 0:
                    record_provider_failure(
                        "apollo",
                        "Apollo reports zero API credits remaining.",
                        status_code=402,
                    )
                    return
            except Exception as exc:  # noqa: BLE001
                logger.debug("Apollo usage_stats probe skipped: %s", exc)
            record_provider_success("apollo")
    except httpx.HTTPError as exc:
        record_provider_failure("apollo", exc)


def _extract_apollo_credits(data: Any) -> Optional[int]:
    """Best-effort parse of Apollo usage stats for remaining credits."""
    if not isinstance(data, dict):
        return None
    for key in (
        "credits_remaining",
        "remaining_credits",
        "api_credits_remaining",
        "export_credits_remaining",
    ):
        val = data.get(key)
        if isinstance(val, (int, float)):
            return int(val)
    # Nested shapes vary by plan
    for block in data.values():
        if isinstance(block, dict):
            nested = _extract_apollo_credits(block)
            if nested is not None:
                return nested
    return None


def _probe_anthropic() -> None:
    if not _anthropic_configured():
        return
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        client.messages.create(
            model=settings.anthropic_model,
            max_tokens=8,
            messages=[{"role": "user", "content": "ping"}],
        )
        record_provider_success("anthropic")
    except Exception as exc:  # noqa: BLE001
        inspect_anthropic_exception(exc)
