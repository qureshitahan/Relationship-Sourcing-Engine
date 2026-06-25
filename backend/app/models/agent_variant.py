"""A/B search variants for the autonomous agent.

The agent doesn't run one fixed search forever. For a playbook it maintains
several **variants** — each a tweak of one axis (titles, seniorities, or
industries) of the base ICP. Every run picks a variant (mostly the best
performer, sometimes an untested one), tags the people it discovers with that
variant, and we learn which search converts best from real reply data.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AgentVariant(Base, TimestampMixin):
    __tablename__ = "agent_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    principal_id: Mapped[int] = mapped_column(
        ForeignKey("principals.id"), index=True, nullable=False
    )
    playbook_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agent_playbooks.id"), index=True
    )

    # Short human label, e.g. "Operating partners + PE" or "CMOs + pharma".
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    # Which axis this variant explores: titles | seniorities | industries | base.
    axis: Mapped[Optional[str]] = mapped_column(String(30))
    # Full ICP criteria for this variant (titles, seniorities, industries, ...).
    criteria: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rationale: Mapped[Optional[str]] = mapped_column(Text)

    # Disabled variants are never selected (manually paused or pruned).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Cumulative counters across all runs that used this variant. Open/reply
    # rates are computed on read from the emails of this variant's contacts.
    runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discovered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    drafted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
