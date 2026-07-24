"""Bulk email campaigns: paste a list of people, brief the assistant, send.

Unlike an agent campaign (AgentConfig) there is no discovery and no research.
Recipients come from text the user pastes into the campaign chat (usually copied
straight out of a spreadsheet) and the brief comes from the same conversation.

Everything downstream is deliberately shared with the rest of the platform: a
recipient is a ``Contact`` and every email is an ``EmailDraft``, so approval,
sending, open tracking, reply detection and the per-person conversation view all
behave exactly as they do for agent campaigns.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import BulkCampaignStatus


class BulkCampaign(Base, TimestampMixin):
    __tablename__ = "bulk_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Which selectable mailbox sends this campaign (email_providers/mailboxes.py).
    mailbox_id: Mapped[Optional[str]] = mapped_column(String(64))
    # The accumulated brief: what these emails should say, in the user's words.
    purpose: Mapped[Optional[str]] = mapped_column(Text)
    # Sign-off appended to every email. Blank = "Thanks, <mailbox from_name>".
    signature: Mapped[Optional[str]] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(20), default=BulkCampaignStatus.COLLECTING, index=True, nullable=False
    )
    # Progress of the in-flight drafting/sending job, for the live UI.
    progress_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Co-operative stop flag checked between recipients by the background job.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text)

    messages = relationship(
        "BulkChatMessage",
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="BulkChatMessage.id",
    )


class BulkChatMessage(Base, TimestampMixin):
    """One turn of the campaign chat (kept so the brief has an audit trail)."""

    __tablename__ = "bulk_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("bulk_campaigns.id"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # What the turn actually changed (recipients added, drafting started, ...).
    meta: Mapped[Optional[dict]] = mapped_column(JSON)

    campaign = relationship("BulkCampaign", back_populates="messages")
