"""Organization records discovered via Apollo ICP search, plus enrichment.

Modeled as `companies` for table compatibility, but in the relationship-sourcing
domain these are target organizations (operating companies, PE/VC firms, family
offices, etc.) relevant to a principal.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import EnrichmentStatus


class Company(Base, TimestampMixin):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    # Normalized key for dedup (lowercased / stripped name).
    normalized_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)

    # --- Core firmographics (filled by enrichment) ---
    domain: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    website: Mapped[Optional[str]] = mapped_column(String(512))
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(512))
    industry: Mapped[Optional[str]] = mapped_column(String(255))
    employee_count: Mapped[Optional[int]] = mapped_column(Integer)
    headquarters: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    funding: Mapped[Optional[str]] = mapped_column(String(255))
    revenue: Mapped[Optional[str]] = mapped_column(String(255))

    # --- Relationship-sourcing classification ---
    company_type: Mapped[Optional[str]] = mapped_column(String(40))   # see enums.CompanyType
    sectors: Mapped[Optional[list]] = mapped_column(JSON)             # healthcare sectors etc.
    themes: Mapped[Optional[list]] = mapped_column(JSON)              # investment / acquisition themes
    # Strategic signals: recent acquisitions, capital raises, leadership changes,
    # board expansion, platform consolidation, etc.
    signals: Mapped[Optional[list]] = mapped_column(JSON)

    discovery_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("discovery_runs.id"), index=True
    )

    # --- Enrichment tracking ---
    enrichment_status: Mapped[str] = mapped_column(
        String(30), default=EnrichmentStatus.PENDING, nullable=False
    )
    enrichment_source: Mapped[Optional[str]] = mapped_column(String(50))

    # --- Compliance ---
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    contacts = relationship("Contact", back_populates="company")
    insights = relationship("RelevanceInsight", back_populates="company")
