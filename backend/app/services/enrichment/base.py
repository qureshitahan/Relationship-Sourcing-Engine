"""Enrichment provider interface and result shape."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class EnrichmentContact:
    name: str
    title: Optional[str] = None
    email: Optional[str] = None
    email_status: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    location: Optional[str] = None
    confidence_score: Optional[float] = None
    # Provider-side stable id (e.g. Apollo person id) used to reveal email/phone.
    external_id: Optional[str] = None
    # Company domain this contact belongs to (helps re-match during reveal).
    domain: Optional[str] = None


@dataclass
class DiscoveryCriteria:
    """Ideal-Customer-Profile filters used to drive Apollo discovery search."""

    industries: Optional[List[str]] = None
    company_types: Optional[List[str]] = None
    healthcare_sectors: Optional[List[str]] = None
    geographies: Optional[List[str]] = None
    titles: Optional[List[str]] = None
    seniorities: Optional[List[str]] = None
    # Apollo contact_email_status (verified, unverified, likely to engage, unavailable).
    contact_email_status: Optional[List[str]] = None
    # Employer domains — Apollo q_organization_domains_list (no www. or @).
    organization_domains: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    themes: Optional[List[str]] = None
    employee_min: Optional[int] = None
    employee_max: Optional[int] = None
    org_limit: int = 25
    people_limit: int = 25
    # Active job postings at the person's employer (Apollo q_organization_job_titles).
    # Used for a supplementary search; matches are prioritized in results.
    organization_job_titles: Optional[List[str]] = None


@dataclass
class DiscoveredOrganization:
    name: str
    domain: Optional[str] = None
    website: Optional[str] = None
    linkedin_url: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    headquarters: Optional[str] = None
    phone: Optional[str] = None
    funding: Optional[str] = None
    revenue: Optional[str] = None
    external_id: Optional[str] = None
    keywords: Optional[List[str]] = None


@dataclass
class DiscoveredPerson:
    name: str
    title: Optional[str] = None
    seniority: Optional[str] = None
    linkedin_url: Optional[str] = None
    external_id: Optional[str] = None
    has_email: bool = False
    organization_name: Optional[str] = None
    organization_domain: Optional[str] = None
    organization_linkedin_url: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    location: Optional[str] = None
    # Employer has an active job posting matching organization_job_titles criteria.
    has_board_job_signal: bool = False


@dataclass
class EnrichmentResult:
    found: bool
    source: str
    domain: Optional[str] = None
    website: Optional[str] = None
    linkedin_url: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    headquarters: Optional[str] = None
    phone: Optional[str] = None
    funding: Optional[str] = None
    revenue: Optional[str] = None
    contacts: Optional[List[EnrichmentContact]] = None


class EnrichmentProvider(ABC):
    """Implement this to add a new enrichment data source."""

    name: str = "base"

    @abstractmethod
    def enrich_company(
        self,
        company_name: str,
        *,
        linkedin_url: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> EnrichmentResult:
        """Look up firmographics for a company."""

    @abstractmethod
    def find_contacts(
        self,
        company_name: str,
        *,
        domain: Optional[str] = None,
        target_titles: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[EnrichmentContact]:
        """Find candidate contacts at the company."""

    def reveal_contacts(self, contacts: List[EnrichmentContact]) -> None:
        """Reveal email/phone for the given contacts, mutating them in place.

        Default is a no-op (providers that already return contact details, or that
        cannot reveal more, simply do nothing). Providers like Apollo override this
        to call a paid enrichment endpoint for the selected contacts only.
        """
        return None

    def discover_organizations(
        self, criteria: "DiscoveryCriteria"
    ) -> List[DiscoveredOrganization]:
        """Discover organizations matching ICP criteria. Default: none."""
        return []

    def discover_people(
        self,
        criteria: "DiscoveryCriteria",
        *,
        organization_ids: Optional[List[str]] = None,
        direct: bool = False,
        exclude_external_ids: Optional[set[str]] = None,
    ) -> List[DiscoveredPerson]:
        """Discover people matching ICP criteria.

        When ``organization_ids`` is provided, results are scoped to those
        (already ICP-matched) organizations. Default: none.
        """
        return []
