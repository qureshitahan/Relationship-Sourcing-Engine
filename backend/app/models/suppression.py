"""Do-not-contact / suppression entries and outreach history.

These power compliance: opt-outs, cooldowns, rate limits, and spam prevention.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Suppression(Base, TimestampMixin):
    """A do-not-contact entry. Scope can be company / domain / email / contact."""

    __tablename__ = "suppressions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    # The value to suppress (e.g. domain string, email, or company id as text).
    value: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))


class OutreachHistory(Base, TimestampMixin):
    """Append-only log of outreach touches, used for cooldown / rate limits."""

    __tablename__ = "outreach_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.id"), index=True)
    contact_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contacts.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)  # email | call
    detail: Mapped[Optional[str]] = mapped_column(Text)
