"""Apollo search filter helpers shared by enrichment and the agent planner."""
from __future__ import annotations

from typing import List, Optional

# Apollo person_seniorities[] — complete enum (docs.apollo.io/reference/people-api-search).
APOLLO_SENIORITIES = (
    "owner",
    "founder",
    "c_suite",
    "partner",
    "vp",
    "head",
    "director",
    "manager",
    "senior",
    "entry",
    "intern",
)

# Apollo contact_email_status[] — we exclude unavailable (no reachable email).
APOLLO_EMAIL_STATUSES = (
    "verified",
    "unverified",
    "likely to engage",
)

_US_ALIASES = frozenset(
    {
        "united states",
        "united states of america",
        "us",
        "usa",
        "u.s.",
        "u.s.a.",
        "america",
    }
)


def expand_geographies(geographies: Optional[List[str]]) -> List[str]:
    """Expand US-focused geography tokens for Apollo location filters.

    Apollo matches free-text locations; profiles may say US, USA, United States,
    or a US city/state. When the caller selects any US alias we include the common
    variants so we don't miss people tagged differently.
    """
    if not geographies:
        return ["United States", "US", "USA"]

    out: List[str] = []
    seen: set[str] = set()
    us_selected = False

    for raw in geographies:
        g = (raw or "").strip()
        if not g:
            continue
        key = g.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(g)
        if key in _US_ALIASES:
            us_selected = True

    if us_selected:
        for alias in ("United States", "US", "USA"):
            k = alias.lower()
            if k not in seen:
                seen.add(k)
                out.append(alias)

    return out or ["United States", "US", "USA"]


def filter_email_statuses(statuses: Optional[List[str]]) -> Optional[List[str]]:
    """Drop unavailable — no point searching for people Apollo can't email."""
    if not statuses:
        return None
    allowed = {s.lower() for s in APOLLO_EMAIL_STATUSES}
    cleaned = [s for s in statuses if (s or "").strip().lower() in allowed]
    return cleaned or None
