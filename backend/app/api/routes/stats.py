"""Dashboard stats + recent audit log."""
from __future__ import annotations

import logging
from typing import Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.audit import AuditLog
from app.models.call import Call
from app.models.company import Company
from app.models.contact import Contact
from app.models.discovery_run import DiscoveryRun
from app.models.email_draft import EmailDraft
from app.models.enums import LinkedInStatus
from app.models.linkedin_follower import (
    FollowerSendStatus,
    LinkedInFollower,
    LinkedInFollowerSend,
)
from app.models.linkedin_message import LinkedInMessage
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.schemas.entities import (
    AuditLogOut,
    DashboardStats,
    LinkedInAccountStats,
    ProviderHealthOut,
)
from app.services.linkedin_account_names import resolved_names as resolved_account_names
from app.services.provider_health import get_provider_health
from app.services.reset_pipeline import reset_pipeline

router = APIRouter(tags=["stats"])

logger = logging.getLogger(__name__)

T = TypeVar("T")

HIGH_RELEVANCE_THRESHOLD = 75.0


def _optional(db: Session, query: Callable[[], T], default: T) -> T:
    """Run an added-on stat, degrading to ``default`` if it cannot execute.

    The email/prospect figures below are the ones this dashboard has always
    shown. The LinkedIn figures were added afterwards, and their tables come
    from ``create_all``, which ``init_db`` deliberately allows to fail without
    raising (see ``db.session``). So a missing or damaged LinkedIn table must not
    be able to take down a dashboard that worked before those numbers existed:
    the LinkedIn tiles fall back to zero and every pre-existing figure still
    renders.

    Rolls the session back on failure so the caller's later queries still work.
    """
    try:
        return query()
    except Exception:  # noqa: BLE001 - an added stat must never fail the page
        db.rollback()
        logger.warning("optional dashboard stat failed; reporting it as empty", exc_info=True)
        return default


def _linkedin_by_account(db: Session) -> list[LinkedInAccountStats]:
    """Split LinkedIn outcomes by the account that sent them.

    A single blended acceptance/reply rate hides the thing a team actually needs:
    one account warming up, or one getting throttled, is invisible once averaged
    with the others. Every figure here is the per-account form of a total already
    reported above; nothing is redefined.

    Attribution is exact rather than inferred. ``from_account`` is stamped when a
    message is sent, so every row that left carries its sender and unsent drafts
    carry none — those are omitted instead of being attributed to a default
    account they may never send from. Follower DMs carry their own
    ``account_id`` and are reported as a separate column, never merged into the
    prospect reply rate.
    """
    prospect_dm = LinkedInMessage.follower_id.is_(None)
    sent_from = LinkedInMessage.from_account.is_not(None)

    def _grouped(*where) -> dict[str, int]:
        query = (
            select(LinkedInMessage.from_account, func.count())
            .where(prospect_dm, sent_from)
            .group_by(LinkedInMessage.from_account)
        )
        for w in where:
            query = query.where(w)
        return {acct: int(n or 0) for acct, n in db.execute(query).all() if acct}

    invited = _grouped(LinkedInMessage.invitation_sent_at.is_not(None))
    # Acceptance has no flag of its own. This mirrors ``invite_stats`` in
    # api/routes/linkedin.py exactly — delivery after an invitation, plus the
    # case where the profile came back 1st-degree but the auto-DM itself failed.
    # Kept identical on purpose: the per-account rows must add up to the
    # acceptance rate the LinkedIn page already reports, not a second opinion.
    accepted = _grouped(
        LinkedInMessage.invitation_sent_at.is_not(None),
        or_(
            LinkedInMessage.sent_at.is_not(None),
            LinkedInMessage.connected.is_(True),
        ),
    )
    sent = _grouped(LinkedInMessage.sent_at.is_not(None))
    replied = _grouped(LinkedInMessage.status == LinkedInStatus.REPLIED)

    follower_sent = {
        acct: int(n or 0)
        for acct, n in db.execute(
            select(LinkedInFollowerSend.account_id, func.count())
            .where(LinkedInFollowerSend.status == FollowerSendStatus.SENT)
            .group_by(LinkedInFollowerSend.account_id)
        ).all()
        if acct
    }

    account_ids = (
        set(invited) | set(accepted) | set(sent) | set(replied) | set(follower_sent)
    )

    rows = [
        LinkedInAccountStats(
            account_id=acct,
            invited=invited.get(acct, 0),
            accepted=accepted.get(acct, 0),
            acceptance_rate=(
                round(accepted.get(acct, 0) / invited[acct], 3) if invited.get(acct) else 0.0
            ),
            sent=sent.get(acct, 0),
            replied=replied.get(acct, 0),
            reply_rate=(
                round(replied.get(acct, 0) / sent[acct], 3) if sent.get(acct) else 0.0
            ),
            follower_dms_sent=follower_sent.get(acct, 0),
        )
        for acct in account_ids
    ]
    # Busiest first, so the account carrying the outreach leads the table.
    rows.sort(key=lambda r: (-(r.sent + r.follower_dms_sent), -r.invited, r.account_id))
    return rows


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

    # --- LinkedIn funnel (prospect-driven messages only) ---
    # ``follower_id IS NULL`` is how the LinkedIn module itself separates prospect
    # outreach from Followers-module DMs (see api/routes/linkedin.py), so reusing
    # it keeps these totals equal to what the LinkedIn page lists. Follower DMs
    # are counted separately further down rather than folded in here: a bulk
    # follower blast would otherwise swamp the prospect funnel.
    prospect_dm = LinkedInMessage.follower_id.is_(None)

    def _linkedin_counts() -> dict:
        return {
            "drafts": count(
                LinkedInMessage, prospect_dm, LinkedInMessage.status == LinkedInStatus.DRAFT
            ),
            # Timestamps, not statuses: an accepted invitation moves the row on to
            # "sent", so counting ``status == invite_sent`` would report only the
            # invitations still outstanding, not how many were actually sent.
            "invited": count(
                LinkedInMessage, prospect_dm, LinkedInMessage.invitation_sent_at.is_not(None)
            ),
            # Mirrors emails_sent: anything that actually left, replies included.
            "sent": count(LinkedInMessage, prospect_dm, LinkedInMessage.sent_at.is_not(None)),
            "replied": count(
                LinkedInMessage, prospect_dm, LinkedInMessage.status == LinkedInStatus.REPLIED
            ),
        }

    linkedin = _optional(
        db, _linkedin_counts, {"drafts": 0, "invited": 0, "sent": 0, "replied": 0}
    )
    linkedin_sent = linkedin["sent"]
    linkedin_replied = linkedin["replied"]
    linkedin_reply_rate = (
        round(linkedin_replied / linkedin_sent, 3) if linkedin_sent else 0.0
    )

    linkedin_by_status = _optional(
        db,
        lambda: {
            status: cnt
            for status, cnt in db.execute(
                select(LinkedInMessage.status, func.count())
                .where(prospect_dm)
                .group_by(LinkedInMessage.status)
            ).all()
        },
        {},
    )

    # --- Followers module, kept apart from the funnel above ---
    # Only confirmed sends count. A "claimed" row means the outcome is unknown
    # (see models/linkedin_follower.py) and must not be reported as delivered.
    follower_dms_sent = _optional(
        db,
        lambda: count(
            LinkedInFollowerSend, LinkedInFollowerSend.status == FollowerSendStatus.SENT
        ),
        0,
    )
    followers_total = _optional(db, lambda: count(LinkedInFollower), 0)
    linkedin_by_account = _optional(db, lambda: _linkedin_by_account(db), [])
    linkedin_account_names = _optional(db, resolved_account_names, {})

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
        linkedin_drafts=linkedin["drafts"],
        linkedin_invited=linkedin["invited"],
        linkedin_sent=linkedin_sent,
        linkedin_replied=linkedin_replied,
        linkedin_reply_rate=linkedin_reply_rate,
        linkedin_by_status=linkedin_by_status,
        follower_dms_sent=follower_dms_sent,
        followers_total=followers_total,
        linkedin_by_account=linkedin_by_account,
        linkedin_account_names=linkedin_account_names,
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
