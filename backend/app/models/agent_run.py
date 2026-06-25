"""A single end-to-end autonomous agent run (discover → qualify → draft → send).

Each row is one execution of the daily outreach pipeline. Counters and the
``summary`` JSON capture what the agent did so the UI can show a clear digest of
"what the agent achieved" without the user having to drive any of the steps.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    principal_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("principals.id"), index=True
    )
    # The campaign (AgentConfig) this run belongs to.
    campaign_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agent_configs.id"), index=True
    )
    discovery_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("discovery_runs.id"), index=True
    )
    playbook_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agent_playbooks.id"), index=True
    )
    # Which A/B search variant this run used.
    variant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agent_variants.id"), index=True
    )

    # running | completed | failed
    status: Mapped[str] = mapped_column(
        String(20), default="running", index=True, nullable=False
    )
    # manual | scheduled
    trigger: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # Funnel counters for the run.
    discovered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicates: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    qualified: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    drafted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    followups_drafted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    followups_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Human-readable per-stage details: {"stage": "...", "people": [...], ...}
    summary: Mapped[Optional[dict]] = mapped_column(JSON)
