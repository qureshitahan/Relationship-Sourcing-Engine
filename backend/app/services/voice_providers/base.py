"""Voice provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class PlaceCallResult:
    placed: bool
    provider: str
    provider_call_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class CallPlacementContext:
    """Per-call context passed to the voice provider."""

    call_id: int
    to_number: str
    script: str
    principal_name: str
    prospect_name: str
    prospect_title: Optional[str] = None
    company_name: Optional[str] = None
    insight_snapshot: Optional[str] = None
    talking_points: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class VoiceProvider(ABC):
    name = "base"

    @abstractmethod
    def place_call(self, *, ctx: CallPlacementContext) -> PlaceCallResult:
        ...
