"""Daily campaign rollup for the autonomous agent UI."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Callable, TypeVar

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.agent_config import AgentConfig
from app.models.agent_playbook import AgentPlaybook
from app.models.agent_run import AgentRun
from app.models.contact import Contact
from app.models.email_draft import EmailDraft
from app.models.enums import EmailStatus, ProspectStatus
from app.models.company import Company
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.services.inbound_text import clean_inbound_reply

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _run_history(db: Session, query: Callable[[], T], default: T) -> T:
    """Run an ``agent_runs`` query, degrading to ``default`` if it cannot execute.

    agent_runs is history: nothing else depends on it structurally. So a damaged
    row there must not take down the campaigns list or a campaign dashboard —
    the pages an operator actually works from, and whose real content (people,
    drafts, replies) lives in other tables. Run counters degrade to
    zero/empty and the pages still render; the boot-time repair in
    ``db.session`` rebuilds the table on the next restart.

    Rolls the session back on failure so the caller's later queries still work.

    Catches broadly on purpose: corruption surfaces as ``DatabaseError`` for a
    damaged page but as ``JSONDecodeError`` for a damaged ``summary`` payload,
    and neither should be able to take a page down.
    """
    try:
        return query()
    except Exception:  # noqa: BLE001 - degrading history must never fail a page
        db.rollback()
        logger.warning("agent_runs query failed; showing run history as empty", exc_info=True)
        return default


def _campaign_filter(db: Session, query_ix, query_scan, default):
    """Run a contacts-by-campaign query; on failure retry bypassing the index.

    ``ix_contacts_campaign_id`` was damaged by the same corruption incident as
    agent_runs. The contacts ROWS are intact (unfiltered scans succeed), so on
    an index error we retry with SQLite's ``NOT INDEXED`` clause, which forbids
    the planner from touching ANY index on the table (a bare ``+column`` is not
    enough: for counts the planner may still scan the damaged index as the
    cheapest b-tree) and scans the healthy table instead — correct results,
    slightly slower, no data hidden. If even the scan fails, fall back to
    ``default`` so the page still renders. The boot-time repair rebuilds the
    index; this keeps pages truthful meanwhile.
    """
    try:
        return query_ix()
    except Exception:  # noqa: BLE001 - damaged index
        db.rollback()
        logger.warning("contacts campaign_id index query failed; retrying via table scan")
    try:
        return query_scan()
    except Exception:  # noqa: BLE001 - damage beyond the index
        db.rollback()
        logger.warning("contacts table-scan fallback failed too", exc_info=True)
        return default


def _unresolved_interrupted_run(
    db: Session, campaign_id: int
) -> AgentRun | None:
    """Return an interrupted run that still needs Continue — or None.

    Startup marks dead ``running`` rows as failed with an Interrupted message.
    Once the operator (or daily schedule) has completed a newer run afterward,
    the amber banner must go away so it does not look like the problem returned.
    """
    interrupted = db.execute(
        select(AgentRun)
        .where(
            AgentRun.campaign_id == campaign_id,
            AgentRun.status == "failed",
            AgentRun.error_message.isnot(None),
            AgentRun.error_message.contains("Interrupted"),
        )
        .order_by(AgentRun.created_at.desc())
    ).scalars().first()
    if interrupted is None:
        return None
    cutoff = (
        interrupted.finished_at
        or getattr(interrupted, "updated_at", None)
        or interrupted.created_at
        or interrupted.started_at
    )
    if cutoff is None:
        return interrupted
    later = db.execute(
        select(AgentRun.id)
        .where(
            AgentRun.campaign_id == campaign_id,
            AgentRun.status == "completed",
            AgentRun.started_at.isnot(None),
            AgentRun.started_at >= cutoff,
        )
        .limit(1)
    ).scalar_one_or_none()
    if later is not None:
        return None
    return interrupted


def campaign_dashboard(
    db: Session,
    principal_id: int,
    *,
    days: int = 14,
) -> dict[str, Any]:
    """Aggregate agent runs and email outcomes by calendar day (UTC)."""
    since = datetime.utcnow() - timedelta(days=max(1, days))

    runs = _run_history(
        db,
        lambda: list(
            db.execute(
                select(AgentRun)
                .where(
                    AgentRun.principal_id == principal_id,
                    AgentRun.started_at.isnot(None),
                    AgentRun.started_at >= since,
                )
                .order_by(AgentRun.started_at.desc())
            ).scalars().all()
        ),
        [],
    )

    by_day: dict[date, dict[str, Any]] = defaultdict(
        lambda: {
            "date": None,
            "runs": [],
            "discovered": 0,
            "qualified": 0,
            "rejected": 0,
            "drafted": 0,
            "sent": 0,
            "followups_sent": 0,
            "replies": 0,
            "variant_labels": set(),
        }
    )

    for run in runs:
        day = (run.started_at or datetime.utcnow()).date()
        bucket = by_day[day]
        bucket["date"] = day.isoformat()
        bucket["discovered"] += run.discovered or 0
        bucket["qualified"] += run.qualified or 0
        bucket["rejected"] += run.rejected or 0
        bucket["drafted"] += run.drafted or 0
        bucket["sent"] += run.sent or 0
        bucket["followups_sent"] += run.followups_sent or 0
        variant = (run.summary or {}).get("variant_label")
        if variant:
            bucket["variant_labels"].add(variant)
        bucket["runs"].append(
            {
                "id": run.id,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "discovered": run.discovered,
                "qualified": run.qualified,
                "sent": run.sent,
                "variant_label": variant,
                "objective": (run.summary or {}).get("objective"),
            }
        )

    # Replies attributed to the day they arrived.
    reply_rows = db.execute(
        select(EmailDraft.replied_at)
        .where(
            EmailDraft.principal_id == principal_id,
            EmailDraft.status == EmailStatus.REPLIED,
            EmailDraft.replied_at.isnot(None),
            EmailDraft.replied_at >= since,
        )
    ).all()
    for (replied_at,) in reply_rows:
        if replied_at:
            day = replied_at.date()
            if day in by_day:
                by_day[day]["replies"] += 1
            else:
                bucket = by_day[day]
                bucket["date"] = day.isoformat()
                bucket["replies"] = 1

    days_out = []
    for day in sorted(by_day.keys(), reverse=True):
        row = by_day[day]
        row["variant_labels"] = sorted(row["variant_labels"])
        days_out.append(row)

    totals = {
        "discovered": sum(r.discovered or 0 for r in runs),
        "qualified": sum(r.qualified or 0 for r in runs),
        "sent": sum(r.sent or 0 for r in runs),
        "followups_sent": sum(r.followups_sent or 0 for r in runs),
        "replies": sum(d["replies"] for d in days_out),
        "runs": len(runs),
    }

    return {
        "principal_id": principal_id,
        "days": days_out,
        "totals": totals,
    }


def list_campaigns(db: Session, *, days: int = 14) -> dict[str, Any]:
    """Summarize every campaign (AgentConfig) across active principals."""
    since = datetime.utcnow() - timedelta(days=max(1, days))

    principals = {
        p.id: p
        for p in db.execute(
            select(Principal).where(Principal.is_active.is_(True))
        ).scalars().all()
    }
    if not principals:
        return {"items": [], "running_count": 0}

    pids = list(principals.keys())

    configs = list(
        db.execute(
            select(AgentConfig)
            .where(AgentConfig.principal_id.in_(pids))
            .order_by(AgentConfig.id.asc())
        ).scalars().all()
    )
    if not configs:
        return {"items": [], "running_count": 0}

    campaign_ids = [c.id for c in configs]

    playbook_ids = {c.playbook_id for c in configs if c.playbook_id}
    playbooks: dict[int, AgentPlaybook] = {}
    if playbook_ids:
        for pb in db.execute(
            select(AgentPlaybook).where(AgentPlaybook.id.in_(playbook_ids))
        ).scalars().all():
            playbooks[pb.id] = pb

    # Latest running run per campaign.
    running: dict[int, AgentRun] = {}
    for r in _run_history(
        db,
        lambda: db.execute(
            select(AgentRun).where(
                AgentRun.status == "running",
                AgentRun.campaign_id.in_(campaign_ids),
            )
        ).scalars().all(),
        [],
    ):
        running[r.campaign_id] = r

    # Interrupted runs that still need Continue (no completed run after them).
    needs_continue: dict[int, AgentRun] = {}
    for cid in campaign_ids:
        interrupted = _run_history(db, lambda: _unresolved_interrupted_run(db, cid), None)
        if interrupted is not None:
            needs_continue[cid] = interrupted

    # Sent (and replied implies sent) drafts in the window, per campaign.
    sent_by_campaign = {
        cid: int(cnt or 0)
        for cid, cnt in db.execute(
            select(EmailDraft.campaign_id, func.count(EmailDraft.id))
            .where(
                EmailDraft.campaign_id.in_(campaign_ids),
                EmailDraft.sent_at.isnot(None),
                EmailDraft.sent_at >= since,
            )
            .group_by(EmailDraft.campaign_id)
        ).all()
    }

    replies_by_campaign = {
        cid: int(cnt or 0)
        for cid, cnt in db.execute(
            select(EmailDraft.campaign_id, func.count(EmailDraft.id))
            .where(
                EmailDraft.campaign_id.in_(campaign_ids),
                EmailDraft.status == EmailStatus.REPLIED,
                EmailDraft.replied_at.isnot(None),
                EmailDraft.replied_at >= since,
            )
            .group_by(EmailDraft.campaign_id)
        ).all()
    }

    items: list[dict[str, Any]] = []
    healed = False
    for config in configs:
        principal = principals.get(config.principal_id)
        if principal is None:
            continue
        playbook = playbooks.get(config.playbook_id) if config.playbook_id else None
        run = running.get(config.id)
        interrupted = needs_continue.get(config.id)

        # Daily-off without the paused flag is leftover from the old "Turn off
        # daily" control — treat it as paused so the badge matches reality.
        if playbook and not config.enabled and not config.paused:
            config.paused = True
            healed = True

        if config.paused or not config.enabled:
            status = "paused"
        elif run:
            status = "running"
        elif playbook:
            status = "ready"  # UI label: "Runs daily"
        else:
            status = "draft"

        objective = (playbook.objective_prompt or "").strip() if playbook else ""
        items.append(
            {
                "id": config.id,
                "name": (config.name or (playbook.name if playbook else None) or "Campaign"),
                "principal_id": principal.id,
                "principal_name": principal.name,
                "playbook_id": playbook.id if playbook else None,
                "playbook_name": playbook.name if playbook else None,
                "objective_preview": objective[:160] if objective else None,
                "enabled": bool(config.enabled),
                "paused": bool(config.paused) or not bool(config.enabled),
                "status": status,
                "current_run_id": run.id if run else None,
                "current_run_discovered": run.discovered if run else None,
                "current_run_sent": run.sent if run else None,
                "last_run_at": config.last_run_at,
                "totals_sent_14d": sent_by_campaign.get(config.id, 0),
                "totals_replies_14d": replies_by_campaign.get(config.id, 0),
                "needs_continue": interrupted is not None,
                "interrupted_discovered": (
                    int(interrupted.discovered or 0) if interrupted else 0
                ),
            }
        )

    if healed:
        db.commit()

    return {"items": items, "running_count": len(running)}


def campaign_detail(db: Session, campaign_id: int, *, days: int = 14) -> dict[str, Any]:
    """Per-campaign dashboard: header, funnel totals, reply rate, daily activity."""
    config = db.get(AgentConfig, campaign_id)
    if config is None:
        raise ValueError("Campaign not found")
    principal = db.get(Principal, config.principal_id)
    playbook = db.get(AgentPlaybook, config.playbook_id) if config.playbook_id else None

    since = datetime.utcnow() - timedelta(days=max(1, days))

    runs = _run_history(
        db,
        lambda: list(
            db.execute(
                select(AgentRun)
                .where(
                    AgentRun.campaign_id == campaign_id,
                    AgentRun.started_at.isnot(None),
                    AgentRun.started_at >= since,
                )
                .order_by(AgentRun.started_at.desc())
            ).scalars().all()
        ),
        [],
    )

    by_day: dict[date, dict[str, Any]] = defaultdict(
        lambda: {
            "date": None,
            "runs": [],
            "discovered": 0,
            "qualified": 0,
            "rejected": 0,
            "drafted": 0,
            "sent": 0,
            "followups_sent": 0,
            "replies": 0,
        }
    )
    for run in runs:
        day = (run.started_at or datetime.utcnow()).date()
        bucket = by_day[day]
        bucket["date"] = day.isoformat()
        bucket["discovered"] += run.discovered or 0
        bucket["qualified"] += run.qualified or 0
        bucket["rejected"] += run.rejected or 0
        bucket["drafted"] += run.drafted or 0
        bucket["sent"] += run.sent or 0
        bucket["followups_sent"] += run.followups_sent or 0
        bucket["runs"].append(
            {
                "id": run.id,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "discovered": run.discovered,
                "qualified": run.qualified,
                "sent": run.sent,
            }
        )

    reply_rows = db.execute(
        select(EmailDraft.replied_at).where(
            EmailDraft.campaign_id == campaign_id,
            EmailDraft.status == EmailStatus.REPLIED,
            EmailDraft.replied_at.isnot(None),
            EmailDraft.replied_at >= since,
        )
    ).all()
    for (replied_at,) in reply_rows:
        if replied_at:
            d = replied_at.date()
            bucket = by_day[d]
            if bucket["date"] is None:
                bucket["date"] = d.isoformat()
            bucket["replies"] += 1

    days_out = [by_day[d] for d in sorted(by_day.keys(), reverse=True)]

    # Funnel totals over the window (drafts are the source of truth for sends).
    sent_total = int(
        db.execute(
            select(func.count(EmailDraft.id)).where(
                EmailDraft.campaign_id == campaign_id,
                EmailDraft.sent_at.isnot(None),
                EmailDraft.sent_at >= since,
            )
        ).scalar_one()
    )
    replies_total = int(
        db.execute(
            select(func.count(EmailDraft.id)).where(
                EmailDraft.campaign_id == campaign_id,
                EmailDraft.status == EmailStatus.REPLIED,
                EmailDraft.replied_at.isnot(None),
                EmailDraft.replied_at >= since,
            )
        ).scalar_one()
    )
    drafts_total = int(
        db.execute(
            select(func.count(EmailDraft.id)).where(
                EmailDraft.campaign_id == campaign_id,
            )
        ).scalar_one()
    )
    # Drafts sitting in review (awaiting human approval before they can send).
    pending_drafts = int(
        db.execute(
            select(func.count(EmailDraft.id)).where(
                EmailDraft.campaign_id == campaign_id,
                EmailDraft.status == EmailStatus.DRAFT,
            )
        ).scalar_one()
    )

    totals = {
        "discovered": _campaign_contact_count(db, campaign_id),
        "qualified": _campaign_qualified_count(db, campaign_id, config),
        "rejected": _campaign_rejected_count(db, campaign_id, config),
        "drafted": drafts_total,
        "sent": sent_total,
        "followups_sent": sum(r.followups_sent or 0 for r in runs),
        "replies": replies_total,
        "runs": len(runs),
    }
    reply_rate = (replies_total / sent_total) if sent_total else 0.0

    def _run_snapshot(run: AgentRun | None) -> dict[str, Any] | None:
        if run is None:
            return None
        summary = run.summary or {}
        return {
            "id": run.id,
            "status": run.status,
            "trigger": run.trigger,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "discovered": run.discovered,
            "qualified": run.qualified,
            "rejected": run.rejected,
            "drafted": run.drafted,
            "sent": run.sent,
            "followups_sent": run.followups_sent,
            "error_message": run.error_message,
            "stages": summary.get("stages") or [],
            "people": summary.get("people") or [],
            "errors": (summary.get("errors") or [])[:20],
        }

    # Last completed (or most recent) run for the funnel visual.
    last_run = _run_history(
        db,
        lambda: db.execute(
            select(AgentRun)
            .where(AgentRun.campaign_id == campaign_id)
            .order_by(AgentRun.created_at.desc())
        ).scalars().first(),
        None,
    )
    last_run_out = _run_snapshot(last_run)

    # A failed run left by a server restart — only surface while unresolved.
    # After Continue (or any completed run afterward), the banner goes away.
    interrupted_run = _run_history(
        db, lambda: _unresolved_interrupted_run(db, campaign_id), None
    )

    running_run = _run_history(
        db,
        lambda: db.execute(
            select(AgentRun).where(
                AgentRun.campaign_id == campaign_id,
                AgentRun.status == "running",
            )
        ).scalars().first(),
        None,
    )
    current_run_out = _run_snapshot(running_run)

    # Daily-off without the paused flag is leftover from the old "Turn off
    # daily" control — treat it as paused so the badge matches reality.
    if playbook and not config.enabled and not config.paused:
        config.paused = True
        db.commit()

    if config.paused or not config.enabled:
        status = "paused"
    elif running_run:
        status = "running"
    elif playbook:
        status = "ready"  # UI label: "Runs daily"
    else:
        status = "draft"

    # Queued sends that a pause/stop would pull back.
    scheduled_count = int(
        db.execute(
            select(func.count(EmailDraft.id)).where(
                EmailDraft.campaign_id == campaign_id,
                EmailDraft.status == EmailStatus.SCHEDULED,
            )
        ).scalar_one()
    )
    # Approved but not queued — schedulable without any further review.
    approved_unscheduled = int(
        db.execute(
            select(func.count(EmailDraft.id)).where(
                EmailDraft.campaign_id == campaign_id,
                EmailDraft.status == EmailStatus.APPROVED,
                EmailDraft.scheduled_at.is_(None),
            )
        ).scalar_one()
    )

    return {
        "id": config.id,
        "name": (config.name or (playbook.name if playbook else None) or "Campaign"),
        "principal_id": config.principal_id,
        "principal_name": principal.name if principal else "",
        "enabled": bool(config.enabled),
        "paused": bool(config.paused) or not bool(config.enabled),
        "scheduled_count": scheduled_count,
        "approved_unscheduled": approved_unscheduled,
        "status": status,
        "objective": (playbook.objective_prompt if playbook else None),
        "playbook_id": playbook.id if playbook else None,
        "playbook_name": playbook.name if playbook else None,
        "criteria": (playbook.criteria if playbook and playbook.criteria else {}),
        "mailbox_daily_cap": int(getattr(principal, "mailbox_daily_cap", 50) or 50)
        if principal
        else 50,
        "discover_target": int(config.discover_target or 0),
        "auto_send": bool(config.auto_send),
        "auto_schedule": bool(getattr(config, "auto_schedule", True)),
        "pending_drafts": pending_drafts,
        "last_run_at": config.last_run_at,
        "current_run_id": running_run.id if running_run else None,
        "totals": totals,
        "reply_rate": round(reply_rate, 4),
        "days": days_out,
        "last_run": last_run_out,
        "current_run": current_run_out,
        "interrupted_run": _run_snapshot(interrupted_run),
    }


def _campaign_contact_count(db: Session, campaign_id: int) -> int:
    return int(
        _campaign_filter(
            db,
            lambda: db.execute(
                select(func.count(Contact.id)).where(Contact.campaign_id == campaign_id)
            ).scalar_one(),
            lambda: db.execute(
                text("SELECT count(*) FROM contacts NOT INDEXED WHERE campaign_id = :cid"),
                {"cid": campaign_id},
            ).scalar_one(),
            0,
        )
    )


def _campaign_qualified_count(db: Session, campaign_id: int, config: AgentConfig) -> int:
    floor = float(config.qualify_min or 40)
    return int(
        _campaign_filter(
            db,
            lambda: db.execute(
                select(func.count(Contact.id)).where(
                    Contact.campaign_id == campaign_id,
                    Contact.relevance_score.isnot(None),
                    Contact.relevance_score >= floor,
                )
            ).scalar_one(),
            lambda: db.execute(
                text(
                    "SELECT count(*) FROM contacts NOT INDEXED WHERE campaign_id = :cid "
                    "AND relevance_score IS NOT NULL AND relevance_score >= :floor"
                ),
                {"cid": campaign_id, "floor": floor},
            ).scalar_one(),
            0,
        )
    )


def _campaign_rejected_count(db: Session, campaign_id: int, config: AgentConfig) -> int:
    return int(
        _campaign_filter(
            db,
            lambda: db.execute(
                select(func.count(Contact.id)).where(
                    Contact.campaign_id == campaign_id,
                    Contact.status == ProspectStatus.REJECTED,
                )
            ).scalar_one(),
            lambda: db.execute(
                text(
                    "SELECT count(*) FROM contacts NOT INDEXED "
                    "WHERE campaign_id = :cid AND status = :st"
                ),
                {"cid": campaign_id, "st": ProspectStatus.REJECTED},
            ).scalar_one(),
            0,
        )
    )


def campaign_prospects(db: Session, campaign_id: int) -> dict[str, Any]:
    """People owned by this campaign (not shared with other campaigns)."""
    config = db.get(AgentConfig, campaign_id)
    if config is None:
        raise ValueError("Campaign not found")

    contacts = _campaign_filter(
        db,
        lambda: list(
            db.execute(
                select(Contact).where(Contact.campaign_id == campaign_id)
            ).scalars().all()
        ),
        lambda: list(
            db.execute(
                select(Contact).from_statement(
                    text(
                        "SELECT * FROM contacts NOT INDEXED WHERE campaign_id = :cid"
                    ).bindparams(cid=campaign_id)
                )
            ).scalars().all()
        ),
        [],
    )
    contact_ids = {c.id for c in contacts}

    if not contact_ids:
        return {"campaign_id": campaign_id, "items": [], "total": 0}

    # Per-person pipeline status, newest run first.
    #
    # This used to read only the MOST RECENT run, so anyone surfaced by an
    # earlier run had no status at all and the page showed them as "not
    # contacted" — including people that run had explicitly rejected. Walk the
    # runs newest-first instead, and keep the first status found for each person.
    runs = _run_history(
        db,
        lambda: list(
            db.execute(
                select(AgentRun)
                .where(AgentRun.campaign_id == campaign_id)
                .order_by(AgentRun.created_at.desc())
            ).scalars().all()
        ),
        [],
    )
    pipeline_status: dict[int, str] = {}
    for run_row in runs:
        for p in (run_row.summary or {}).get("people") or []:
            pid = p.get("id")
            if pid is not None and int(pid) not in pipeline_status:
                pipeline_status[int(pid)] = str(p.get("status") or "discovered")

    company_ids = {c.company_id for c in contacts if c.company_id}
    companies: dict[int, Company] = {}
    if company_ids:
        for co in db.execute(
            select(Company).where(Company.id.in_(company_ids))
        ).scalars().all():
            companies[co.id] = co

    # Latest draft per contact for THIS campaign.
    drafts = list(
        db.execute(
            select(EmailDraft)
            .where(
                EmailDraft.campaign_id == campaign_id,
                EmailDraft.contact_id.in_(contact_ids),
            )
            .order_by(EmailDraft.created_at.desc())
        ).scalars().all()
    )
    latest_draft: dict[int, EmailDraft] = {}
    for d in drafts:
        if d.contact_id is not None and d.contact_id not in latest_draft:
            latest_draft[d.contact_id] = d

    items: list[dict[str, Any]] = []
    for contact in contacts:
        company = companies.get(contact.company_id) if contact.company_id else None
        d = latest_draft.get(contact.id)
        items.append(
            {
                "contact_id": contact.id,
                "name": contact.name,
                "title": contact.title,
                "company_name": company.name if company else None,
                "email": contact.email,
                "location": contact.location,
                "status": contact.status,
                # Runs before the summary-persistence fix recorded nothing, so
                # fall back to the prospect's own stored status, which the
                # orchestrator has always written.
                "pipeline_status": pipeline_status.get(contact.id) or contact.status,
                "relevance_score": contact.relevance_score,
                "email_status": d.status if d else None,
                "email_subject": d.subject if d else None,
                "sent_at": d.sent_at if d else None,
                "replied_at": d.replied_at if d else None,
                "reply_snippet": (
                    clean_inbound_reply(d.reply_snippet or d.reply_body or "")
                    if d and (d.reply_snippet or d.reply_body)
                    else None
                ),
                "last_email_id": d.id if d else None,
            }
        )

    # Replied first, then sent, then drafted, then the rest; by relevance.
    status_rank = {
        EmailStatus.REPLIED: 0,
        EmailStatus.SENT: 1,
        EmailStatus.SCHEDULED: 2,
        EmailStatus.APPROVED: 3,
        EmailStatus.DRAFT: 4,
    }
    items.sort(
        key=lambda it: (
            status_rank.get(it["email_status"], 9),
            -(it["relevance_score"] or 0),
        )
    )

    return {"campaign_id": campaign_id, "items": items, "total": len(items)}
