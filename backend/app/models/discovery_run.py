"""Tracks Apollo-driven ICP discovery runs (organizations + people)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import DiscoveryStatus


class DiscoveryRun(Base, TimestampMixin):
    __tablename__ = "discovery_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    principal_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("principals.id"), index=True
    )
    search_definition_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("search_definitions.id"), index=True
    )

    provider: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    # Snapshot of the ICP criteria actually used for this run.
    criteria: Mapped[Optional[dict]] = mapped_column(JSON)

    status: Mapped[str] = mapped_column(
        String(30), index=True, default=DiscoveryStatus.PENDING, nullable=False
    )

    organizations_found: Mapped[Optional[int]] = mapped_column(Integer)
    organizations_imported: Mapped[Optional[int]] = mapped_column(Integer)
    people_found: Mapped[Optional[int]] = mapped_column(Integer)
    people_imported: Mapped[Optional[int]] = mapped_column(Integer)
    duplicates: Mapped[Optional[int]] = mapped_column(Integer)
    insights_generated: Mapped[Optional[int]] = mapped_column(Integer)

    error_message: Mapped[Optional[str]] = mapped_column(Text)
    requested_by: Mapped[Optional[str]] = mapped_column(String(255))

    # --- Background bulk job progress (additive) ---
    # A discovery run can also drive follow-on bulk jobs: drafting emails, sending
    # emails, or sending LinkedIn messages to its prospects. These track the most
    # recent such job so the UI can show a live progress bar and cancel it. Null =
    # no bulk job has run, so existing runs/behaviour are unaffected.
    # job_kind: draft_email | send_email | send_linkedin | discovery
    job_kind: Mapped[Optional[str]] = mapped_column(String(30))
    # job_status: running | done | failed
    job_status: Mapped[Optional[str]] = mapped_column(String(20))
    job_total: Mapped[Optional[int]] = mapped_column(Integer)
    job_done: Mapped[Optional[int]] = mapped_column(Integer)
    # For the pipeline job specifically: how many drafts have actually been sent
    # so far, distinct from job_done (which counts approve+draft progress) — the
    # send stage runs on its own dedicated thread and can lag behind produce.
    job_sent: Mapped[Optional[int]] = mapped_column(Integer)
    job_error: Mapped[Optional[str]] = mapped_column(Text)
    job_cancel_requested: Mapped[Optional[bool]] = mapped_column(default=False)

    search_definition = relationship("SearchDefinition", back_populates="discovery_runs")
