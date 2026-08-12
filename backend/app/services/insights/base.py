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


def fit_complete_sentences(text: str, limit: int) -> str:
    """Whole sentences up to ``limit`` chars; a hard word-boundary cut (with an
    ellipsis) only as a last resort, so a short note never ends mid-word.

    Used as a safety net around connection-note generation — the primary path
    is a model/template that writes directly to the limit, but this keeps any
    overshoot from ending on an incomplete thought.
    """
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    out = ""
    for chunk in flat.replace("? ", "?|").replace("! ", "!|").replace(". ", ".|").split("|"):
        candidate = (out + " " + chunk).strip() if out else chunk
        if len(candidate) > limit:
            break
        out = candidate
    if not out:
        out = flat[: max(0, limit - 1)].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return out.strip()


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
        style: Optional[dict] = None,
    ) -> OutreachResult:
        """Draft a warm, strategic, personalized outreach email.

        ``style`` is an optional A/B copy directive (hook/structure/cta/tone/length).
        """

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

    def generate_connection_note(
        self,
        *,
        principal: dict,
        person: Optional[dict] = None,
        organization: Optional[dict] = None,
        insight: Optional[dict] = None,
        message_body: Optional[str] = None,
        limit: int = 220,
    ) -> str:
        """Write a short, complete LinkedIn connection-invitation note.

        This is NOT a truncation of ``message_body`` — it is composed as its
        own short punch-line: a specific hook plus one line of relevance,
        fitted to ``limit`` as a complete thought. Default is a deterministic
        template so the pipeline works without an API key; the Anthropic
        provider overrides this with a personalized, model-written note.
        """
        name = ((person or {}).get("name") or "").strip()
        first = name.split()[0] if name else ""
        org = ((organization or {}).get("name") or "").strip()
        insight = insight or {}
        hook = None
        for source in (insight.get("talking_points"), insight.get("key_facts")):
            if source:
                hook = str(source[0]).strip()
                break
        if not hook:
            title = ((person or {}).get("title") or "").strip()
            if title and org:
                hook = f"Your work as {title} at {org} caught my eye."
            elif org:
                hook = f"{org}'s work caught my eye."
            else:
                hook = "Your background caught my eye."
        if hook and hook[-1] not in ".!?":
            hook = f"{hook}."
        greeting = f"Hi {first}, " if first else ""
        note = f"{greeting}{hook} Would love to connect."
        return fit_complete_sentences(note, limit)

    def generate_reply(
        self,
        *,
        principal: dict,
        person: Optional[dict] = None,
        organization: Optional[dict] = None,
        insight: Optional[dict] = None,
        previous: Optional[dict] = None,
        inbound_reply: Optional[str] = None,
    ) -> OutreachResult:
        """Draft a contextual reply to an inbound email (never auto-sends).

        Default template keeps the pipeline working without an API key.
        """
        name = ((person or {}).get("name") or "").strip()
        first = name.split()[0] if name else "there"
        body = (
            f"Hi {first},\n"
            "Thanks for getting back to me — really helpful context. "
            "Happy to keep this short and focus on what would actually be useful "
            "for you.\n"
            "Would a 20-minute call this week or next work?"
        )
        return OutreachResult(
            subject="", body=body, generated_by=f"{self.name} (template reply)"
        )
