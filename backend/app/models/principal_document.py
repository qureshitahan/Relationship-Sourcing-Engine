"""Principal context documents (resume, case studies, bios, proof points).

Each file the user drops in `data/principal_docs/<principal>/` becomes one row.
We store a content hash so ingestion is incremental: a file is only re-read and
re-indexed by the LLM when it is new or has changed. The LLM-extracted
``summary`` / ``key_facts`` / ``themes`` are what ground relevance research and
outreach personalization (e.g. relating Dalbir's expertise to a prospect's).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class PrincipalDocument(Base, TimestampMixin):
    __tablename__ = "principal_documents"
    __table_args__ = (
        UniqueConstraint("principal_id", "filename", name="uq_principal_document"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    principal_id: Mapped[int] = mapped_column(
        ForeignKey("principals.id"), index=True, nullable=False
    )

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # SHA-256 of the file bytes — drives incremental re-indexing.
    file_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    doc_type: Mapped[Optional[str]] = mapped_column(String(50))

    # Extracted plain text (kept so we can re-index without the original file).
    raw_text: Mapped[Optional[str]] = mapped_column(Text)
    char_count: Mapped[Optional[int]] = mapped_column(Integer)

    # --- LLM-derived context (what grounds research + outreach) ---
    summary: Mapped[Optional[str]] = mapped_column(Text)
    key_facts: Mapped[Optional[list]] = mapped_column(JSON)   # verbatim proof points
    themes: Mapped[Optional[list]] = mapped_column(JSON)      # short tags for retrieval
    # Fit for board-seat sourcing (0–100). Drives status + outreach inclusion.
    relevance_score: Mapped[Optional[float]] = mapped_column()
    relevance_note: Mapped[Optional[str]] = mapped_column(Text)

    # indexed (core) | peripheral (partial) | irrelevant | pending | failed
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    indexed_by: Mapped[Optional[str]] = mapped_column(String(100))
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    principal = relationship("Principal", back_populates="documents")
