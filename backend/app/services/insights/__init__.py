"""AI insight + personalization engine.

Pluggable providers behind a common interface. The active provider is chosen by
LLM_PROVIDER. The `stub` provider returns deterministic insights without an API
key so the platform runs end-to-end out of the box.
"""
from app.core.config import settings
from app.services.insights.anthropic_provider import AnthropicInsightProvider
from app.services.insights.base import InsightProvider, InsightResult, OutreachResult
from app.services.insights.stub import StubInsightProvider

_PROVIDERS = {
    "stub": StubInsightProvider,
    "none": StubInsightProvider,
    "anthropic": AnthropicInsightProvider,
}


def get_insight_provider() -> InsightProvider:
    """Return the configured insight provider (defaults to stub)."""
    provider_cls = _PROVIDERS.get(settings.llm_provider, StubInsightProvider)
    return provider_cls()


__all__ = [
    "InsightProvider",
    "InsightResult",
    "OutreachResult",
    "get_insight_provider",
]
