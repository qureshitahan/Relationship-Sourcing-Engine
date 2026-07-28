"""Runtime switch between the current pipeline and the cost-optimized one.

The optimized pipeline is opt-in and reversible from the UI so quality can be
compared side by side against the pipeline that is known to produce good
research and good drafts.

Each capability is a separate flag, grouped by how much it can affect output:

  Identical output (billing/plumbing only)
    - prompt_caching: cache the static system prompt; same model, same prompt.

  Fewer calls, same output for anyone who still gets researched
    - research_gate: skip LLM research for contacts whose rule-based fit score
      is clearly too low to ever be drafted.
    - reuse_insight: don't re-research someone who already has a fresh insight
      (including when they reply).

  Slightly less searching, same hook material in the common case
    - adaptive_search: one web search when we already know the LinkedIn URL,
      two when we still have to find the person.

  Can change writing style — OFF by default, opt in only after comparing
    - cheap_draft_model: write emails with a cheaper model. Research always
      stays on the main model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from app.core.config import settings
from app.services.app_settings import get_setting, set_setting

_PREFIX = "optimization."
MASTER_KEY = f"{_PREFIX}enabled"

# Flags that turn on with the master switch. cheap_draft_model is deliberately
# absent: it is the only one that can change how an email reads.
_DEFAULT_ON = ("prompt_caching", "research_gate", "reuse_insight", "adaptive_search")
_DEFAULT_OFF = ("cheap_draft_model",)
FLAGS = _DEFAULT_ON + _DEFAULT_OFF


@dataclass
class OptimizationState:
    """Which pipeline is active and which capabilities are on."""

    enabled: bool = False
    flags: Dict[str, bool] = field(default_factory=dict)
    draft_model: str = ""
    research_model: str = ""
    research_gate_min: float = 0.0

    def is_on(self, flag: str) -> bool:
        """True only when the master switch and the individual flag are on."""
        return bool(self.enabled and self.flags.get(flag, False))


def _read_bool(key: str, default: bool) -> bool:
    raw = get_setting(f"{_PREFIX}{key}")
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def current_state() -> OptimizationState:
    """Read the live optimization settings."""
    enabled = _read_bool("enabled", False)
    flags = {name: _read_bool(name, True) for name in _DEFAULT_ON}
    flags.update({name: _read_bool(name, False) for name in _DEFAULT_OFF})
    return OptimizationState(
        enabled=enabled,
        flags=flags,
        draft_model=(get_setting(f"{_PREFIX}draft_model") or settings.optimized_draft_model),
        research_model=settings.anthropic_model,
        research_gate_min=settings.research_gate_min,
    )


def set_flag(key: str, value: bool) -> None:
    set_setting(f"{_PREFIX}{key}", "true" if value else "false")


def set_draft_model(model: str) -> None:
    set_setting(f"{_PREFIX}draft_model", (model or "").strip())


def is_on(flag: str) -> bool:
    """Convenience check for a single capability."""
    return current_state().is_on(flag)


def draft_model() -> str:
    """Model to write emails with — the main model unless opted into the cheap one."""
    state = current_state()
    if state.is_on("cheap_draft_model") and state.draft_model:
        return state.draft_model
    return settings.anthropic_model


def web_search_max_uses(*, has_linkedin_url: bool) -> int:
    """How many web searches research may run for one person.

    With a known LinkedIn URL the model opens it directly, so one search is
    normally enough for the snapshot and key facts a draft hook needs. Without
    one it still has to find the person first, so the full budget applies.
    """
    configured = int(settings.insight_web_search_max_uses)
    if configured <= 0:
        return configured
    if not is_on("adaptive_search"):
        return configured
    return 1 if has_linkedin_url else configured
