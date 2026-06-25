"""Saved agent search playbooks — prompt, clarifying answers, and ICP criteria.

Lets a user describe their goal in plain language, answer a few clarifying
questions, pick titles/seniorities, save the result as a named playbook, and
re-run it without re-prompting each time.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AgentPlaybook(Base, TimestampMixin):
    __tablename__ = "agent_playbooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    principal_id: Mapped[int] = mapped_column(
        ForeignKey("principals.id"), index=True, nullable=False
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # What the user typed (2–3 lines describing their outreach goal).
    objective_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # Answers to clarifying questions, e.g. {"geography": "United States", ...}
    clarifying_answers: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    # Full ICP criteria used for Apollo discovery (titles, seniorities, industries…).
    criteria: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
