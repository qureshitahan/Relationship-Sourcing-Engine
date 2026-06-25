"""Enrichment + discovery layer.

Pluggable providers behind a common interface. The active provider is chosen by
ENRICHMENT_PROVIDER (for enrichment) and DISCOVERY_PROVIDER (for ICP discovery).
Until real API keys exist, the `stub` provider returns deterministic mock data so
the rest of the pipeline can be built and tested.
"""
from __future__ import annotations

import logging
import os

from app.core.config import settings
from app.services.enrichment.apollo import ApolloEnrichmentProvider
from app.services.enrichment.base import (
    DiscoveredOrganization,
    DiscoveredPerson,
    DiscoveryCriteria,
    EnrichmentContact,
    EnrichmentProvider,
    EnrichmentResult,
)
from app.services.enrichment.stub import StubEnrichmentProvider
from app.services.enrichment.zoominfo import ZoomInfoEnrichmentProvider

logger = logging.getLogger(__name__)

_PROVIDERS = {
    "stub": StubEnrichmentProvider,
    "apollo": ApolloEnrichmentProvider,
    "zoominfo": ZoomInfoEnrichmentProvider,
}


def _force_stub() -> bool:
    return os.environ.get("FORCE_DISCOVERY_STUB", "").lower() in ("1", "true", "yes")


def get_enrichment_provider() -> EnrichmentProvider:
    """Return the configured enrichment provider (defaults to stub)."""
    provider_cls = _PROVIDERS.get(settings.enrichment_provider, StubEnrichmentProvider)
    return provider_cls()


def get_discovery_provider() -> EnrichmentProvider:
    """Return the configured discovery provider.

    Uses Apollo when ``DISCOVERY_PROVIDER=apollo`` or when an Apollo API key is
    configured (unless ``FORCE_DISCOVERY_STUB=1`` for offline seed/tests).
    """
    name = (settings.discovery_provider or "apollo").lower()

    if name == "stub":
        if _force_stub() or not settings.apollo_api_key.strip():
            return StubEnrichmentProvider()
        logger.info(
            "DISCOVERY_PROVIDER=stub ignored — APOLLO_API_KEY is set; using Apollo. "
            "Set FORCE_DISCOVERY_STUB=1 to force offline stub discovery."
        )
        return ApolloEnrichmentProvider()

    provider_cls = _PROVIDERS.get(name, ApolloEnrichmentProvider)
    if provider_cls is ApolloEnrichmentProvider and not settings.apollo_api_key.strip():
        logger.warning("Apollo discovery requested but APOLLO_API_KEY is missing — using stub")
        return StubEnrichmentProvider()
    return provider_cls()


__all__ = [
    "EnrichmentProvider",
    "EnrichmentResult",
    "EnrichmentContact",
    "DiscoveryCriteria",
    "DiscoveredOrganization",
    "DiscoveredPerson",
    "get_enrichment_provider",
    "get_discovery_provider",
]
