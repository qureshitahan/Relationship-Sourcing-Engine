"""Delete a single discovery run and its prospects (frees dedup for future searches)."""
from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models.agent_run import AgentRun
from app.models.call import Call
from app.models.company import Company
from app.models.contact import Contact
from app.models.discovery_run import DiscoveryRun
from app.models.email_draft import EmailDraft
from app.models.relevance_insight import RelevanceInsight
from app.models.suppression import OutreachHistory


def delete_discovery_run(db: Session, run_id: int) -> dict:
    """Remove one discovery run and all prospects imported by it.

    Deletes contacts from this run (plus their drafts, insights, calls). People
    removed here are no longer deduped — they can appear again in a new search.
    Orphan organizations with no remaining contacts are removed too.
    """
    run = db.get(DiscoveryRun, run_id)
    if run is None:
        raise LookupError(f"Discovery run {run_id} not found")

    contact_ids = list(
        db.execute(select(Contact.id).where(Contact.discovery_run_id == run_id)).scalars().all()
    )
    company_ids = {
        cid
        for cid in db.execute(
            select(Contact.company_id).where(
                Contact.discovery_run_id == run_id,
                Contact.company_id.isnot(None),
            )
        ).scalars().all()
        if cid is not None
    }

    deleted: dict[str, int] = {
        "prospects": 0,
        "email_drafts": 0,
        "insights": 0,
        "calls": 0,
        "organizations": 0,
    }

    if contact_ids:
        deleted["email_drafts"] = db.execute(
            delete(EmailDraft).where(EmailDraft.contact_id.in_(contact_ids))
        ).rowcount or 0
        deleted["calls"] = db.execute(
            delete(Call).where(Call.contact_id.in_(contact_ids))
        ).rowcount or 0
        db.execute(delete(OutreachHistory).where(OutreachHistory.contact_id.in_(contact_ids)))
        deleted["insights"] = db.execute(
            delete(RelevanceInsight).where(RelevanceInsight.contact_id.in_(contact_ids))
        ).rowcount or 0
        deleted["prospects"] = db.execute(
            delete(Contact).where(Contact.discovery_run_id == run_id)
        ).rowcount or 0

    db.execute(
        update(AgentRun)
        .where(AgentRun.discovery_run_id == run_id)
        .values(discovery_run_id=None)
    )
    db.execute(
        update(Company)
        .where(Company.discovery_run_id == run_id)
        .values(discovery_run_id=None)
    )

    for company_id in company_ids:
        remaining = db.execute(
            select(func.count()).select_from(Contact).where(Contact.company_id == company_id)
        ).scalar_one()
        if remaining:
            continue
        db.execute(delete(EmailDraft).where(EmailDraft.company_id == company_id))
        db.execute(delete(RelevanceInsight).where(RelevanceInsight.company_id == company_id))
        db.execute(delete(Call).where(Call.company_id == company_id))
        db.execute(delete(OutreachHistory).where(OutreachHistory.company_id == company_id))
        if db.execute(delete(Company).where(Company.id == company_id)).rowcount:
            deleted["organizations"] += 1

    db.delete(run)
    db.commit()

    return {
        "run_id": run_id,
        "deleted": deleted,
        "message": (
            f"Deleted run #{run_id} and {deleted['prospects']} prospect(s). "
            "Those people can appear again in future discovery searches."
        ),
    }
