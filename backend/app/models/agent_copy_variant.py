"""A/B email-copy variants for the autonomous agent.

The agent doesn't write one fixed email forever. For a playbook it maintains
several **copy variants** — each a different writing approach (hook, structure,
call-to-action, tone, length). Every draft is assigned a copy variant (mostly
the best performer, sometimes an untested one), the draft is tagged with it, and
we learn which copy converts best from real reply data.

This is the second optimization lever, parallel to AgentVariant (which A/B tests
the search ICP) and the send-time buckets (which A/B test when to send).
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AgentCopyVariant(Base, TimestampMixin):
    __tablename__ = "agent_copy_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    principal_id: Mapped[int] = mapped_column(
        ForeignKey("principals.id"), index=True, nullable=False
    )
    playbook_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agent_playbooks.id"), index=True
    )

    # Short human label, e.g. "Direct, peer-to-peer" or "Curiosity hook".
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    # Style spec that steers the writer: {hook, structure, cta, tone, length}.
    style: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rationale: Mapped[Optional[str]] = mapped_column(Text)

    # Disabled variants are never selected (manually paused or pruned by learning).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Cumulative counters. Open/reply rates are computed on read from the emails
    # tagged with this copy variant.
    drafted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
