"""Stopping, pausing, and resuming a campaign's outbound mail.

Halting an agent run only stops *new* work — anything already SCHEDULED would
still be delivered, either by this app's scheduler or (for Outlook deferred
sends) by Exchange itself. These helpers pull those queued sends back so
"stop"/"pause" means nothing more goes out.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.email_draft import EmailDraft
from app.models.enums import EmailStatus

logger = logging.getLogger(__name__)


@dataclass
class UnscheduleResult:
    """What happened when we pulled back a campaign's queued sends."""

    cancelled: int = 0
    failed_ids: List[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed_ids

    def message(self) -> str:
        if not self.cancelled and not self.failed_ids:
            return "No scheduled emails were waiting to go out."
        parts = [f"Cancelled {self.cancelled} scheduled email(s)."]
        if self.failed_ids:
            parts.append(
                f"Could not cancel {len(self.failed_ids)} already queued in Outlook "
                f"(ids: {', '.join(str(i) for i in self.failed_ids)}) — check the "
                "mailbox Outbox/Sent Items."
            )
        return " ".join(parts)


def unschedule_drafts(db: Session, drafts: List[EmailDraft]) -> UnscheduleResult:
    """Revert SCHEDULED drafts to APPROVED so they will not be sent.

    Outlook-deferred copies are deleted at the provider first; without that,
    Exchange delivers them regardless of anything we change locally.
    """
    from app.services.email_providers import provider_for_mailbox, resolve_mailbox

    result = UnscheduleResult()
    for draft in drafts:
        if draft.status != EmailStatus.SCHEDULED:
            continue
        if draft.outlook_scheduled and draft.provider_message_id:
            provider = provider_for_mailbox(resolve_mailbox(draft.from_mailbox))
            try:
                cancelled = provider.cancel_scheduled(
                    remote_message_id=draft.provider_message_id
                )
            except Exception as exc:  # noqa: BLE001 - one bad draft must not stop the rest
                logger.warning("Cancel of Outlook draft %s failed: %s", draft.id, exc)
                cancelled = False
            if not cancelled:
                result.failed_ids.append(draft.id)
                continue
            draft.provider_message_id = None
            draft.conversation_id = None
            draft.internet_message_id = None
        draft.status = EmailStatus.APPROVED
        draft.scheduled_at = None
        draft.outlook_scheduled = False
        result.cancelled += 1
    return result


def unschedule_campaign_emails(db: Session, campaign_id: int) -> UnscheduleResult:
    """Pull back every queued send belonging to one campaign."""
    drafts = db.execute(
        select(EmailDraft).where(
            EmailDraft.campaign_id == campaign_id,
            EmailDraft.status == EmailStatus.SCHEDULED,
        )
    ).scalars().all()
    return unschedule_drafts(db, drafts)


def reschedule_campaign_emails(db: Session, campaign_id: int) -> int:
    """Re-queue a campaign's approved-but-unscheduled emails with AI timing.

    Schedules the *entire* approved backlog in one click, packed across days so
    each day stays within the principal's shared mailbox cap (e.g. 120/day).
    Today's remaining capacity is used first; the rest spill into tomorrow,
    the day after, and so on. Returns how many were scheduled.
    """
    import random
    from datetime import datetime, timedelta

    from sqlalchemy import func

    from app.core.config import settings
    from app.models.agent_config import AgentConfig
    from app.models.contact import Contact
    from app.models.principal import Principal
    from app.services.agent.experiments import EXPLORE_EPSILON, best_send_bucket
    from app.services.agent.send_timing import (
        AB_SEND_BUCKETS,
        personalized_send_time_utc,
        staggered_send_times_utc,
    )

    config = db.get(AgentConfig, campaign_id)
    if config is None or config.paused:
        return 0
    principal = db.get(Principal, config.principal_id)
    if principal is None:
        return 0

    candidates = db.execute(
        select(EmailDraft)
        .where(
            EmailDraft.campaign_id == campaign_id,
            EmailDraft.status == EmailStatus.APPROVED,
            EmailDraft.scheduled_at.is_(None),
        )
        .order_by(EmailDraft.id)
    ).scalars().all()
    if not candidates:
        return 0

    sendable: List[EmailDraft] = []
    for draft in candidates:
        contact = db.get(Contact, draft.contact_id) if draft.contact_id else None
        if not contact or not contact.email or contact.do_not_contact:
            continue
        sendable.append(draft)
    if not sendable:
        return 0

    # Pack across days using the shared mailbox cap. Cap of 0 means "unlimited"
    # for this re-queue path (still stagger times so nothing fires as one burst).
    from app.services.agent.orchestrator import _sent_today

    cap = max(0, int(getattr(principal, "mailbox_daily_cap", None) or 0))
    day_cap = cap if cap > 0 else max(len(sendable), 1)

    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    already_queued_today = int(
        db.execute(
            select(func.count())
            .select_from(EmailDraft)
            .where(
                EmailDraft.principal_id == principal.id,
                EmailDraft.status == EmailStatus.SCHEDULED,
                EmailDraft.scheduled_at.isnot(None),
                EmailDraft.scheduled_at >= start,
                EmailDraft.scheduled_at < end,
            )
        ).scalar_one()
    )
    sent_today = _sent_today(db, principal.id)

    auto_schedule = bool(getattr(config, "auto_schedule", True))
    winning_bucket = best_send_bucket(db, principal)
    delay = max(0, int(settings.send_batch_delay_seconds or 120))
    gap_minutes = max(1, delay // 60) if delay else 2

    # Precompute fixed-window times for the whole backlog when auto_schedule is off.
    fixed_times: List = []
    if not auto_schedule:
        fixed_times = staggered_send_times_utc(
            count=len(sendable),
            timezone=config.timezone or "America/New_York",
            start_hour=int(getattr(config, "send_window_start_local", None) or 9),
            end_hour=int(getattr(config, "send_window_end_local", None) or 17),
            gap_minutes=gap_minutes,
        )

    now = datetime.utcnow()
    scheduled = 0

    # Seed today's occupancy so we don't double-book the remaining cap.
    # Keyed by UTC date — close enough for packing; send times themselves stay
    # recipient-local via personalized_send_time_utc.
    from collections import defaultdict

    day_counts: dict = defaultdict(int)
    day_counts[start.date()] = already_queued_today + sent_today

    for global_idx, draft in enumerate(sendable):
        contact = db.get(Contact, draft.contact_id)
        bucket_index: Optional[int] = None
        if auto_schedule:
            if winning_bucket is not None and random.random() < (1 - EXPLORE_EPSILON):
                bucket_index = winning_bucket
            else:
                bucket_index = global_idx % len(AB_SEND_BUCKETS)

            # Walk forward day by day until we find one under the daily cap.
            day_offset = 0
            when = now
            for _ in range(60):  # safety: never loop forever
                intended = (now + timedelta(days=day_offset)).date()
                if day_counts[intended] >= day_cap:
                    day_offset += 1
                    continue
                # How many of THIS batch already land on the intended day.
                order_index = max(
                    0,
                    day_counts[intended]
                    - (already_queued_today + sent_today if intended == start.date() else 0),
                )
                when = personalized_send_time_utc(
                    location=contact.location if contact else None,
                    order_index=order_index,
                    ab_index=bucket_index if bucket_index is not None else 0,
                    day_offset=day_offset,
                )
                if when <= now:
                    day_offset += 1
                    continue
                landed = when.date()
                if day_counts[landed] >= day_cap:
                    day_offset = max(day_offset + 1, (landed - start.date()).days + 1)
                    continue
                day_counts[landed] += 1
                break
            else:
                when = now + timedelta(days=day_offset, minutes=global_idx * gap_minutes)
                day_counts[when.date()] += 1
        else:
            when = (
                fixed_times[global_idx]
                if global_idx < len(fixed_times)
                else now + timedelta(minutes=global_idx * gap_minutes)
            )
            if when <= now:
                when = now + timedelta(minutes=max(2, gap_minutes) * (global_idx + 1))
            day_counts[when.date()] += 1

        draft.send_bucket_index = bucket_index
        draft.status = EmailStatus.SCHEDULED
        draft.scheduled_at = when
        draft.outlook_scheduled = False
        if not draft.approved_at:
            draft.approved_at = now
        scheduled += 1

    return scheduled


INTERRUPTED_MESSAGE = (
    "Interrupted — the server restarted while this run was working. "
    "Click Continue to finish researching people it already found."
)


def _finish_run(run, *, status: str, message: Optional[str] = None) -> None:
    """Mark a run finished even if its background thread is already dead."""
    from datetime import datetime

    run.status = status
    run.finished_at = datetime.utcnow()
    run.summary = {
        **(run.summary or {}),
        "cancel_requested": True,
        "finished_reason": status,
    }
    if message:
        run.error_message = message


def request_run_cancel(db: Session, campaign_id: int) -> bool:
    """Stop any in-flight run for this campaign.

    Always flips the DB row to ``cancelled`` (not just a soft flag). Azure
    restarts kill the background thread, so a flag-only cancel left zombie
    "Running now" rows that blocked new runs forever.
    """
    from app.models.agent_run import AgentRun

    running = list(
        db.execute(
            select(AgentRun).where(
                AgentRun.campaign_id == campaign_id,
                AgentRun.status == "running",
            )
        ).scalars().all()
    )
    if not running:
        return False
    for run in running:
        _finish_run(run, status="cancelled", message="Stopped by user")
    return True


def reap_orphaned_runs(db: Session) -> int:
    """Fail every run still marked ``running``.

    Agent runs execute on in-process daemon threads. A deploy, Azure recycle,
    or crash kills those threads but leaves the DB row as ``running``. Calling
    this on startup clears the zombies so Continue / Run now work again.
    """
    from app.models.agent_run import AgentRun

    orphans = list(
        db.execute(
            select(AgentRun).where(AgentRun.status == "running")
        ).scalars().all()
    )
    for run in orphans:
        _finish_run(run, status="failed", message=INTERRUPTED_MESSAGE)
        logger.warning(
            "Reaped orphaned agent run #%s (campaign=%s principal=%s)",
            run.id,
            run.campaign_id,
            run.principal_id,
        )
    if orphans:
        db.commit()
    return len(orphans)


def campaign_is_paused(db: Session, campaign_id: Optional[int]) -> bool:
    """True when the campaign is paused (no new runs / no new scheduling).

    Already-queued SCHEDULED emails may still send when the operator chose
    "keep scheduled" on pause — those stay SCHEDULED on purpose.
    """
    if not campaign_id:
        return False
    from app.models.agent_config import AgentConfig

    config = db.get(AgentConfig, campaign_id)
    return bool(config and config.paused)
