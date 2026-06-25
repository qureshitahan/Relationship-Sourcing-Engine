"""Insight + personalization provider interface and result shapes.

The insight engine answers, for a principal and a discovered organization/person:
why is this relevant, why should they speak with the principal, what is the
strategic connection, what common ground exists, and what experience is most
relevant. It also drafts warm, strategic (non-cold-sales) outreach.

Providers are decoupled from the ORM: callers pass plain dict contexts.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class InsightResult:
    relevance_score: float
    why_relevant: Optional[str] = None
    why_speak_with_principal: Optional[str] = None
    strategic_connection: Optional[str] = None
    common_ground: Optional[str] = None
    relevant_experience: Optional[str] = None
    signals: List[str] = field(default_factory=list)
    talking_points: List[str] = field(default_factory=list)
    opportunity_type: Optional[str] = None
    generated_by: Optional[str] = None
    # Concise, research-grounded additions:
    # one-line "who this person actually is" snapshot.
    snapshot: Optional[str] = None
    # 3-6 researched bullets: {"text", "source_url?", "source_title?"} or legacy strings.
    key_facts: List[Any] = field(default_factory=list)
    # Web/LinkedIn sources used to ground the research: [{"title","url"}].
    sources: List[dict] = field(default_factory=list)
    # The person's real LinkedIn profile URL, if research recovered it.
    linkedin_url: Optional[str] = None
    # False when LinkedIn/CRM data conflict or the person could not be confirmed.
    identity_verified: bool = True
    identity_warnings: List[str] = field(default_factory=list)


@dataclass
class OutreachResult:
    subject: str
    body: str
    generated_by: Optional[str] = None


@dataclass
class DocumentIndex:
    """LLM-extracted context from one principal document."""

    summary: str = ""
    key_facts: List[str] = field(default_factory=list)  # verbatim proof points
    themes: List[str] = field(default_factory=list)      # short tags for retrieval
    doc_type: Optional[str] = None
    # 0–100: fit for board-seat sourcing / principal positioning (not file quality).
    relevance_score: float = 50.0
    relevance_note: Optional[str] = None
    generated_by: Optional[str] = None


class InsightProvider(ABC):
    """Implement this to add a new AI insight/personalization backend."""

    name: str = "base"

    def index_document(
        self,
        *,
        text: str,
        filename: str,
        principal: Optional[dict] = None,
    ) -> "DocumentIndex":
        """Extract a summary, verbatim proof points, and themes from a document.

        Default is a deterministic, no-LLM extraction so the pipeline works
        without an API key; the Anthropic provider overrides this with Claude.
        """
        cleaned = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        summary = " ".join(cleaned)[:500]
        # Keep the most "fact-like" lines (short, contain a digit or proper noun-ish).
        facts = [ln for ln in cleaned if 8 <= len(ln) <= 160][:8]
        return DocumentIndex(
            summary=summary,
            key_facts=facts,
            themes=[],
            generated_by=f"{self.name} (no-LLM extract)",
        )

    @abstractmethod
    def score_relevance(
        self,
        *,
        principal: dict,
        organization: Optional[dict] = None,
        person: Optional[dict] = None,
    ) -> InsightResult:
        """Produce a strategic relevance assessment for a prospect/org."""

    @abstractmethod
    def generate_outreach(
        self,
        *,
        principal: dict,
        person: Optional[dict] = None,
        organization: Optional[dict] = None,
        insight: Optional[dict] = None,
    ) -> OutreachResult:
        """Draft a warm, strategic, personalized outreach email."""

    def generate_followup(
        self,
        *,
        principal: dict,
        person: Optional[dict] = None,
        organization: Optional[dict] = None,
        insight: Optional[dict] = None,
        previous: Optional[dict] = None,
        step: int = 2,
    ) -> OutreachResult:
        """Draft a short, polite follow-up to an unanswered email.

        Default is a deterministic template so the pipeline works without an
        API key; the Anthropic provider overrides this with Claude.
        """
        name = ((person or {}).get("name") or "").strip()
        first = name.split()[0] if name else "there"
        body = (
            f"Hi {first},\n"
            "Circling back on my note in case it slipped past a busy inbox. "
            "I'd still value the chance to compare notes briefly.\n"
            "Worth a quick hello?"
        )
        return OutreachResult(
            subject="", body=body, generated_by=f"{self.name} (template followup)"
        )
