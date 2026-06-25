"""Relevance insight generation and listing."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.company import Company
from app.models.contact import Contact
from app.models.enums import ProspectStatus
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.schemas.entities import BatchInsightSummary, Page, RelevanceInsightOut
from app.schemas.requests import InsightBatchGenerateRequest, InsightGenerateRequest
from app.services.insights.engine import InsightResearchError, generate_insight
from app.services.outreach_goal import outreach_goal_for_contact

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/insights", tags=["insights"])

QUALIFIED_RELEVANCE = 50.0


@router.get("", response_model=Page[RelevanceInsightOut])
def list_insights(
    db: Session = Depends(get_db),
    principal_id: Optional[int] = None,
    contact_id: Optional[int] = None,
    company_id: Optional[int] = None,
    min_relevance: Optional[float] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    query = select(RelevanceInsight)
    count_query = select(func.count()).select_from(RelevanceInsight)
    filters = []
    if principal_id is not None:
        filters.append(RelevanceInsight.principal_id == principal_id)
    if contact_id is not None:
        filters.append(RelevanceInsight.contact_id == contact_id)
    if company_id is not None:
        filters.append(RelevanceInsight.company_id == company_id)
    if min_relevance is not None:
        filters.append(RelevanceInsight.relevance_score >= min_relevance)
    for f in filters:
        query = query.where(f)
        count_query = count_query.where(f)
    query = query.order_by(RelevanceInsight.relevance_score.desc()).limit(limit).offset(offset)
    items = db.execute(query).scalars().all()
    total = db.execute(count_query).scalar_one()
    return Page[RelevanceInsightOut](items=items, total=total, limit=limit, offset=offset)


@router.post("/generate", response_model=RelevanceInsightOut, status_code=201)
def generate(payload: InsightGenerateRequest, db: Session = Depends(get_db)):
    principal = db.get(Principal, payload.principal_id)
    if not principal:
        raise HTTPException(status_code=404, detail="Principal not found")
    if payload.contact_id is None and payload.company_id is None:
        raise HTTPException(
            status_code=400, detail="Provide a contact_id and/or company_id"
        )

    contact = db.get(Contact, payload.contact_id) if payload.contact_id else None
    company = db.get(Company, payload.company_id) if payload.company_id else None
    if payload.contact_id and not contact:
        raise HTTPException(status_code=404, detail="Prospect not found")
    if payload.company_id and not company:
        raise HTTPException(status_code=404, detail="Organization not found")

    try:
        insight = generate_insight(db, principal, contact=contact, company=company)
    except InsightResearchError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    db.commit()
    db.refresh(insight)
    return insight


@router.post("/batch-generate", response_model=BatchInsightSummary)
def batch_generate(payload: InsightBatchGenerateRequest, db: Session = Depends(get_db)):
    """Research all prospects in a run (or explicit id list) without opening each profile."""
    principal = db.get(Principal, payload.principal_id)
    if not principal:
        raise HTTPException(status_code=404, detail="Principal not found")
    if payload.discovery_run_id is None and not payload.contact_ids:
        raise HTTPException(
            status_code=400,
            detail="Provide discovery_run_id and/or contact_ids",
        )

    query = select(Contact)
    if payload.discovery_run_id is not None:
        query = query.where(Contact.discovery_run_id == payload.discovery_run_id)
    if payload.contact_ids:
        query = query.where(Contact.id.in_(payload.contact_ids))
    contacts = db.execute(query.order_by(Contact.id)).scalars().all()
    if not contacts:
        raise HTTPException(status_code=404, detail="No prospects matched")

    researched = skipped = failed = qualified = auto_rejected = 0
    errors: list[str] = []

    for contact in contacts:
        if payload.skip_existing and contact.relevance_score is not None:
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
            elif payload.auto_reject_below > 0 and score < payload.auto_reject_below:
                contact.status = ProspectStatus.REJECTED
                auto_rejected += 1
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            failed += 1
            msg = f"{contact.name or contact.id}: {exc}"
            errors.append(msg)
            logger.warning("Batch insight failed for contact %s: %s", contact.id, exc)

    return BatchInsightSummary(
        total=len(contacts),
        researched=researched,
        skipped=skipped,
        failed=failed,
        qualified=qualified,
        auto_rejected=auto_rejected,
        errors=errors[:20],
    )
