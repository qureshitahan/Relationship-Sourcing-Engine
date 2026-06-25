"""Resolve the outreach goal for a prospect (AI search_goal, filters, or principal)."""
from __future__ import annotations

from typing import Optional

from app.models.contact import Contact
from app.models.discovery_run import DiscoveryRun
from app.models.principal import Principal


def outreach_goal_for_contact(
    db, contact: Contact, principal: Principal
) -> Optional[str]:
    """Build a search-aware outreach goal for research and email drafting."""
    if contact.discovery_run_id:
        run = db.get(DiscoveryRun, contact.discovery_run_id)
        if run and run.criteria:
            c = run.criteria
            goal = (c.get("search_goal") or "").strip()
            if goal:
                return goal
            titles = [t for t in (c.get("titles") or []) if t][:6]
            industries = [i for i in (c.get("industries") or []) if i][:3]
            geos = [g for g in (c.get("geographies") or []) if g][:2]
            if titles:
                geo_part = f" in {', '.join(geos)}" if geos else ""
                ind_part = (
                    f" at {', '.join(industries)} companies"
                    if industries
                    else ""
                )
                return (
                    f"Outreach goal: connect with people in roles like "
                    f"{', '.join(titles)}{ind_part}{geo_part}. "
                    "Score relevance and draft emails for THIS objective — "
                    "not board seats unless that is what the goal says."
                )
    focus = (principal.document_focus or "").strip()
    if focus:
        return focus
    objective = (principal.objective or "").strip()
    return objective or None


def outreach_goal_for_run(criteria: Optional[dict]) -> Optional[str]:
    """Human-readable goal from a discovery run criteria snapshot."""
    if not criteria:
        return None
    goal = (criteria.get("search_goal") or "").strip()
    if goal:
        return goal
    titles = [t for t in (criteria.get("titles") or []) if t][:6]
    industries = [i for i in (criteria.get("industries") or []) if i][:3]
    geos = [g for g in (criteria.get("geographies") or []) if g][:2]
    if not titles:
        return None
    geo_part = f" in {', '.join(geos)}" if geos else ""
    ind_part = f" at {', '.join(industries)} companies" if industries else ""
    return (
        f"Connect with people in roles like {', '.join(titles)}"
        f"{ind_part}{geo_part}."
    )
