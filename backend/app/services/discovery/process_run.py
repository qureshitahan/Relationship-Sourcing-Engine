"""Post-discovery processing: research all prospects, then reveal contact details."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.contact import Contact
from app.models.discovery_run import DiscoveryRun
from app.models.principal import Principal
from app.schemas.entities import BatchInsightSummary, BatchRevealSummary, DiscoveryProcessSummary
from app.services.contacts import RevealNotAllowed, reveal_contact
from app.services.insights.engine import InsightResearchError, generate_insight
from app.services.outreach_goal import outreach_goal_for_contact

logger = logging.getLogger(__name__)

QUALIFIED_RELEVANCE = 50.0


def batch_research_contacts(
    db: Session,
    *,
    principal: Principal,
    contacts: list[Contact],
    skip_existing: bool = True,
    auto_reject_below: float = 0.0,
) -> BatchInsightSummary:
    researched = skipped = failed = qualified = auto_rejected = 0
    errors: list[str] = []

    for contact in contacts:
        if skip_existing and contact.relevance_score is not None:
            skipped += 1
            if contact.relevance_score >= QUALIFIED_RELEVANCE:
                qualified += 1
            continue

        company = db.get(Company, contact.company_id) if contact.company_id else None
        goal = outreach_goal_for_contact(db, contact, principal)
        try:
            insight = generate_insight(
                db,
                principal,
                contact=contact,
                company=company,
                outreach_goal=goal,
            )
            researched += 1
            score = insight.relevance_score
            if score >= QUALIFIED_RELEVANCE:
                qualified += 1
            elif auto_reject_below > 0 and score < auto_reject_below:
                from app.models.enums import ProspectStatus

                contact.status = ProspectStatus.REJECTED
                auto_rejected += 1
            db.commit()
        except InsightResearchError as exc:
            db.rollback()
            failed += 1
            errors.append(f"{contact.name or contact.id}: {exc}")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            failed += 1
            errors.append(f"{contact.name or contact.id}: {exc}")
            logger.warning("Batch research failed for contact %s: %s", contact.id, exc)

    return BatchInsightSummary(
        total=len(contacts),
        researched=researched,
        skipped=skipped,
        failed=failed,
        qualified=qualified,
        auto_rejected=auto_rejected,
        errors=errors[:20],
    )


def batch_reveal_contacts(
    db: Session,
    contacts: list[Contact],
) -> BatchRevealSummary:
    revealed = skipped = failed = 0
    errors: list[str] = []

    need_reveal = [c for c in contacts if not c.email and not c.do_not_contact]
    skipped = len(contacts) - len(need_reveal)

    for contact in need_reveal:
        try:
            reveal_contact(db, contact)
            db.commit()
            revealed += 1
        except RevealNotAllowed as exc:
            db.rollback()
            failed += 1
            errors.append(f"{contact.name or contact.id}: {exc}")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            failed += 1
            errors.append(f"{contact.name or contact.id}: {exc}")
            logger.warning("Batch reveal failed for contact %s: %s", contact.id, exc)

    return BatchRevealSummary(
        total=len(contacts),
        revealed=revealed,
        skipped=skipped,
        failed=failed,
        errors=errors[:20],
    )


def process_discovery_run(
    db: Session,
    run_id: int,
    *,
    principal: Principal | None = None,
    skip_existing_research: bool = False,
) -> DiscoveryProcessSummary:
    """Research then reveal every prospect imported by a discovery run."""
    run = db.get(DiscoveryRun, run_id)
    if run is None:
        raise LookupError(f"Discovery run {run_id} not found")

    if principal is None:
        if run.principal_id:
            principal = db.get(Principal, run.principal_id)
        if principal is None:
            raise ValueError("No principal available for this run")

    contacts = list(
        db.execute(
            select(Contact)
            .where(Contact.discovery_run_id == run_id)
            .order_by(Contact.id)
        ).scalars().all()
    )
    if not contacts:
        empty = BatchInsightSummary(
            total=0, researched=0, skipped=0, failed=0, qualified=0, auto_rejected=0
        )
        return DiscoveryProcessSummary(
            run_id=run_id, research=empty, reveal=BatchRevealSummary(
                total=0, revealed=0, skipped=0, failed=0
            )
        )

    research = batch_research_contacts(
        db,
        principal=principal,
        contacts=contacts,
        skip_existing=skip_existing_research,
        auto_reject_below=0.0,
    )
    # Refresh contacts after research commits.
    contacts = list(
        db.execute(
            select(Contact).where(Contact.discovery_run_id == run_id).order_by(Contact.id)
        ).scalars().all()
    )
    reveal = batch_reveal_contacts(db, contacts)

    return DiscoveryProcessSummary(run_id=run_id, research=research, reveal=reveal)
