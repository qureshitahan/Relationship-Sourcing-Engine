"""Helper for writing audit log entries.

Every automated decision and human action should leave a trace here.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def log_action(
    db: Session,
    action: str,
    *,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    actor: str = "system",
    summary: Optional[str] = None,
    detail: Optional[dict] = None,
    commit: bool = False,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        summary=summary,
        detail=detail,
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry
