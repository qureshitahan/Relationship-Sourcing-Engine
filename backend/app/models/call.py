"""Voice call objects: queue, script, status, transcript, and outcomes.

Used for a future Twilio + ElevenLabs integration. In the MVP we only generate
scripts and manage the queue; no calls are placed without human approval.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import CallStatus


class Call(Base, TimestampMixin):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    principal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("principals.id"), index=True)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.id"), index=True)
    contact_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contacts.id"), index=True)
    insight_id: Mapped[Optional[int]] = mapped_column(ForeignKey("relevance_insights.id"), index=True)

    phone_number: Mapped[Optional[str]] = mapped_column(String(50))
    script: Mapped[Optional[str]] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(30), default=CallStatus.QUEUED, index=True, nullable=False
    )

    transcript: Mapped[Optional[str]] = mapped_column(Text)
    outcome_notes: Mapped[Optional[str]] = mapped_column(Text)
    human_handoff_needed: Mapped[bool] = mapped_column(default=False, nullable=False)
    meeting_requested: Mapped[bool] = mapped_column(default=False, nullable=False)

    approved_by: Mapped[Optional[str]] = mapped_column(String(255))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    placed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    provider: Mapped[Optional[str]] = mapped_column(String(50))
    provider_call_id: Mapped[Optional[str]] = mapped_column(String(255))
