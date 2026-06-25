"""Audit log of automated decisions and human actions.

Every classification, draft, approval, and (future) send/call writes a row here
so the team can review why the system did what it did.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    # The kind of entity acted on (job, company, contact, email_draft, call...).
    entity_type: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    # "system" for automated steps, or a username for human actions.
    actor: Mapped[str] = mapped_column(String(255), default="system", nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    # Structured detail (scores, reasons, before/after, etc.).
    detail: Mapped[Optional[dict]] = mapped_column(JSON)
