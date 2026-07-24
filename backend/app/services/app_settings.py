"""Read/write helpers for the AppSetting key/value store (short-lived sessions)."""
from __future__ import annotations

from typing import Optional

from app.db.session import SessionLocal
from app.models.app_setting import AppSetting


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    db = SessionLocal()
    try:
        row = db.get(AppSetting, key)
        return row.value if row and row.value else default
    except Exception:  # noqa: BLE001 - never let a settings read crash a request
        return default
    finally:
        db.close()


def set_setting(key: str, value: Optional[str]) -> None:
    db = SessionLocal()
    try:
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value))
        else:
            row.value = value
        db.commit()
    finally:
        db.close()
