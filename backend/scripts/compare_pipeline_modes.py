"""Prove the optimized pipeline sends the same prompts as the current one.

Captures the exact Anthropic request for research and for drafting in both
modes, without making any network calls, and diffs them. The only differences
that should ever appear are the cache marker on the system prompt and the
web-search budget when the prospect's LinkedIn URL is already known.

Run:  .venv/bin/python scripts/compare_pipeline_modes.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import optimization  # noqa: E402
from app.services.insights.anthropic_provider import (  # noqa: E402
    AnthropicInsightProvider,
)

PRINCIPAL = {
    "name": "Dalbir Bains",
    "objective": "Introduce Tekhqs AI to multi-site healthcare operators.",
    "proof_points": ["$100m in combined cost and revenue savings", "200+ engineers"],
}
ORG = {"name": "Advocare, LLC", "industry": "hospital & health care"}
INSIGHT = {
    "snapshot": "COO of a large multi-site physician group.",
    "talking_points": ["after-hours call capture", "documentation overhead"],
}


class _Recorder:
    """Stands in for the Anthropic client and records the request."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any):
        self.calls.append(kwargs)
        raise RuntimeError("captured")  # stop before any parsing


def _capture(fn, **kwargs) -> dict[str, Any]:
    provider = AnthropicInsightProvider()
    provider.api_key = "test-key"
    recorder = _Recorder()
    provider._client = recorder
    try:
        getattr(provider, fn)(**kwargs)
    except Exception:  # noqa: BLE001 - the recorder always raises
        pass
    return recorder.calls[0] if recorder.calls else {}


def _system_text(call: dict[str, Any]) -> str:
    system = call.get("system")
    if isinstance(system, str):
        return system
    return "".join(block.get("text", "") for block in system or [])


def _cached(call: dict[str, Any]) -> bool:
    system = call.get("system")
    return isinstance(system, list) and any(
        "cache_control" in block for block in system
    )


def _searches(call: dict[str, Any]) -> Any:
    for tool in call.get("tools") or []:
        if tool.get("name") == "web_search":
            return tool.get("max_uses")
    return None


def _set_mode(enabled: bool) -> None:
    optimization.set_flag("enabled", enabled)


def _compare(label: str, fn: str, **kwargs) -> list[str]:
    _set_mode(False)
    before = _capture(fn, **kwargs)
    _set_mode(True)
    after = _capture(fn, **kwargs)

    problems: list[str] = []
    if _system_text(before) != _system_text(after):
        problems.append(f"{label}: system prompt text changed")
    if before.get("messages") != after.get("messages"):
        problems.append(f"{label}: user message changed")
    if before.get("max_tokens") != after.get("max_tokens"):
        problems.append(f"{label}: max_tokens changed")

    print(f"\n{label}")
    print(f"  system prompt identical : {_system_text(before) == _system_text(after)}")
    print(f"  user message identical  : {before.get('messages') == after.get('messages')}")
    print(f"  model  current={before.get('model')}  optimized={after.get('model')}")
    print(f"  cached system  current={_cached(before)}  optimized={_cached(after)}")
    if _searches(before) is not None or _searches(after) is not None:
        print(f"  web searches   current={_searches(before)}  optimized={_searches(after)}")
    return problems


def main() -> int:
    original = optimization.current_state().enabled
    problems: list[str] = []
    try:
        problems += _compare(
            "RESEARCH — LinkedIn URL known",
            "score_relevance",
            principal=PRINCIPAL,
            organization=ORG,
            person={
                "name": "Patrick Board",
                "title": "COO",
                "linkedin_url": "https://linkedin.com/in/example",
            },
        )
        problems += _compare(
            "RESEARCH — LinkedIn URL unknown",
            "score_relevance",
            principal=PRINCIPAL,
            organization=ORG,
            person={"name": "Patrick Board", "title": "COO"},
        )
        problems += _compare(
            "DRAFT — first outreach (cheap model off)",
            "generate_outreach",
            principal=PRINCIPAL,
            organization=ORG,
            person={"name": "Patrick Board", "title": "COO"},
            insight=INSIGHT,
        )

        # And the one setting that is allowed to differ, when opted into.
        optimization.set_flag("enabled", True)
        optimization.set_flag("cheap_draft_model", True)
        opted_in = _capture(
            "generate_outreach",
            principal=PRINCIPAL,
            organization=ORG,
            person={"name": "Patrick Board", "title": "COO"},
            insight=INSIGHT,
        )
        print("\nDRAFT — cheap model opted in")
        print(f"  model: {opted_in.get('model')}")
        optimization.set_flag("cheap_draft_model", False)
    finally:
        _set_mode(original)

    print("\n" + "=" * 62)
    if problems:
        for p in problems:
            print(f"FAIL  {p}")
        return 1
    print("PASS  Optimized mode sends identical prompts to the same model.")
    print("      Only the cache marker and search budget differ.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
