"""Prospects (people) discovered at an organization, ranked for outreach.

Modeled as `contacts` for table compatibility. In the relationship-sourcing
domain these are executives, investors, board members, operating partners,
founders and other decision-makers relevant to a principal.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import ProspectStatus


class Contact(Base, TimestampMixin):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.id"), index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    # Decision-maker category (investor, board_member, ceo, founder, ...).
    role_category: Mapped[Optional[str]] = mapped_column(String(40))
    seniority: Mapped[Optional[str]] = mapped_column(String(50))

    email: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    # Apollo People Search reports whether an email EXISTS to unlock (without
    # revealing it). We store this flag so bulk mode only spends reveal credits
    # on people Apollo can actually give us an email for.
    has_email: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Apollo email verification: verified | likely | guessed | unavailable | unknown.
    email_status: Mapped[Optional[str]] = mapped_column(String(30))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    # pending | revealed | unavailable | failed — set when Apollo phone webhook is requested.
    phone_reveal_status: Mapped[Optional[str]] = mapped_column(String(20))
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(512))
    # City/region from discovery or enrichment (helps disambiguate people).
    location: Mapped[Optional[str]] = mapped_column(String(255))

    # Stable id from the enrichment provider (e.g. Apollo person id) for re-lookups.
    external_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    source: Mapped[Optional[str]] = mapped_column(String(50))
    discovery_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("discovery_runs.id"), index=True
    )
    # Which A/B search variant surfaced this person (for conversion tracking).
    variant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agent_variants.id"), index=True
    )

    # Confidence that this is a real, reachable, relevant contact (0-100).
    confidence_score: Mapped[Optional[float]] = mapped_column(Float)
    # How useful this contact type is for the principal (0-100), drives ranking.
    usefulness_score: Mapped[Optional[float]] = mapped_column(Float)
    rank_reason: Mapped[Optional[str]] = mapped_column(Text)
    # Best relevance score from any insight (denormalized for fast sorting).
    relevance_score: Mapped[Optional[float]] = mapped_column(Float)

    status: Mapped[str] = mapped_column(
        String(30), default=ProspectStatus.NEW, index=True, nullable=False
    )

    # Human gate: a prospect must be approved before any outreach.
    approved_for_outreach: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Compliance: per-prospect suppression.
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    company = relationship("Company", back_populates="contacts")
    insights = relationship("RelevanceInsight", back_populates="contact")
