"""LinkedIn outreach messages tied to a principal, prospect, org, and insight.

The LinkedIn analog of EmailDraft. Nothing is sent automatically without human
approval. Because LinkedIn only allows direct messages to 1st-degree
connections, a message to a non-connection first sends a connection invitation
(``invite_sent``) and the stored ``body`` is auto-delivered once accepted.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import LinkedInStatus


class LinkedInMessage(Base, TimestampMixin):
    __tablename__ = "linkedin_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    principal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("principals.id"), index=True)
    campaign_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agent_configs.id"), index=True)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.id"), index=True)
    contact_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contacts.id"), index=True)
    insight_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("relevance_insights.id"), index=True
    )

    # The direct message (sent when connected / after an invite is accepted).
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Short note (<=300 chars) attached to a connection invitation.
    invitation_note: Mapped[Optional[str]] = mapped_column(String(400))

    status: Mapped[str] = mapped_column(
        String(30), default=LinkedInStatus.DRAFT, index=True, nullable=False
    )

    # Resolved LinkedIn identity for the recipient.
    provider: Mapped[Optional[str]] = mapped_column(String(50))
    linkedin_provider_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    public_identifier: Mapped[Optional[str]] = mapped_column(String(255))
    network_distance: Mapped[Optional[str]] = mapped_column(String(30))
    # True once we know they are a 1st-degree connection (directly messageable).
    connected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Which of OUR connected LinkedIn accounts sent this (Unipile account_id),
    # stamped at send time. Reply/invite-acceptance tracking must poll with the
    # SAME account that sent, so switching the active account never blinds an
    # earlier account's threads. Null on legacy rows = use the active account
    # (exactly today's single-account behaviour).
    from_account: Mapped[Optional[str]] = mapped_column(String(64))

    # --- Followers module ---
    # Set only for DMs to a follower of a connected account (see
    # models/linkedin_follower.py). NULL on every prospect-driven message, which
    # is how the existing LinkedIn module keeps its lists and counts unchanged:
    # its queries filter follower_id IS NULL.
    follower_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("linkedin_followers.id"), index=True
    )
    # Which outreach goal this follower DM belongs to, so the follower tabs can
    # scope to one campaign without touching prospect messages.
    follower_campaign_key: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    # Unipile ids captured on send, used to match replies.
    provider_chat_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255))
    provider_invitation_id: Mapped[Optional[str]] = mapped_column(String(255))

    approved_by: Mapped[Optional[str]] = mapped_column(String(255))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    invitation_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_status_check_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    error: Mapped[Optional[str]] = mapped_column(Text)

    # --- Reply tracking ---
    replied_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    reply_snippet: Mapped[Optional[str]] = mapped_column(Text)
    reply_body: Mapped[Optional[str]] = mapped_column(Text)
    last_reply_check_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
