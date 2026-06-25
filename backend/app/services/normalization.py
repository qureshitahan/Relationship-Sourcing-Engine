"""Field-level normalization helpers.

Pure functions (no DB access) so they are easy to unit-test and reuse.
"""
from __future__ import annotations

import re
from typing import Any, Optional


def clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_company_name(name: Any) -> str:
    """Lowercase, strip punctuation and common suffixes for dedup/matching."""
    text = (clean_text(name) or "").lower()
    text = re.sub(r"[\.,]", "", text)
    # Drop trailing legal suffixes that cause false mismatches.
    text = re.sub(r"\b(inc|llc|ltd|corp|co|gmbh|plc|limited|incorporated)\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_int(value: Any) -> Optional[int]:
    text = clean_text(value)
    if not text:
        return None
    digits = re.sub(r"[^0-9]", "", text)
    return int(digits) if digits else None
