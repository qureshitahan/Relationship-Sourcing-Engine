"""Clear prospect pipeline data for a fresh start (keeps principals + indexed docs)."""
from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.call import Call
from app.models.company import Company
from app.models.contact import Contact
from app.models.discovery_run import DiscoveryRun
from app.models.email_draft import EmailDraft
from app.models.relevance_insight import RelevanceInsight
from app.models.suppression import OutreachHistory


def reset_pipeline(db: Session) -> dict:
    """Delete pipeline data for a fresh start.

    Removes: prospects, insights, email drafts, organizations, discovery run history.
    Keeps: principals, principal documents, search definitions.
    """
    counts = {
        "email_drafts": db.execute(select(func.count()).select_from(EmailDraft)).scalar_one(),
        "calls": db.execute(select(func.count()).select_from(Call)).scalar_one(),
        "outreach_history": db.execute(select(func.count()).select_from(OutreachHistory)).scalar_one(),
        "insights": db.execute(select(func.count()).select_from(RelevanceInsight)).scalar_one(),
        "prospects": db.execute(select(func.count()).select_from(Contact)).scalar_one(),
        "organizations": db.execute(select(func.count()).select_from(Company)).scalar_one(),
        "discovery_runs": db.execute(select(func.count()).select_from(DiscoveryRun)).scalar_one(),
    }

    db.execute(delete(EmailDraft))
    db.execute(delete(Call))
    db.execute(delete(OutreachHistory))
    db.execute(delete(RelevanceInsight))
    db.execute(delete(Contact))
    db.execute(delete(Company))
    db.execute(delete(DiscoveryRun))
    db.commit()

    return {
        "deleted": counts,
        "message": "Pipeline cleared (prospects, drafts, orgs, discovery runs). Principals and indexed documents kept.",
    }
