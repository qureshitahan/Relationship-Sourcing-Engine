"""Organization listing, detail, enrichment, and insights."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.company import Company
from app.models.relevance_insight import RelevanceInsight
from app.schemas.entities import OrganizationOut, Page, ProspectOut, RelevanceInsightOut
from app.schemas.requests import EnrichRequest
from app.services.contacts import enrich_and_find_contacts

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("", response_model=Page[OrganizationOut])
def list_organizations(
    db: Session = Depends(get_db),
    search: Optional[str] = None,
    company_type: Optional[str] = None,
    enrichment_status: Optional[str] = None,
    discovery_run_id: Optional[int] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    query = select(Company)
    count_query = select(func.count()).select_from(Company)
    filters = []
    if search:
        filters.append(Company.name.ilike(f"%{search}%"))
    if company_type:
        filters.append(Company.company_type == company_type)
    if enrichment_status:
        filters.append(Company.enrichment_status == enrichment_status)
    if discovery_run_id is not None:
        filters.append(Company.discovery_run_id == discovery_run_id)
    for f in filters:
        query = query.where(f)
        count_query = count_query.where(f)

    query = query.order_by(Company.created_at.desc()).limit(limit).offset(offset)
    items = db.execute(query).scalars().all()
    total = db.execute(count_query).scalar_one()
    return Page[OrganizationOut](items=items, total=total, limit=limit, offset=offset)


@router.get("/{organization_id}", response_model=OrganizationOut)
def get_organization(organization_id: int, db: Session = Depends(get_db)):
    company = db.get(Company, organization_id)
    if not company:
        raise HTTPException(status_code=404, detail="Organization not found")
    return company


@router.get("/{organization_id}/insights", response_model=list[RelevanceInsightOut])
def organization_insights(organization_id: int, db: Session = Depends(get_db)):
    rows = db.execute(
        select(RelevanceInsight)
        .where(RelevanceInsight.company_id == organization_id)
        .order_by(RelevanceInsight.relevance_score.desc())
    ).scalars().all()
    return rows


@router.post("/{organization_id}/enrich", response_model=list[ProspectOut])
def enrich_organization(
    organization_id: int,
    payload: Optional[EnrichRequest] = None,
    db: Session = Depends(get_db),
):
    """Enrich firmographics and discover ranked prospects at this organization."""
    company = db.get(Company, organization_id)
    if not company:
        raise HTTPException(status_code=404, detail="Organization not found")
    max_contacts = payload.max_contacts if payload else None
    contacts = enrich_and_find_contacts(db, company, max_contacts=max_contacts)
    db.commit()
    for c in contacts:
        db.refresh(c)
    return contacts
