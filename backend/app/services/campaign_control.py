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

    Used when resuming after a pause: rather than making the user send each
    email by hand, everything that was pulled back is scheduled again using the
    same per-recipient timing the agent uses (good local business hour in the
    recipient's timezone, spaced out, A/B time buckets). Respects the
    principal's shared daily cap. Returns how many were scheduled.
    """
    import random
    from datetime import datetime

    from app.models.agent_config import AgentConfig
    from app.models.contact import Contact
    from app.models.principal import Principal
    from app.services.agent.experiments import EXPLORE_EPSILON, best_send_bucket
    from app.services.agent.send_timing import (
        AB_SEND_BUCKETS,
        personalized_send_time_utc,
        staggered_send_times_utc,
    )
    from app.core.config import settings

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

    # Don't blow through the principal's shared daily cap in one go; the rest
    # stay approved and get picked up on the next resume/run.
    from app.services.agent.orchestrator import _remaining_daily_cap

    remaining = _remaining_daily_cap(db, principal)
    if remaining <= 0:
        return 0

    sendable: List[EmailDraft] = []
    for draft in candidates:
        contact = db.get(Contact, draft.contact_id) if draft.contact_id else None
        if not contact or not contact.email or contact.do_not_contact:
            continue
        sendable.append(draft)
        if len(sendable) >= remaining:
            break
    if not sendable:
        return 0

    auto_schedule = bool(getattr(config, "auto_schedule", True))
    winning_bucket = best_send_bucket(db, principal)
    delay = max(0, int(settings.send_batch_delay_seconds or 120))
    gap_minutes = max(1, delay // 60) if delay else 2

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
    for idx, draft in enumerate(sendable):
        contact = db.get(Contact, draft.contact_id)
        bucket_index: Optional[int] = None
        if auto_schedule:
            if winning_bucket is not None and random.random() < (1 - EXPLORE_EPSILON):
                bucket_index = winning_bucket
            else:
                bucket_index = idx % len(AB_SEND_BUCKETS)
            when = personalized_send_time_utc(
                location=contact.location if contact else None,
                order_index=idx,
                ab_index=bucket_index,
            )
        else:
            when = fixed_times[idx] if idx < len(fixed_times) else now
        # Never schedule in the past — a resumed email should go out on the next
        # good slot, not fire instantly for everyone at once.
        if when <= now:
            continue
        draft.send_bucket_index = bucket_index
        draft.status = EmailStatus.SCHEDULED
        draft.scheduled_at = when
        draft.outlook_scheduled = False
        if not draft.approved_at:
            draft.approved_at = now
        scheduled += 1
    return scheduled


def request_run_cancel(db: Session, campaign_id: int) -> bool:
    """Ask any in-flight run for this campaign to stop. True if one was running."""
    from app.models.agent_run import AgentRun

    running = db.execute(
        select(AgentRun).where(
            AgentRun.campaign_id == campaign_id,
            AgentRun.status == "running",
        )
    ).scalars().first()
    if running is None:
        return False
    running.summary = {**(running.summary or {}), "cancel_requested": True}
    return True


def campaign_is_paused(db: Session, campaign_id: Optional[int]) -> bool:
    """True when the campaign is paused and must not send anything."""
    if not campaign_id:
        return False
    from app.models.agent_config import AgentConfig

    config = db.get(AgentConfig, campaign_id)
    return bool(config and config.paused)
