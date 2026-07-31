"""Read-only status for the automated daily outreach (backend-only feature).

Exposes ``GET /api/automation/status`` so an operator can confirm the scheduled
runs are alive and see today's sent counts without digging through server logs.
Read-only; safe to poll. Returns quickly (a few cheap counts).
"""
from __future__ import annotations

import threading
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.agent_config import AgentConfig
from app.models.email_draft import EmailDraft
from app.models.enums import EmailStatus
from app.models.principal import Principal
from app.services import automation
from app.services.app_settings import get_setting

router = APIRouter(prefix="/automation", tags=["automation"])

# In-process guard so a manual trigger can't stack two full runs on top of each
# other (each run already stamps last_run_at to block the scheduler; this stops
# a double-click from launching two at once within a worker).
_run_lock = threading.Lock()
_run_thread: threading.Thread | None = None


def _emails_sent_today(db: Session, principal_id: int) -> int:
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.execute(
        select(func.count())
        .select_from(EmailDraft)
        .where(
            EmailDraft.principal_id == principal_id,
            EmailDraft.status.in_([EmailStatus.SENT, EmailStatus.REPLIED]),
            EmailDraft.sent_at.isnot(None),
            EmailDraft.sent_at >= start,
        )
    ).scalar_one()


@router.get("/status")
def automation_status(db: Session = Depends(get_db)) -> dict:
    """Snapshot of the automated daily outreach: config, schedule, today's sends."""
    now = datetime.utcnow()

    # Email campaigns (the seeded "[auto] " AgentConfigs), matched by mailbox so
    # we never touch any other principal.
    email_campaigns = []
    for spec in automation.email_specs():
        principal = db.execute(
            select(Principal).where(Principal.outreach_mailbox_id == spec.mailbox_id)
        ).scalars().first()
        cfg = None
        sent_today = 0
        if principal is not None:
            cfg = db.execute(
                select(AgentConfig).where(
                    AgentConfig.principal_id == principal.id,
                    AgentConfig.name.like(f"{automation.AUTO_PREFIX}%"),
                )
            ).scalars().first()
            sent_today = _emails_sent_today(db, principal.id)
        email_campaigns.append(
            {
                "account": spec.from_email,
                "mailbox": spec.mailbox_id,
                "campaign": spec.campaign.label,
                "seeded": principal is not None and cfg is not None,
                "enabled": bool(cfg.enabled) if cfg else False,
                "last_run_at": (
                    cfg.last_run_at.isoformat() if (cfg and cfg.last_run_at) else None
                ),
                "sent_today": sent_today,
            }
        )

    linkedin_accounts = []
    for spec in automation.linkedin_specs():
        linkedin_accounts.append(
            {
                "label": spec.label,
                "account_id": spec.account_id,
                "campaign": spec.campaign.label,
                "sent_today": (
                    automation._linkedin_sent_today(db, spec.account_id)
                    if spec.account_id
                    else 0
                ),
            }
        )

    return {
        "enabled": settings.automation_enabled,
        "run_on_startup": settings.automation_run_on_startup,
        "now_utc": now.isoformat() + "Z",
        "weekday": now.strftime("%A"),
        "is_weekday": now.weekday() < 5,
        "notify_email": settings.automation_notify_email,
        "schedule": {
            "email_hour_utc": settings.automation_email_hour_utc,
            "linkedin_hour_utc": settings.automation_linkedin_hour_utc,
            "weekdays_only": True,
        },
        "email": {
            "daily_cap_per_account": settings.automation_email_daily_cap,
            "discover_target": settings.automation_email_discover_target,
            "total_sent_today": sum(c["sent_today"] for c in email_campaigns),
            "campaigns": email_campaigns,
        },
        "linkedin": {
            "enabled": settings.automation_linkedin_enabled,
            "daily_cap_per_account": settings.automation_linkedin_daily_cap,
            "send_delay_seconds": settings.automation_linkedin_send_delay_seconds,
            "last_run_date": get_setting("automation_linkedin_last_run"),
            "total_sent_today": sum(a["sent_today"] for a in linkedin_accounts),
            "accounts": linkedin_accounts,
        },
    }


@router.post("/run-now")
def automation_run_now() -> dict:
    """Fire ONE full outreach cycle now (email drip + LinkedIn), on demand.

    Backend trigger only — there is no UI for this. Unlike ``AUTOMATION_RUN_ON_
    STARTUP`` (which is guarded to fire at most once per UTC day so restarts don't
    stack runs), this always launches a run when hit, so you can kick off the
    batch right after a deploy without waiting for the 08:00 UTC schedule. It runs
    exactly what the daily job runs — same per-account caps, dedupe, and pacing —
    so re-hitting it is safe: already-contacted people are skipped and each account
    still stops at its daily cap.
    """
    global _run_thread
    if not settings.automation_enabled:
        return {
            "started": False,
            "reason": "automation is disabled — set AUTOMATION_ENABLED=true first",
        }
    with _run_lock:
        if _run_thread is not None and _run_thread.is_alive():
            return {"started": False, "reason": "a run is already in progress"}
        from app.services.automation import run_all_now

        _run_thread = threading.Thread(
            target=run_all_now, name="automation-run-now-api", daemon=True
        )
        _run_thread.start()
    return {
        "started": True,
        "message": "Outreach run started. Watch GET /api/automation/status; "
        "email.total_sent_today will climb as the drip delivers.",
    }
