"""Rules for whether a prospect is ready for outreach email drafting."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.contact import Contact
from app.models.relevance_insight import RelevanceInsight


def latest_insight(
    db: Session, *, principal_id: int, contact_id: int
) -> RelevanceInsight | None:
    insight = db.execute(
        select(RelevanceInsight)
        .where(
            RelevanceInsight.principal_id == principal_id,
            RelevanceInsight.contact_id == contact_id,
        )
        .order_by(RelevanceInsight.created_at.desc())
    ).scalars().first()
    if insight is not None:
        return insight
    # Cost-friendly reuse: drafting for a principal reusing another
    # principal's discovery run (see discovery_jobs.launch_run_draft's
    # draft_principal_id) has no research of their own for this prospect yet.
    # Reuse whatever research already exists rather than treating "nobody
    # researched this FOR THIS EXACT PRINCIPAL" as "research failed" — that
    # would wrongly block drafting and force an unnecessary re-research.
    return db.execute(
        select(RelevanceInsight)
        .where(RelevanceInsight.contact_id == contact_id)
        .order_by(RelevanceInsight.created_at.desc())
    ).scalars().first()


def research_failed(insight: RelevanceInsight | None) -> bool:
    if insight is None:
        return True
    provider = (insight.generated_by or "").lower()
    return "stub fallback" in provider or provider == "stub"


def outreach_draft_blockers(
    db: Session,
    *,
    principal_id: int,
    contact: Contact,
) -> list[str]:
    """Human-readable reasons drafting should not run (empty = OK to draft)."""
    blockers: list[str] = []
    if not (contact.email or "").strip():
        blockers.append("email not revealed")
    if contact.relevance_score is None:
        blockers.append("not researched")
    insight = latest_insight(db, principal_id=principal_id, contact_id=contact.id)
    if research_failed(insight):
        blockers.append("research failed — retry research first")
    return blockers
