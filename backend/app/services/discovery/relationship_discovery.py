"""Apollo-driven ICP discovery orchestrator.

Discovers people via Apollo, imports them as prospects, dedupes across all prior
runs, and optionally auto-expands search criteria when fewer than the requested
count are found. Research / relevance scoring happens later on the Prospects page.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.company import Company
from app.models.contact import Contact
from app.models.discovery_run import DiscoveryRun
from app.models.enums import (
    AuditAction,
    CompanyType,
    DiscoveryStatus,
    ProspectStatus,
)
from app.models.principal import Principal
from app.services.audit import log_action
from app.services.companies import get_or_create_company
from app.services.contacts import classify_role_category, discovery_fit_score, infer_seniority
from app.services.discovery.criteria_expander import (
    ExpansionState,
    copy_criteria,
    next_expansion,
    summarize_adjustments,
)
from app.services.enrichment import get_discovery_provider
from app.services.enrichment.base import DiscoveryCriteria

logger = logging.getLogger(__name__)

MAX_EXPANSION_ROUNDS = 12


def _is_junk_person(person, company: Optional[Company]) -> bool:
    name = (getattr(person, "name", "") or "").strip().lower()
    if not name or name == "(unknown)":
        return True
    org_name = (getattr(person, "organization_name", "") or "").strip().lower()
    if name and (name == org_name or (company and name == (company.name or "").strip().lower())):
        return True
    if not any(ch.isalpha() for ch in name):
        return True
    return False


def _infer_company_type(industry: Optional[str], criteria: DiscoveryCriteria) -> Optional[str]:
    if criteria.company_types and len(criteria.company_types) == 1:
        return criteria.company_types[0]
    text = (industry or "").lower()
    if "private equity" in text:
        return CompanyType.PRIVATE_EQUITY
    if "venture" in text:
        return CompanyType.VENTURE_CAPITAL
    if "family office" in text:
        return CompanyType.FAMILY_OFFICE
    if "bank" in text:
        return CompanyType.INVESTMENT_BANK
    if industry:
        return CompanyType.OPERATING_COMPANY
    return None


def _apply_org_fields(company: Company, org, criteria: DiscoveryCriteria, run_id: int) -> None:
    company.domain = company.domain or org.domain
    company.website = company.website or org.website
    company.linkedin_url = company.linkedin_url or org.linkedin_url
    company.industry = company.industry or org.industry
    company.employee_count = company.employee_count or org.employee_count
    company.headquarters = company.headquarters or org.headquarters
    company.phone = company.phone or org.phone
    company.funding = company.funding or org.funding
    company.revenue = company.revenue or org.revenue
    if not company.company_type:
        company.company_type = _infer_company_type(org.industry, criteria)
    if not company.sectors and criteria.healthcare_sectors:
        company.sectors = list(criteria.healthcare_sectors)
    if not company.themes and criteria.themes:
        company.themes = list(criteria.themes)
    if company.discovery_run_id is None:
        company.discovery_run_id = run_id


def run_discovery(
    db: Session,
    principal: Principal,
    criteria: DiscoveryCriteria,
    *,
    search_definition_id: Optional[int] = None,
    requested_by: str = "user",
    generate_insights: bool = False,
    include_organizations: bool = False,
    people_first: bool = False,
    auto_expand_to_target: bool = True,
    search_goal: Optional[str] = None,
    campaign_id: Optional[int] = None,
    run: Optional[DiscoveryRun] = None,
) -> DiscoveryRun:
    provider = get_discovery_provider()
    original_criteria = copy_criteria(criteria)
    working_criteria = copy_criteria(criteria)
    expansion_adjustments: list[str] = []

    if run is None:
        # Synchronous path (e.g. the autonomous agent): create the run row here.
        run = DiscoveryRun(
            principal_id=principal.id,
            search_definition_id=search_definition_id,
            provider=getattr(provider, "name", "stub"),
            criteria=_criteria_to_dict(original_criteria),
            status=DiscoveryStatus.RUNNING,
            requested_by=requested_by,
        )
        db.add(run)
        db.flush()
    else:
        # Async path: the route pre-created a PENDING run so the UI has an id to
        # poll; adopt it and mark it running instead of creating a second row.
        run.provider = getattr(provider, "name", "stub")
        run.criteria = _criteria_to_dict(original_criteria)
        run.status = DiscoveryStatus.RUNNING
        db.flush()

    orgs_found = orgs_imported = people_found = people_imported = duplicates = 0
    insights_generated = 0
    target = max(1, min(criteria.people_limit or 25, settings.discovery_max_people_limit))
    seen_apollo_ids: set[str] = set()
    expansion_state = ExpansionState()

    try:
        discovered_org_ids: list[str] = []
        if people_first:
            orgs_found = 0
        else:
            discovered_orgs = provider.discover_organizations(working_criteria)
            orgs_found = len(discovered_orgs)
            discovered_org_ids = [
                org.external_id for org in discovered_orgs if getattr(org, "external_id", None)
            ]
            if include_organizations:
                for org in discovered_orgs:
                    existing = _find_company(db, org.name)
                    company = get_or_create_company(db, org.name, linkedin_url=org.linkedin_url)
                    if company is None:
                        continue
                    if existing is None:
                        orgs_imported += 1
                    else:
                        duplicates += 1
                    _apply_org_fields(company, org, working_criteria, run.id)
                db.flush()

        use_direct = people_first or not discovered_org_ids
        round_num = 0
        rounds_without_progress = 0

        while people_imported < target and round_num <= MAX_EXPANSION_ROUNDS:
            need = target - people_imported
            fetch_limit = min(max(need * 2, 30), settings.discovery_max_people_limit)
            working_criteria.people_limit = fetch_limit

            discovered_people = provider.discover_people(
                working_criteria,
                organization_ids=discovered_org_ids or None,
                direct=use_direct,
                exclude_external_ids=seen_apollo_ids,
            )
            people_found += len(discovered_people)

            if not discovered_people:
                if not auto_expand_to_target or people_imported >= target:
                    break
                working_criteria, note = next_expansion(working_criteria, expansion_state)
                if note:
                    expansion_adjustments.append(note)
                    round_num += 1
                    rounds_without_progress = 0
                    continue
                break

            imported_this_round = 0
            for person in discovered_people:
                if people_imported >= target:
                    break
                if person.external_id:
                    seen_apollo_ids.add(person.external_id)

                company = get_or_create_company(
                    db, person.organization_name, linkedin_url=person.organization_linkedin_url
                )
                if _is_junk_person(person, company):
                    continue

                if company is not None:
                    company.domain = company.domain or person.organization_domain
                    if not company.website and person.organization_domain:
                        company.website = f"https://{person.organization_domain}"
                    company.industry = company.industry or person.industry
                    company.employee_count = company.employee_count or person.employee_count
                    if not company.company_type:
                        company.company_type = _infer_company_type(person.industry, working_criteria)
                    if company.discovery_run_id is None:
                        company.discovery_run_id = run.id
                    db.flush()

                existing = _find_contact(
                    db,
                    person.external_id,
                    person.name,
                    company,
                    person.linkedin_url,
                    campaign_id=campaign_id,
                )
                if existing is not None:
                    duplicates += 1
                    continue

                role_category = classify_role_category(person.title)
                usefulness, rank_reason = discovery_fit_score(
                    person.title,
                    working_criteria,
                    industry=person.industry,
                )

                contact = Contact(
                    company_id=company.id if company else None,
                    name=person.name,
                    title=person.title,
                    role_category=role_category,
                    seniority=person.seniority or infer_seniority(person.title),
                    location=person.location,
                    linkedin_url=person.linkedin_url,
                    external_id=person.external_id,
                    source=run.provider,
                    discovery_run_id=run.id,
                    campaign_id=campaign_id,
                    has_email=bool(person.has_email),
                    confidence_score=70.0 if person.has_email else 45.0,
                    usefulness_score=usefulness,
                    rank_reason=rank_reason,
                    status=ProspectStatus.REVIEW,
                )
                db.add(contact)
                people_imported += 1
                imported_this_round += 1

            db.flush()

            if people_imported >= target:
                break

            if not auto_expand_to_target:
                break

            # Full page from Apollo → more pages may exist; retry before widening.
            if len(discovered_people) >= fetch_limit:
                round_num += 1
                if imported_this_round == 0:
                    rounds_without_progress += 1
                else:
                    rounds_without_progress = 0
                if rounds_without_progress < 3:
                    continue

            working_criteria, note = next_expansion(working_criteria, expansion_state)
            if note:
                expansion_adjustments.append(note)
                round_num += 1
                rounds_without_progress = 0
            else:
                break

        if generate_insights:
            from app.services.insights.engine import generate_insight

            created = db.execute(
                select(Contact).where(Contact.discovery_run_id == run.id)
            ).scalars().all()
            for contact in created:
                company = db.get(Company, contact.company_id) if contact.company_id else None
                try:
                    generate_insight(db, principal, contact=contact, company=company)
                    insights_generated += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Insight generation failed for contact %s: %s", contact.id, exc)

        run.status = DiscoveryStatus.COMPLETED
    except Exception as exc:  # noqa: BLE001
        logger.exception("Discovery run failed")
        run.status = DiscoveryStatus.FAILED
        run.error_message = str(exc)

    criteria_snapshot = _criteria_to_dict(working_criteria)
    criteria_snapshot["criteria_original"] = _criteria_to_dict(original_criteria)
    if search_goal:
        criteria_snapshot["search_goal"] = search_goal
    criteria_snapshot["expansion_adjustments"] = expansion_adjustments
    summary = summarize_adjustments(expansion_adjustments)
    if people_imported < target:
        shortfall = target - people_imported
        shortfall_note = (
            f"Imported {people_imported} of {target} requested ({shortfall} short)."
        )
        if run.provider == "stub":
            shortfall_note += (
                " Stub provider returns mock data only (max ~24 people). "
                "Set DISCOVERY_PROVIDER=apollo in .env and restart the backend."
            )
        elif duplicates:
            shortfall_note += f" {duplicates} skipped as duplicates from prior runs."
        criteria_snapshot["shortfall_note"] = shortfall_note
        if summary:
            summary = f"{summary} {shortfall_note}"
        else:
            summary = shortfall_note
    if summary:
        criteria_snapshot["expansion_summary"] = summary
    run.criteria = criteria_snapshot

    run.organizations_found = orgs_found
    run.organizations_imported = orgs_imported
    run.people_found = people_found
    run.people_imported = people_imported
    run.duplicates = duplicates
    run.insights_generated = insights_generated
    db.flush()

    log_action(
        db,
        AuditAction.DISCOVER,
        entity_type="discovery_run",
        entity_id=run.id,
        actor=requested_by,
        summary=(
            f"Discovery via {run.provider}: {people_imported}/{target} people imported, "
            f"{duplicates} skipped as duplicates ({run.status})."
        ),
        detail=run.criteria,
    )
    db.commit()
    db.refresh(run)
    return run


def _criteria_to_dict(criteria: DiscoveryCriteria) -> dict:
    return {
        "industries": criteria.industries,
        "company_types": criteria.company_types,
        "healthcare_sectors": criteria.healthcare_sectors,
        "geographies": criteria.geographies,
        "titles": criteria.titles,
        "seniorities": criteria.seniorities,
        "contact_email_status": criteria.contact_email_status,
        "organization_domains": criteria.organization_domains,
        "keywords": criteria.keywords,
        "themes": criteria.themes,
        "employee_min": criteria.employee_min,
        "employee_max": criteria.employee_max,
        "org_limit": criteria.org_limit,
        "people_limit": criteria.people_limit,
        "organization_job_titles": criteria.organization_job_titles,
    }


def _find_company(db: Session, name: Optional[str]) -> Optional[Company]:
    if not name:
        return None
    from app.services.normalization import normalize_company_name

    normalized = normalize_company_name(name)
    if not normalized:
        return None
    return db.execute(
        select(Company).where(Company.normalized_name == normalized)
    ).scalar_one_or_none()


def _find_contact(
    db: Session,
    external_id: Optional[str],
    name: str,
    company: Optional[Company],
    linkedin_url: Optional[str] = None,
    *,
    campaign_id: Optional[int] = None,
) -> Optional[Contact]:
    """Dedup within a campaign (or globally when campaign_id is unset)."""
    def _scoped(q):
        if campaign_id is not None:
            return q.where(Contact.campaign_id == campaign_id)
        return q

    if external_id:
        found = db.execute(
            _scoped(select(Contact).where(Contact.external_id == external_id))
        ).scalars().first()
        if found:
            return found
    if linkedin_url:
        found = db.execute(
            _scoped(select(Contact).where(Contact.linkedin_url == linkedin_url))
        ).scalars().first()
        if found:
            return found
    if company is not None:
        return db.execute(
            _scoped(
                select(Contact).where(
                    Contact.company_id == company.id,
                    Contact.name == name,
                )
            )
        ).scalars().first()
    return None
