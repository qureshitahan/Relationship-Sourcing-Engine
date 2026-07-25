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
