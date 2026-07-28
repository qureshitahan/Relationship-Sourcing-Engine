"""Switch the pipeline between the current and cost-optimized modes."""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import optimization

router = APIRouter(prefix="/optimization", tags=["optimization"])


class CapabilityOut(BaseModel):
    key: str
    label: str
    description: str
    enabled: bool
    # True when the capability can change how an email reads, so the UI can warn
    # before it is switched on.
    affects_quality: bool


class OptimizationOut(BaseModel):
    enabled: bool
    research_model: str
    draft_model: str
    capabilities: List[CapabilityOut]


class OptimizationUpdate(BaseModel):
    enabled: Optional[bool] = None
    capabilities: Optional[Dict[str, bool]] = None
    draft_model: Optional[str] = None


_COPY: Dict[str, tuple[str, str, bool]] = {
    "prompt_caching": (
        "Prompt caching",
        "Reuses the cached research and writing instructions across prospects. "
        "Same model and same prompt, so output is unchanged.",
        False,
    ),
    "research_gate": (
        "Skip hopeless prospects",
        "Skips paid research for people whose free title-based fit score is "
        "already too low to qualify. Skipped people are listed in the run.",
        False,
    ),
    "reuse_insight": (
        "Reuse recent research",
        "Reuses a brief from the last 30 days instead of researching the same "
        "person again. The manual Research button always refreshes.",
        False,
    ),
    "adaptive_search": (
        "Smarter search budget",
        "Runs one web search when we already know the prospect's LinkedIn URL, "
        "and the full budget when we still have to find them.",
        False,
    ),
    "cheap_draft_model": (
        "Cheaper writing model",
        "Writes emails with a smaller model. Research always stays on the main "
        "model. This is the only setting that can change how drafts read, so "
        "compare a batch before leaving it on.",
        True,
    ),
}


def _state_out() -> OptimizationOut:
    state = optimization.current_state()
    return OptimizationOut(
        enabled=state.enabled,
        research_model=state.research_model,
        draft_model=optimization.draft_model(),
        capabilities=[
            CapabilityOut(
                key=key,
                label=_COPY[key][0],
                description=_COPY[key][1],
                enabled=state.flags.get(key, False),
                affects_quality=_COPY[key][2],
            )
            for key in optimization.FLAGS
        ],
    )


@router.get("", response_model=OptimizationOut)
def get_optimization() -> OptimizationOut:
    return _state_out()


@router.put("", response_model=OptimizationOut)
def update_optimization(payload: OptimizationUpdate) -> OptimizationOut:
    if payload.capabilities:
        unknown = set(payload.capabilities) - set(optimization.FLAGS)
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown capability: {', '.join(sorted(unknown))}",
            )
        for key, value in payload.capabilities.items():
            optimization.set_flag(key, bool(value))
    if payload.draft_model is not None:
        optimization.set_draft_model(payload.draft_model)
    if payload.enabled is not None:
        optimization.set_flag("enabled", payload.enabled)
    return _state_out()
