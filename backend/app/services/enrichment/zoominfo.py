"""ZoomInfo enrichment provider (skeleton).

Mirrors the Apollo provider. Implement real calls when ZOOMINFO_API_KEY is set.
"""
from __future__ import annotations

from typing import List, Optional

from app.core.config import settings
from app.services.enrichment.base import (
    EnrichmentContact,
    EnrichmentProvider,
    EnrichmentResult,
)
from app.services.enrichment.stub import StubEnrichmentProvider


class ZoomInfoEnrichmentProvider(EnrichmentProvider):
    name = "zoominfo"

    def __init__(self) -> None:
        self.api_key = settings.zoominfo_api_key
        self._fallback = StubEnrichmentProvider()

    def enrich_company(
        self,
        company_name: str,
        *,
        linkedin_url: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> EnrichmentResult:
        if not self.api_key:
            result = self._fallback.enrich_company(
                company_name, linkedin_url=linkedin_url, domain=domain
            )
            result.source = "zoominfo (stub fallback: no API key)"
            return result
        raise NotImplementedError("ZoomInfo enrich_company not yet implemented")

    def find_contacts(
        self,
        company_name: str,
        *,
        domain: Optional[str] = None,
        target_titles: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[EnrichmentContact]:
        if not self.api_key:
            return self._fallback.find_contacts(
                company_name, domain=domain, target_titles=target_titles, limit=limit
            )
        raise NotImplementedError("ZoomInfo find_contacts not yet implemented")
