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

    search_definition = relationship("SearchDefinition", back_populates="discovery_runs")
