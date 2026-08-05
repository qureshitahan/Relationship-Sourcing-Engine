"""Prospect (people) listing, detail, insights, and approval."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.company import Company
from app.models.contact import Contact
from app.models.discovery_run import DiscoveryRun
from app.models.email_draft import EmailDraft
from app.models.enums import AuditAction
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.schemas.entities import BatchRevealSummary, Page, ProspectOut, RelevanceInsightOut
from app.schemas.requests import (
    ProspectApprovalRequest,
    ProspectBatchRevealRequest,
    ProspectStatusRequest,
)
from app.services.audit import log_action
from app.services.contacts import RevealNotAllowed, ensure_discovery_fit, reveal_contact
from app.services.discovery.process_run import batch_reveal_contacts
from app.services.insights.engine import InsightResearchError, generate_insight
from app.services.outreach_eligibility import outreach_draft_blockers
from app.services.outreach_goal import outreach_goal_for_contact

router = APIRouter(prefix="/prospects", tags=["prospects"])


def _principal_for_research(db: Session, contact: Contact) -> Optional[Principal]:
    """Pick the principal to research a prospect against.

    Prefer a principal already linked via an existing insight; otherwise fall
    back to the first principal in the system (single-principal deployments).
    """
    insight = db.execute(
        select(RelevanceInsight)
        .where(RelevanceInsight.contact_id == contact.id)
        .order_by(RelevanceInsight.created_at.desc())
    ).scalars().first()
    if insight and insight.principal_id:
        principal = db.get(Principal, insight.principal_id)
        if principal:
            return principal
    return db.execute(select(Principal).order_by(Principal.id.asc())).scalars().first()


def _rollup_outreach_status(rows: list[tuple[int, str, object, object]]) -> dict[int, str]:
    """Map contact_id → best outreach stage across all their email drafts.

    A follow-up draft must not hide that the initial email was already sent.
    Priority: replied > sent > scheduled > approved > draft.
    """
    by_contact: dict[int, list[tuple[str, object, object]]] = {}
    for cid, status, sent_at, replied_at in rows:
        by_contact.setdefault(cid, []).append((status, sent_at, replied_at))

    out: dict[int, str] = {}
    for cid, drafts in by_contact.items():
        if any(st == "replied" or replied_at for st, _, replied_at in drafts):
            out[cid] = "replied"
        elif any(sent_at for _, sent_at, _ in drafts):
            out[cid] = "sent"
        else:
            priority = {"scheduled": 4, "approved": 3, "draft": 2}
            best = max(
                (st for st, _, _ in drafts),
                key=lambda s: priority.get(s, 0),
            )
            out[cid] = best
    return out


@router.get("", response_model=Page[ProspectOut])
def list_prospects(
    db: Session = Depends(get_db),
    company_id: Optional[int] = None,
    discovery_run_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    bulk_campaign_id: Optional[int] = None,
    role_category: Optional[str] = None,
    status: Optional[str] = None,
    approved: Optional[bool] = None,
    min_relevance: Optional[float] = None,
    researched: Optional[bool] = None,
    search: Optional[str] = None,
    sort: str = "relevance",
    limit: int = Query(50, le=1000),
    offset: int = 0,
):
    query = select(Contact)
    count_query = select(func.count()).select_from(Contact)
    filters = []
    if company_id is not None:
        filters.append(Contact.company_id == company_id)
    if discovery_run_id is not None:
        filters.append(Contact.discovery_run_id == discovery_run_id)
    if campaign_id is not None:
        filters.append(Contact.campaign_id == campaign_id)
    # People pasted into a bulk campaign were never discovered or researched, so
    # they'd only be noise on the prospect sheet unless explicitly asked for.
    if bulk_campaign_id is not None:
        filters.append(Contact.bulk_campaign_id == bulk_campaign_id)
    else:
        filters.append(Contact.bulk_campaign_id.is_(None))
    if role_category:
        filters.append(Contact.role_category == role_category)
    if status:
        filters.append(Contact.status == status)
    if approved is not None:
        filters.append(Contact.approved_for_outreach.is_(approved))
    if min_relevance is not None:
        filters.append(Contact.relevance_score >= min_relevance)
    if researched is True:
        filters.append(Contact.relevance_score.isnot(None))
    elif researched is False:
        filters.append(Contact.relevance_score.is_(None))
    if search:
        filters.append(Contact.name.ilike(f"%{search}%"))
    for f in filters:
        query = query.where(f)
        count_query = count_query.where(f)

    if sort == "usefulness":
        order = Contact.usefulness_score.desc()
    elif sort == "recent":
        order = Contact.created_at.desc()
    else:
        order = Contact.relevance_score.desc().nullslast()
    query = query.order_by(order).limit(limit).offset(offset)
    items = db.execute(query).scalars().all()
    total = db.execute(count_query).scalar_one()

    # Attach rolled-up outreach status per prospect (furthest stage reached).
    contact_ids = [c.id for c in items]
    if contact_ids:
        rows = db.execute(
            select(
                EmailDraft.contact_id,
                EmailDraft.status,
                EmailDraft.sent_at,
                EmailDraft.replied_at,
            ).where(EmailDraft.contact_id.in_(contact_ids))
        ).all()
        latest = _rollup_outreach_status(rows)
        for c in items:
            setattr(c, "outreach_status", latest.get(c.id))

        # Attach the provider that produced each prospect's latest insight, so the
        # UI can flag stub-fallback scores (real research did not actually run).
        irows = db.execute(
            select(
                RelevanceInsight.contact_id, RelevanceInsight.generated_by
            )
            .where(RelevanceInsight.contact_id.in_(contact_ids))
            .order_by(RelevanceInsight.contact_id, RelevanceInsight.created_at.desc())
        ).all()
        prov: dict[int, str] = {}
        for cid, gb in irows:
            if cid not in prov:
                prov[cid] = gb
        for c in items:
            setattr(c, "insight_provider", prov.get(c.id))

    return Page[ProspectOut](items=items, total=total, limit=limit, offset=offset)


@router.post("/batch-reveal", response_model=BatchRevealSummary)
def batch_reveal(payload: ProspectBatchRevealRequest, db: Session = Depends(get_db)):
    """Reveal email/phone for many prospects (consumes Apollo credits)."""
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
    return batch_reveal_contacts(db, contacts)


@router.get("/export.csv")
def export_prospects(
    db: Session = Depends(get_db),
    discovery_run_id: Optional[int] = None,
    role_category: Optional[str] = None,
    status: Optional[str] = None,
    approved: Optional[bool] = None,
    min_relevance: Optional[float] = None,
    search: Optional[str] = None,
    sort: str = "usefulness",
):
    """Export the prospect sheet as CSV (respects the same filters as the list)."""
    query = select(Contact)
    if discovery_run_id is not None:
        query = query.where(Contact.discovery_run_id == discovery_run_id)
    if role_category:
        query = query.where(Contact.role_category == role_category)
    if status:
        query = query.where(Contact.status == status)
    if approved is not None:
        query = query.where(Contact.approved_for_outreach.is_(approved))
    if min_relevance is not None:
        query = query.where(Contact.relevance_score >= min_relevance)
    if search:
        query = query.where(Contact.name.ilike(f"%{search}%"))
    if sort == "recent":
        query = query.order_by(Contact.created_at.desc())
    elif sort == "relevance":
        query = query.order_by(Contact.relevance_score.desc().nullslast())
    else:
        query = query.order_by(Contact.usefulness_score.desc().nullslast())

    rows = db.execute(query).scalars().all()
    # Resolve employer names in one pass.
    company_ids = {c.company_id for c in rows if c.company_id}
    companies = {
        c.id: c.name
        for c in (
            db.execute(select(Company).where(Company.id.in_(company_ids))).scalars().all()
            if company_ids
            else []
        )
    }

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "name", "title", "role_category", "seniority", "board_fit",
        "relevance_score", "company", "email", "email_status", "phone",
        "linkedin_url", "location", "status", "approved_for_outreach", "rank_reason",
    ])
    for c in rows:
        writer.writerow([
            c.id, c.name, c.title or "", c.role_category or "", c.seniority or "",
            c.usefulness_score if c.usefulness_score is not None else "",
            c.relevance_score if c.relevance_score is not None else "",
            companies.get(c.company_id, ""), c.email or "", c.email_status or "",
            c.phone or "", c.linkedin_url or "", c.location or "", c.status,
            "yes" if c.approved_for_outreach else "no", (c.rank_reason or "").replace("\n", " "),
        ])
    buf.seek(0)
    filename = "prospects.csv"
    if discovery_run_id is not None:
        filename = f"prospects_run_{discovery_run_id}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{prospect_id}", response_model=ProspectOut)
def get_prospect(prospect_id: int, db: Session = Depends(get_db)):
    contact = db.get(Contact, prospect_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Prospect not found")
    rows = db.execute(
        select(
            EmailDraft.contact_id,
            EmailDraft.status,
            EmailDraft.sent_at,
            EmailDraft.replied_at,
        ).where(EmailDraft.contact_id == prospect_id)
    ).all()
    setattr(contact, "outreach_status", _rollup_outreach_status(rows).get(prospect_id))
    return contact


@router.get("/{prospect_id}/insights", response_model=list[RelevanceInsightOut])
def prospect_insights(prospect_id: int, db: Session = Depends(get_db)):
    rows = db.execute(
        select(RelevanceInsight)
        .where(RelevanceInsight.contact_id == prospect_id)
        .order_by(RelevanceInsight.relevance_score.desc())
    ).scalars().all()
    return rows


@router.post("/{prospect_id}/reveal", response_model=ProspectOut)
def reveal_prospect(prospect_id: int, db: Session = Depends(get_db)):
    """Reveal email/phone/real name for a single prospect (consumes Apollo credits)."""
    contact = db.get(Contact, prospect_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Prospect not found")
    try:
        ensure_discovery_fit(db, contact)
        reveal_contact(db, contact)
    except RevealNotAllowed as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    db.refresh(contact)
    return contact


@router.post("/{prospect_id}/research", response_model=ProspectOut)
def research_prospect(prospect_id: int, db: Session = Depends(get_db)):
    """Run deep LLM research for one prospect on demand (consumes LLM credits).

    In bulk mode the agent skips up-front research to save cost; use this to
    qualify a specific person (e.g. after they reply, or before a manual reach).
    """
    contact = db.get(Contact, prospect_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Prospect not found")
    principal = _principal_for_research(db, contact)
    if not principal:
        raise HTTPException(status_code=400, detail="No principal available to research against.")
    company = db.get(Company, contact.company_id) if contact.company_id else None
    try:
        generate_insight(
            db,
            principal,
            contact=contact,
            company=company,
            outreach_goal=outreach_goal_for_contact(db, contact, principal),
        )
    except InsightResearchError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    db.commit()
    db.refresh(contact)
    return contact


@router.post("/{prospect_id}/approval", response_model=ProspectOut)
def set_approval(
    prospect_id: int, payload: ProspectApprovalRequest, db: Session = Depends(get_db)
):
    contact = db.get(Contact, prospect_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Prospect not found")

    if payload.approved_for_outreach:
        principal = _principal_for_research(db, contact)
        if principal is None:
            raise HTTPException(status_code=400, detail="No principal configured")

        # Approval is the one action the user takes — research and email/LinkedIn
        # reveal are no longer separate manual steps. If this prospect isn't
        # ready yet, do the work here (best effort) before re-checking, instead
        # of just rejecting the approval and making the user go run two other
        # buttons first.
        blockers = outreach_draft_blockers(
            db, principal_id=principal.id, contact=contact
        )
        if "email not revealed" in blockers:
            try:
                ensure_discovery_fit(db, contact)
                reveal_contact(db, contact)
                db.commit()
            except RevealNotAllowed as exc:
                db.rollback()
                raise HTTPException(status_code=400, detail=str(exc))
            except Exception:  # noqa: BLE001 - best effort; re-checked below
                db.rollback()
        if "not researched" in blockers or "research failed — retry research first" in blockers:
            company = db.get(Company, contact.company_id) if contact.company_id else None
            try:
                generate_insight(
                    db,
                    principal,
                    contact=contact,
                    company=company,
                    outreach_goal=outreach_goal_for_contact(db, contact, principal),
                )
                db.commit()
            except Exception:  # noqa: BLE001 - best effort; re-checked below
                db.rollback()

        blockers = outreach_draft_blockers(
            db, principal_id=principal.id, contact=contact
        )
        # A prospect reachable on LinkedIn (a personal /in/ profile) can be
        # approved without a revealed email (email drafting still enforces its
        # own email requirement). Company pages don't count — they can't be
        # messaged. This lets multi-channel outreach not miss anyone.
        from app.services.linkedin_providers import public_identifier_from_url

        if public_identifier_from_url(contact.linkedin_url or ""):
            blockers = [b for b in blockers if b != "email not revealed"]
        if blockers:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot approve: {', '.join(blockers)}. Automatic research/reveal "
                    "was attempted but could not resolve this — Apollo or Anthropic may "
                    "not have data for this prospect."
                ),
            )

    contact.approved_for_outreach = payload.approved_for_outreach
    log_action(
        db,
        AuditAction.PROSPECT_APPROVAL,
        entity_type="contact",
        entity_id=contact.id,
        actor=payload.approved_by or "user",
        summary=f"approved_for_outreach={payload.approved_for_outreach}",
    )
    db.commit()
    db.refresh(contact)
    return contact


@router.post("/{prospect_id}/status", response_model=ProspectOut)
def set_status(
    prospect_id: int, payload: ProspectStatusRequest, db: Session = Depends(get_db)
):
    contact = db.get(Contact, prospect_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Prospect not found")
    contact.status = payload.status
    log_action(
        db,
        AuditAction.PROSPECT_APPROVAL,
        entity_type="contact",
        entity_id=contact.id,
        actor=payload.actor or "user",
        summary=f"status -> {payload.status}",
    )
    db.commit()
    db.refresh(contact)
    return contact
