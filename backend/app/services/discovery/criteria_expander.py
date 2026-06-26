"""Auto-expand Apollo discovery criteria when a run returns fewer prospects than requested.

Geographies, titles, employer domains, and job-posting signals are locked — they
reflect user intent. Expandable fields: related industries, relaxing keyword/theme
filters that over-narrow combined searches, seniority, email status, employee min.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from app.core.config import settings
from app.services.enrichment.base import DiscoveryCriteria

logger = logging.getLogger(__name__)

# Related industry tags — Apollo keyword search, not a fixed enum.
_INDUSTRY_RELATED: dict[str, list[str]] = {
    "healthcare": ["Healthcare Services", "Hospital & Health Care", "Health Care"],
    "healthcare services": ["Healthcare", "Hospital & Health Care", "Medical Practice"],
    "health care": ["Healthcare", "Healthcare Services", "Hospital & Health Care"],
    "hospital & health care": ["Healthcare", "Healthcare Services", "Health Care"],
    "hospitals and health care": ["Healthcare", "Hospital & Health Care"],
    "pharmaceuticals": ["Biotechnology", "Healthcare", "Medical Devices"],
    "biotechnology": ["Pharmaceuticals", "Healthcare", "Research"],
    "medical devices": ["Healthcare", "Pharmaceuticals", "Medical Equipment Manufacturing"],
    "financial services": ["Investment Management", "Venture Capital & Private Equity"],
    "investment management": ["Financial Services", "Private Equity"],
}

_BROAD_HEALTH = ["Healthcare", "Hospital & Health Care", "Healthcare Services"]


def copy_criteria(criteria: DiscoveryCriteria) -> DiscoveryCriteria:
    return DiscoveryCriteria(
        industries=list(criteria.industries) if criteria.industries else None,
        company_types=list(criteria.company_types) if criteria.company_types else None,
        healthcare_sectors=list(criteria.healthcare_sectors)
        if criteria.healthcare_sectors
        else None,
        geographies=list(criteria.geographies) if criteria.geographies else None,
        titles=list(criteria.titles) if criteria.titles else None,
        seniorities=list(criteria.seniorities) if criteria.seniorities else None,
        contact_email_status=list(criteria.contact_email_status)
        if criteria.contact_email_status
        else None,
        organization_domains=list(criteria.organization_domains)
        if criteria.organization_domains
        else None,
        keywords=list(criteria.keywords) if criteria.keywords else None,
        themes=list(criteria.themes) if criteria.themes else None,
        employee_min=criteria.employee_min,
        employee_max=criteria.employee_max,
        org_limit=criteria.org_limit,
        people_limit=criteria.people_limit,
        organization_job_titles=list(criteria.organization_job_titles)
        if criteria.organization_job_titles
        else None,
    )


@dataclass
class ExpansionState:
    step: int = 0
    applied: set[str] = field(default_factory=set)


def _norm_list(items: Optional[List[str]]) -> List[str]:
    return [t.strip() for t in (items or []) if (t or "").strip()]


def _add_industries(criteria: DiscoveryCriteria, additions: List[str]) -> Optional[str]:
    current = _norm_list(criteria.industries)
    seen = {x.lower() for x in current}
    added: List[str] = []
    for tag in additions:
        if tag.lower() not in seen:
            seen.add(tag.lower())
            added.append(tag)
    if not added:
        return None
    criteria.industries = current + added
    return "Added industries: " + ", ".join(added)


def _step_related_industries(
    criteria: DiscoveryCriteria, state: ExpansionState
) -> Optional[str]:
    if "related_industries" in state.applied:
        return None
    state.applied.add("related_industries")
    additions: List[str] = []
    for ind in _norm_list(criteria.industries):
        for rel in _INDUSTRY_RELATED.get(ind.lower(), []):
            if rel.lower() not in {a.lower() for a in additions}:
                additions.append(rel)
    return _add_industries(criteria, additions)


def _step_broad_healthcare(
    criteria: DiscoveryCriteria, state: ExpansionState
) -> Optional[str]:
    if "broad_health" in state.applied:
        return None
    state.applied.add("broad_health")
    industries = _norm_list(criteria.industries)
    if not any("health" in i.lower() or "hospital" in i.lower() for i in industries):
        return None
    return _add_industries(criteria, _BROAD_HEALTH)


def _step_clear_themes(criteria: DiscoveryCriteria, state: ExpansionState) -> Optional[str]:
    if "clear_themes" in state.applied:
        return None
    themes = _norm_list(criteria.themes)
    if not themes:
        return None
    state.applied.add("clear_themes")
    criteria.themes = None
    return "Removed theme filters from search (themes were narrowing results when combined with industry/company type)."


def _step_clear_keywords(criteria: DiscoveryCriteria, state: ExpansionState) -> Optional[str]:
    if "clear_keywords" in state.applied:
        return None
    keywords = _norm_list(criteria.keywords)
    if not keywords:
        return None
    state.applied.add("clear_keywords")
    criteria.keywords = None
    return "Removed keyword filters to widen the search."


def _step_clear_company_types(
    criteria: DiscoveryCriteria, state: ExpansionState
) -> Optional[str]:
    if "clear_company_types" in state.applied:
        return None
    types = _norm_list(criteria.company_types)
    if not types:
        return None
    state.applied.add("clear_company_types")
    criteria.company_types = None
    return "Removed company-type filters (industry + title filters kept)."


def _step_clear_seniorities(
    criteria: DiscoveryCriteria, state: ExpansionState
) -> Optional[str]:
    if "clear_seniorities" in state.applied:
        return None
    if not criteria.seniorities:
        return None
    state.applied.add("clear_seniorities")
    criteria.seniorities = None
    return "Removed seniority filter for a broader title match."


def _step_clear_email_status(
    criteria: DiscoveryCriteria, state: ExpansionState
) -> Optional[str]:
    if "clear_email_status" in state.applied:
        return None
    if not criteria.contact_email_status:
        return None
    state.applied.add("clear_email_status")
    criteria.contact_email_status = None
    return "Removed email-status filter to include more people."


def _step_clear_employee_min(
    criteria: DiscoveryCriteria, state: ExpansionState
) -> Optional[str]:
    if "clear_employee_min" in state.applied:
        return None
    if criteria.employee_min is None:
        return None
    state.applied.add("clear_employee_min")
    criteria.employee_min = None
    return "Removed minimum employee-count filter."


def _parse_json(text: str) -> Optional[dict]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                return None
    return None


def _step_llm_expand(criteria: DiscoveryCriteria, state: ExpansionState) -> Optional[str]:
    if "llm_expand" in state.applied or not settings.anthropic_api_key:
        return None
    state.applied.add("llm_expand")
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        locked = {
            "geographies": criteria.geographies,
            "titles": criteria.titles,
            "organization_domains": criteria.organization_domains,
        }
        prompt = (
            "A B2B people search returned too few results. Suggest SMALL expansions.\n\n"
            f"LOCKED (do not change): {json.dumps(locked)}\n"
            f"Current industries: {criteria.industries}\n"
            f"Current company_types: {criteria.company_types}\n\n"
            "Reply JSON only: "
            '{"add_industries": ["..."], "note": "one sentence why"}'
            "\nAdd 1-3 BROAD industry keyword tags not already listed. "
            "Do not change geography or titles."
        )
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        )
        data = _parse_json(text) or {}
        additions = [str(x) for x in (data.get("add_industries") or []) if x]
        note = _add_industries(criteria, additions)
        if note:
            llm_note = (data.get("note") or "").strip()
            return f"{note}" + (f" ({llm_note})" if llm_note else "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM criteria expansion failed: %s", exc)
    return None


def _step_clear_healthcare_sectors(
    criteria: DiscoveryCriteria, state: ExpansionState
) -> Optional[str]:
    if "clear_healthcare_sectors" in state.applied:
        return None
    sectors = _norm_list(criteria.healthcare_sectors)
    if not sectors:
        return None
    state.applied.add("clear_healthcare_sectors")
    criteria.healthcare_sectors = None
    return "Removed healthcare-sector filters to widen the search."


def _step_trim_titles(criteria: DiscoveryCriteria, state: ExpansionState) -> Optional[str]:
    if "trim_titles" in state.applied:
        return None
    titles = _norm_list(criteria.titles)
    if len(titles) <= 8:
        return None
    state.applied.add("trim_titles")
    priority: List[str] = []
    rest: List[str] = []
    for title in titles:
        lower = title.lower()
        if any(
            w in lower
            for w in ("director", "chief", "vp", "head", "president", "manager", "officer")
        ):
            priority.append(title)
        else:
            rest.append(title)
    criteria.titles = (priority + rest)[:8]
    return f"Trimmed title list to {len(criteria.titles)} broad decision-maker roles."


def _step_clear_industries_to_broad(
    criteria: DiscoveryCriteria, state: ExpansionState
) -> Optional[str]:
    if "single_industry" in state.applied:
        return None
    industries = _norm_list(criteria.industries)
    if len(industries) <= 1:
        return None
    state.applied.add("single_industry")
    # Domain-agnostic: keep the first (primary) industry tag. Do NOT prefer
    # healthcare — that biased non-healthcare searches toward health companies.
    preferred = industries[0]
    criteria.industries = [preferred]
    return f"Narrowed to one industry tag: {preferred}."


_STEPS: List[Callable[[DiscoveryCriteria, ExpansionState], Optional[str]]] = [
    _step_related_industries,
    _step_broad_healthcare,
    _step_clear_themes,
    _step_clear_keywords,
    _step_clear_company_types,
    _step_clear_healthcare_sectors,
    _step_trim_titles,
    _step_clear_seniorities,
    _step_clear_email_status,
    _step_clear_employee_min,
    _step_clear_industries_to_broad,
    _step_llm_expand,
]


def next_expansion(
    criteria: DiscoveryCriteria, state: ExpansionState
) -> Tuple[DiscoveryCriteria, Optional[str]]:
    """Apply the next expansion step. Returns (updated criteria, one-line note or None)."""
    working = copy_criteria(criteria)
    while state.step < len(_STEPS):
        fn = _STEPS[state.step]
        state.step += 1
        note = fn(working, state)
        if note:
            return working, note
    return working, None


def summarize_adjustments(adjustments: List[str]) -> Optional[str]:
    if not adjustments:
        return None
    if len(adjustments) == 1:
        return adjustments[0]
    return "Auto-expanded search: " + "; ".join(adjustments)
