"""Reusable Ideal-Customer-Profile (ICP) search definitions.

A SearchDefinition captures the criteria a user defines to discover relevant
organizations and people (industries, company types, size, geography, titles,
seniority, keywords, themes, healthcare sectors). It is tied to a principal and
drives Apollo discovery runs.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class SearchDefinition(Base, TimestampMixin):
    __tablename__ = "search_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    principal_id: Mapped[int] = mapped_column(
        ForeignKey("principals.id"), index=True, nullable=False
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # --- ICP criteria (all optional; JSON lists / scalars) ---
    industries: Mapped[Optional[list]] = mapped_column(JSON)
    company_types: Mapped[Optional[list]] = mapped_column(JSON)      # see enums.CompanyType
    healthcare_sectors: Mapped[Optional[list]] = mapped_column(JSON)
    geographies: Mapped[Optional[list]] = mapped_column(JSON)
    titles: Mapped[Optional[list]] = mapped_column(JSON)
    seniorities: Mapped[Optional[list]] = mapped_column(JSON)
    keywords: Mapped[Optional[list]] = mapped_column(JSON)
    themes: Mapped[Optional[list]] = mapped_column(JSON)             # investment / acquisition themes

    employee_min: Mapped[Optional[int]] = mapped_column(Integer)
    employee_max: Mapped[Optional[int]] = mapped_column(Integer)

    principal = relationship("Principal", back_populates="search_definitions")
    discovery_runs = relationship("DiscoveryRun", back_populates="search_definition")
