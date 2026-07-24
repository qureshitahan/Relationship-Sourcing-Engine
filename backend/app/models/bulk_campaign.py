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

from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import BulkCampaignStatus, BulkLookupStatus


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


class BulkLookup(Base, TimestampMixin):
    """The search for one pasted person's email address, and its evidence.

    Pasting a conference roster gives names and job descriptions but rarely
    addresses. Each of those people gets a placeholder ``Contact`` plus one of
    these rows, which records who the web search decided they are, what Apollo
    returned, and the sources behind it — so the user approves a proposal they
    can actually check rather than an address that simply appeared.
    """

    __tablename__ = "bulk_lookups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("bulk_campaigns.id"), index=True, nullable=False
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id"), index=True, nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20), default=BulkLookupStatus.PENDING, index=True, nullable=False
    )
    # Exactly what the user pasted about this person, kept verbatim so the
    # review screen can show the original next to the match.
    source_text: Mapped[Optional[str]] = mapped_column(Text)

    # What the web search concluded about who this person is.
    resolved_name: Mapped[Optional[str]] = mapped_column(String(255))
    resolved_title: Mapped[Optional[str]] = mapped_column(String(255))
    resolved_org: Mapped[Optional[str]] = mapped_column(String(255))
    resolved_domain: Mapped[Optional[str]] = mapped_column(String(255))
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(512))
    location: Mapped[Optional[str]] = mapped_column(String(255))
    # 0-1 self-reported certainty that this is the right person.
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    # Why the lookup landed where it did (especially when ambiguous/not found).
    reason: Mapped[Optional[str]] = mapped_column(Text)
    # [{"title": ..., "url": ...}] the search actually used.
    evidence: Mapped[Optional[list]] = mapped_column(JSON)

    # The proposed address. Never copied onto the contact without approval.
    email: Mapped[Optional[str]] = mapped_column(String(255))
    # Apollo's verdict: verified | likely | guessed | unavailable | unknown.
    email_status: Mapped[Optional[str]] = mapped_column(String(30))
    # Set when the address was typed in by hand instead of found.
    manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text)


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
