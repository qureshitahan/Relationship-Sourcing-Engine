"""Principal profiles: the executive(s) whose network we are building.

A principal (e.g. a healthcare operator and board candidate like Dalbir Bains)
defines who they are, what they are looking for, and what they bring to the
table. Discovery and relevance scoring are always performed relative to a
principal.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Principal(Base, TimestampMixin):
    __tablename__ = "principals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    headline: Mapped[Optional[str]] = mapped_column(String(512))
    # Public LinkedIn profile URL — appended to outreach email signatures.
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(512))
    # Contact phone for signature / scheduling (required for new principals).
    phone: Mapped[Optional[str]] = mapped_column(String(64))
    bio: Mapped[Optional[str]] = mapped_column(Text)
    # Long-form career narrative used to ground AI insight + personalization.
    background: Mapped[Optional[str]] = mapped_column(Text)

    # Deprecated — outreach goals live on Agent/Discover (playbook.objective_prompt).
    # Kept for backward compatibility with existing rows.
    objective: Mapped[Optional[str]] = mapped_column(Text)

    # Optional niche to emphasize when indexing uploaded documents, e.g.
    # "AI Engineering" or "Data Analysis". Blank = extract all relevant signal.
    document_focus: Mapped[Optional[str]] = mapped_column(Text)

    # --- What the principal is interested in (drives discovery + scoring) ---
    focus_areas: Mapped[Optional[list]] = mapped_column(JSON)          # ["Healthcare Services", ...]
    target_sectors: Mapped[Optional[list]] = mapped_column(JSON)       # ["Home Health", "Behavioral Health", ...]
    investment_themes: Mapped[Optional[list]] = mapped_column(JSON)    # ["Platform consolidation", ...]
    acquisition_themes: Mapped[Optional[list]] = mapped_column(JSON)
    target_titles: Mapped[Optional[list]] = mapped_column(JSON)        # ["CEO", "Operating Partner", ...]
    target_seniorities: Mapped[Optional[list]] = mapped_column(JSON)   # ["c_suite", "owner", ...]
    geographies: Mapped[Optional[list]] = mapped_column(JSON)          # ["United States", ...]
    # opportunity types the principal wants (see enums.OpportunityType).
    opportunity_types: Mapped[Optional[list]] = mapped_column(JSON)

    # --- What the principal offers (drives personalization) ---
    value_props: Mapped[Optional[list]] = mapped_column(JSON)          # ["M&A integration", "PE-backed scaling", ...]

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Shared mailbox safety cap: the MOST emails this principal will send in a
    # single day across ALL of their campaigns (protects the one mailbox from
    # looking like spam). Enforced by the agent's send step.
    mailbox_daily_cap: Mapped[int] = mapped_column(Integer, default=50, nullable=False)

    search_definitions = relationship(
        "SearchDefinition", back_populates="principal", cascade="all, delete-orphan"
    )
    insights = relationship("RelevanceInsight", back_populates="principal")
    documents = relationship(
        "PrincipalDocument", back_populates="principal", cascade="all, delete-orphan"
    )
