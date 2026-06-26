"""Personalized email drafts tied to a principal, prospect, org, and insight.

Nothing is sent automatically in the MVP. Drafts must be human-approved.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import EmailStatus


class EmailDraft(Base, TimestampMixin):
    __tablename__ = "email_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    principal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("principals.id"), index=True)
    # The campaign (AgentConfig) this draft belongs to. Lets sends, replies, and
    # per-campaign dashboards scope to one campaign.
    campaign_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agent_configs.id"), index=True)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.id"), index=True)
    contact_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contacts.id"), index=True)
    insight_id: Mapped[Optional[int]] = mapped_column(ForeignKey("relevance_insights.id"), index=True)
    # Which A/B email-copy approach wrote this draft (for reply-rate learning).
    copy_variant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agent_copy_variants.id"), index=True
    )
    # Which A/B send-time bucket this draft was scheduled into (index into
    # AB_SEND_BUCKETS); lets us learn which send window earns more replies.
    send_bucket_index: Mapped[Optional[int]] = mapped_column(Integer)

    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(30), default=EmailStatus.DRAFT, index=True, nullable=False
    )
    # Provider used / message id once sent (future milestone).
    provider: Mapped[Optional[str]] = mapped_column(String(50))
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255))

    approved_by: Mapped[Optional[str]] = mapped_column(String(255))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # When set (and status == scheduled), the background scheduler sends the
    # email at/after this UTC time while the backend process is running.
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    # True when a SCHEDULED draft was handed off to Outlook for server-side
    # deferred delivery (fires even if this app is offline). The in-process
    # scheduler then only syncs status, it never re-sends these.
    outlook_scheduled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # --- Reply tracking (Phase 6b; populated by the Mail.Read poll) ---
    # Microsoft Graph conversation/message ids let us match an inbound reply
    # to this specific outbound email.
    conversation_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    internet_message_id: Mapped[Optional[str]] = mapped_column(String(512))
    replied_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    reply_snippet: Mapped[Optional[str]] = mapped_column(Text)
    # Full plain-text reply body for the in-app conversation thread.
    reply_body: Mapped[Optional[str]] = mapped_column(Text)
    last_reply_check_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Open tracking (via a 1x1 pixel). Approximate: clients may block/prefetch.
    open_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
