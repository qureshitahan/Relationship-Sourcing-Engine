"""Stub enrichment + discovery provider.

Returns deterministic mock data so the discovery, enrichment, and insight flows
can be built and demoed without real API keys.
"""
from __future__ import annotations

import re
from typing import List, Optional

from app.services.enrichment.base import (
    DiscoveredOrganization,
    DiscoveredPerson,
    DiscoveryCriteria,
    EnrichmentContact,
    EnrichmentProvider,
    EnrichmentResult,
)


def _guess_domain(company_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "", company_name.lower())
    return f"{slug or 'company'}.example.com"


# Deterministic sample organizations used by the stub discovery flow.
_SAMPLE_ORGS = [
    ("Meridian Health Partners", "Healthcare Services", 850),
    ("Cascade Behavioral Group", "Behavioral Health", 420),
    ("Summit Home Health", "Home Health", 1300),
    ("Evergreen Capital Partners", "Private Equity", 60),
    ("Northstar Ventures", "Venture Capital", 35),
    ("Harbor Family Office", "Family Office", 20),
]

_SAMPLE_PEOPLE = [
    ("Dana Chen", "Chief Executive Officer", "c_suite"),
    ("Marcus Patel", "Operating Partner", "partner"),
    ("Renee Alvarez", "Board Member", "c_suite"),
    ("Samuel Okafor", "Founder & Managing Partner", "owner"),
]


class StubEnrichmentProvider(EnrichmentProvider):
    name = "stub"

    def enrich_company(
        self,
        company_name: str,
        *,
        linkedin_url: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> EnrichmentResult:
        domain = domain or _guess_domain(company_name)
        return EnrichmentResult(
            found=True,
            source=self.name,
            domain=domain,
            website=f"https://{domain}",
            linkedin_url=linkedin_url,
            industry="Healthcare Services",
            employee_count=120,
            headquarters="Remote / Unknown (stub)",
            phone="+1-555-0100",
            funding="Series B (stub)",
            revenue="$50M (stub)",
        )

    def find_contacts(
        self,
        company_name: str,
        *,
        domain: Optional[str] = None,
        target_titles: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[EnrichmentContact]:
        domain = domain or _guess_domain(company_name)
        contacts = [
            EnrichmentContact(
                name="Dana Chen",
                title="Chief Executive Officer",
                email=f"dana.chen@{domain}",
                linkedin_url=None,
                confidence_score=75.0,
            ),
            EnrichmentContact(
                name="Marcus Patel",
                title="Operating Partner",
                email=f"marcus.patel@{domain}",
                linkedin_url=None,
                confidence_score=65.0,
            ),
        ]
        if limit is not None:
            return contacts[: max(0, limit)]
        return contacts

    def discover_organizations(
        self, criteria: DiscoveryCriteria
    ) -> List[DiscoveredOrganization]:
        limit = max(1, min(criteria.org_limit or 25, len(_SAMPLE_ORGS)))
        orgs: List[DiscoveredOrganization] = []
        for name, industry, headcount in _SAMPLE_ORGS[:limit]:
            domain = _guess_domain(name)
            orgs.append(
                DiscoveredOrganization(
                    name=name,
                    domain=domain,
                    website=f"https://{domain}",
                    industry=industry,
                    employee_count=headcount,
                    headquarters="United States (stub)",
                    funding="Growth equity (stub)",
                    external_id=f"stub-org-{re.sub(r'[^a-z0-9]', '', name.lower())}",
                    keywords=criteria.keywords,
                )
            )
        return orgs

    def discover_people(
        self,
        criteria: DiscoveryCriteria,
        *,
        organization_ids: Optional[List[str]] = None,
        direct: bool = False,
        exclude_external_ids: Optional[set[str]] = None,
    ) -> List[DiscoveredPerson]:
        exclude = exclude_external_ids or set()
        limit = max(1, min(criteria.people_limit or 25, len(_SAMPLE_PEOPLE) * len(_SAMPLE_ORGS)))
        people: List[DiscoveredPerson] = []
        for org_name, industry, headcount in _SAMPLE_ORGS:
            for person_name, title, seniority in _SAMPLE_PEOPLE:
                if len(people) >= limit:
                    return people
                domain = _guess_domain(org_name)
                eid = f"stub-{re.sub(r'[^a-z0-9]', '', (org_name + person_name).lower())}"
                if eid in exclude:
                    continue
                people.append(
                    DiscoveredPerson(
                        name=person_name,
                        title=title,
                        seniority=seniority,
                        external_id=eid,
                        has_email=True,
                        organization_name=org_name,
                        organization_domain=domain,
                        industry=industry,
                        employee_count=headcount,
                        location="United States (stub)",
                    )
                )
        return people
