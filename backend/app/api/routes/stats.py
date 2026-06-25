"""Dashboard stats + recent audit log."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.audit import AuditLog
from app.models.call import Call
from app.models.company import Company
from app.models.contact import Contact
from app.models.discovery_run import DiscoveryRun
from app.models.email_draft import EmailDraft
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.schemas.entities import AuditLogOut, DashboardStats, ProviderHealthOut
from app.services.provider_health import get_provider_health
from app.services.reset_pipeline import reset_pipeline

router = APIRouter(tags=["stats"])

HIGH_RELEVANCE_THRESHOLD = 75.0


@router.get("/stats", response_model=DashboardStats)
def dashboard_stats(db: Session = Depends(get_db)):
    def count(model, *where):
        q = select(func.count()).select_from(model)
        for w in where:
            q = q.where(w)
        return db.execute(q).scalar_one()

    status_rows = db.execute(
        select(Contact.status, func.count()).group_by(Contact.status)
    ).all()
    prospects_by_status = {status: cnt for status, cnt in status_rows}

    role_rows = db.execute(
        select(Contact.role_category, func.count()).group_by(Contact.role_category)
    ).all()
    prospects_by_role = {(role or "other"): cnt for role, cnt in role_rows}

    email_status_rows = db.execute(
        select(EmailDraft.status, func.count()).group_by(EmailDraft.status)
    ).all()
    emails_by_status = {status: cnt for status, cnt in email_status_rows}

    # Outreach funnel. "Sent" = anything that actually left the outbox (has a
    # sent timestamp), which also covers replies. Opened/replied are subsets.
    emails_sent = count(EmailDraft, EmailDraft.sent_at.is_not(None))
    emails_opened = count(EmailDraft, EmailDraft.open_count > 0)
    emails_replied = count(EmailDraft, EmailDraft.status == "replied")
    open_rate = round(emails_opened / emails_sent, 3) if emails_sent else 0.0
    reply_rate = round(emails_replied / emails_sent, 3) if emails_sent else 0.0

    return DashboardStats(
        principals_total=count(Principal),
        organizations_total=count(Company),
        prospects_total=count(Contact),
        prospects_by_status=prospects_by_status,
        prospects_by_role=prospects_by_role,
        insights_total=count(RelevanceInsight),
        high_relevance_prospects=count(
            Contact, Contact.relevance_score >= HIGH_RELEVANCE_THRESHOLD
        ),
        email_drafts_total=count(EmailDraft),
        calls_total=count(Call),
        discovery_runs_total=count(DiscoveryRun),
        prospects_approved=count(Contact, Contact.approved_for_outreach.is_(True)),
        prospects_researched=count(Contact, Contact.relevance_score.is_not(None)),
        emails_sent=emails_sent,
        emails_opened=emails_opened,
        emails_replied=emails_replied,
        open_rate=open_rate,
        reply_rate=reply_rate,
        emails_by_status=emails_by_status,
    )


@router.get("/provider-health", response_model=ProviderHealthOut)
def provider_health(probe: bool = Query(False, description="Run a live API check (may use minimal credits)")):
    """Surface Apollo / Anthropic credit, auth, and stub-fallback issues for the UI."""
    data = get_provider_health(probe=probe)
    return ProviderHealthOut(**data)


@router.get("/audit", response_model=list[AuditLogOut])
def recent_audit(db: Session = Depends(get_db), limit: int = Query(50, le=200)):
    rows = db.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    ).scalars().all()
    return rows


@router.post("/reset-pipeline")
def reset_pipeline_data(
    confirm: bool = Query(False, description="Must be true to delete all prospects/insights/drafts"),
    db: Session = Depends(get_db),
):
    """Clear prospects, insights, and outreach drafts for a fresh start.

    Keeps principals, indexed documents, organizations, and discovery run history.
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Pass ?confirm=true to delete all prospects, insights, and email drafts.",
        )
    return reset_pipeline(db)
