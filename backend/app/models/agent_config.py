"""Per-principal configuration for the autonomous outreach agent.

One row per principal. Controls how aggressive the daily run is: how many
people to discover, whether to auto-send (and a daily cap), qualification
thresholds, follow-up cadence, the daily schedule, and digest recipients.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AgentConfig(Base, TimestampMixin):
    __tablename__ = "agent_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NOTE: no longer unique — a principal can own MANY campaigns (one
    # AgentConfig row == one campaign).
    principal_id: Mapped[int] = mapped_column(
        ForeignKey("principals.id"), index=True, nullable=False
    )

    # Human-friendly campaign name (e.g. "Director of Pharmacy outreach").
    name: Mapped[Optional[str]] = mapped_column(String(255))

    # Daily auto-run. When False, the agent only runs when "Run now" is clicked.
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Hard stop: while paused the campaign cannot run and none of its emails
    # send, including anything already scheduled. Stays paused until resumed.
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # UTC hour (0-23) at which the scheduled run fires.
    run_hour_utc: Mapped[int] = mapped_column(Integer, default=13, nullable=False)

    # Which saved playbook to use (replaces legacy search_definition_id).
    playbook_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agent_playbooks.id")
    )
    # Legacy — kept for backward compatibility.
    search_definition_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("search_definitions.id")
    )

    # Deprecated columns (kept for DB compatibility; the engine is per-person now).
    mode: Mapped[str] = mapped_column(String(20), default="research", nullable=False)
    sanity_min: Mapped[float] = mapped_column(Float, default=20.0, nullable=False)
    draft_batch_size: Mapped[int] = mapped_column(Integer, default=8, nullable=False)

    # How many NEW people to discover per run.
    discover_target: Mapped[int] = mapped_column(Integer, default=50, nullable=False)

    # Qualification thresholds (0-100 relevance score) for per-person research.
    qualify_min: Mapped[float] = mapped_column(Float, default=40.0, nullable=False)
    auto_reject_below: Mapped[float] = mapped_column(Float, default=35.0, nullable=False)

    # Sending behaviour. Auto-send drips in small batches (see settings) up to the
    # daily cap; anything over the cap stays ready and goes out on the next run.
    auto_send: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    daily_send_cap: Mapped[int] = mapped_column(Integer, default=50, nullable=False)

    # Follow-ups for people who went silent.
    followup_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    followup_days: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    # Days after first send for each follow-up wave, e.g. [3, 10, 15, 30].
    followup_schedule_days: Mapped[Optional[list]] = mapped_column(
        JSON, default=lambda: [3, 10, 15, 30]
    )
    max_followups: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    followup_cap: Mapped[int] = mapped_column(Integer, default=25, nullable=False)

    # Local timezone for daily run + send-window planning.
    timezone: Mapped[str] = mapped_column(
        String(64), default="America/New_York", nullable=False
    )
    # When True (default), the agent decides send timing autonomously: each email
    # goes out during business hours in the RECIPIENT'S local timezone, with A/B
    # time-of-day testing. When False, the local send window below is used.
    auto_schedule: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Hour (0-23) in ``timezone`` when the daily campaign run starts.
    run_hour_local: Mapped[int] = mapped_column(Integer, default=9, nullable=False)
    # Local send window (hour 0-23) — emails stagger inside this range.
    send_window_start_local: Mapped[int] = mapped_column(Integer, default=9, nullable=False)
    send_window_end_local: Mapped[int] = mapped_column(Integer, default=17, nullable=False)

    # Email addresses that receive the end-of-run summary digest.
    digest_recipients: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
