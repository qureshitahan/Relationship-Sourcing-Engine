"""Deterministic stub insight provider (no API key required).

Produces plausible, principal-aware insights and outreach so the full flow can be
demoed and tested without calling Anthropic. Copy avoids robotic board-seat pitches.
"""
from __future__ import annotations

from typing import Optional

from app.services.insights.base import DocumentIndex, InsightProvider, InsightResult, OutreachResult


def _first_name(name: Optional[str]) -> str:
    if not name:
        return "there"
    return name.strip().split()[0]


def _org_name(organization: Optional[dict]) -> str:
    return (organization or {}).get("name") or "your firm"


def _title(person: Optional[dict]) -> str:
    return ((person or {}).get("title") or "leader").strip()


class StubInsightProvider(InsightProvider):
    name = "stub"

    _BOARD_SIGNALS = (
        "board", "director", "governance", "audit", "healthcare", "pharma",
        "private equity", "operating", "m&a", "acquisition", "ceo", "cfo",
        "fda", "503b", "sox", "nasdaq", "pe-backed", "value creation",
    )

    def index_document(
        self,
        *,
        text: str,
        filename: str,
        principal: Optional[dict] = None,
    ) -> DocumentIndex:
        cleaned = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        blob = " ".join(cleaned).lower()
        hits = sum(1 for s in self._BOARD_SIGNALS if s in blob)
        score = min(95.0, 25.0 + hits * 8.0)
        if len(cleaned) < 3:
            score = min(score, 20.0)
        facts = [ln for ln in cleaned if 8 <= len(ln) <= 160][:8] if score >= 35 else []
        note = (
            "Stub index: document appears relevant to board/career positioning."
            if score >= 55
            else "Stub index: limited board/career signal detected."
            if score >= 35
            else "Stub index: no clear board-seat or career relevance."
        )
        return DocumentIndex(
            summary=" ".join(cleaned)[:500],
            key_facts=facts,
            themes=[],
            relevance_score=score,
            relevance_note=note,
            generated_by=f"{self.name} (no-LLM extract)",
        )

    def score_relevance(
        self,
        *,
        principal: dict,
        organization: Optional[dict] = None,
        person: Optional[dict] = None,
    ) -> InsightResult:
        org = _org_name(organization)
        person_name = (person or {}).get("name")
        person_title = (person or {}).get("title") or "a senior leader"
        principal_name = principal.get("name") or "the principal"
        sectors = principal.get("target_sectors") or principal.get("focus_areas") or []
        sector_phrase = sectors[0] if sectors else "healthcare"
        value_props = principal.get("value_props") or []
        value_phrase = value_props[0] if value_props else "operational scaling and M&A"

        score = 72.0
        if person_title and any(
            k in person_title.lower() for k in ("ceo", "founder", "partner", "board", "chief")
        ):
            score = 85.0

        who = f"{person_name} ({person_title})" if person_name else org
        return InsightResult(
            relevance_score=score,
            why_relevant=(
                f"{org} operates in {sector_phrase}, which aligns directly with "
                f"{principal_name}'s focus."
            ),
            why_speak_with_principal=(
                f"{principal_name} brings {value_phrase}, directly useful to {who} as they "
                f"pursue growth and consolidation."
            ),
            strategic_connection=(
                f"Overlap between {principal_name}'s operating background and {org}'s "
                f"trajectory in {sector_phrase}."
            ),
            common_ground=(
                f"Shared experience scaling {sector_phrase} platforms and navigating "
                f"private-equity-backed growth."
            ),
            relevant_experience=(
                f"{principal_name}'s track record as a healthcare operator is highly applicable here."
            ),
            signals=["Stub signal: recent growth activity", "Stub signal: sector tailwinds"],
            talking_points=[
                f"How {org} is approaching expansion in {sector_phrase}",
                f"Where {principal_name}'s operating playbook could accelerate outcomes",
                "Potential for a peer exchange on governance and integration",
            ],
            opportunity_type=(principal.get("opportunity_types") or ["networking"])[0],
            generated_by="stub",
        )

    def generate_outreach(
        self,
        *,
        principal: dict,
        person: Optional[dict] = None,
        organization: Optional[dict] = None,
        insight: Optional[dict] = None,
        style: Optional[dict] = None,
    ) -> OutreachResult:
        """Deterministic, per-person template grounded in the insight when present."""
        greeting = _first_name((person or {}).get("name"))
        org = _org_name(organization)
        title = _title(person)
        cred = (
            principal.get("credential_summary")
            or principal.get("headline")
            or "I have spent the last decade inside PE-backed healthcare roll-ups."
        )

        # Prefer a research-grounded hook from the insight.
        insight = insight or {}
        hook = None
        for source in (insight.get("talking_points"), insight.get("key_facts")):
            if source:
                hook = str(source[0]).strip()
                break
        if not hook:
            hook = f"Your work as {title} at {org} caught my attention."

        bridge = (
            f"I work on the operator side of the same space ({cred.split('.')[0].strip()})."
            if cred
            else "I am a healthcare operator who has lived through a few roll-ups."
        )
        subject = f"{org} and your work" if org != "your firm" else "A quick note"
        body = (
            f"Hi {greeting},\n"
            f"{hook}\n"
            f"{bridge}\n"
            f"Open to comparing notes briefly?"
        )
        return OutreachResult(subject=subject, body=body, generated_by="stub")
