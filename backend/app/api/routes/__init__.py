"""API route registration."""
from fastapi import APIRouter

from app.api.routes import (
    agent,
    automation,
    bulk_emails,
    calls,
    campaigns,
    discovery,
    emails,
    insights,
    linkedin,
    linkedin_followers,
    optimization,
    organizations,
    principals,
    prospects,
    search_definitions,
    stats,
    webhooks,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(stats.router)
api_router.include_router(agent.router)
api_router.include_router(campaigns.router)
api_router.include_router(principals.router)
api_router.include_router(search_definitions.router)
api_router.include_router(discovery.router)
api_router.include_router(organizations.router)
api_router.include_router(prospects.router)
api_router.include_router(insights.router)
api_router.include_router(emails.router)
api_router.include_router(bulk_emails.router)
api_router.include_router(linkedin.router)
api_router.include_router(linkedin_followers.router)
api_router.include_router(calls.router)
api_router.include_router(webhooks.router)
api_router.include_router(optimization.router)
api_router.include_router(automation.router)

__all__ = ["api_router"]
