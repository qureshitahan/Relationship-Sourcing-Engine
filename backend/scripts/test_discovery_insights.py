"""Smoke test for the discovery + insight pipeline using stub providers.

Runs fully offline (no API keys). Verifies that an ICP discovery produces
organizations, prospects, relevance insights, and a personalized outreach draft.

Usage:
    cd backend && python scripts/test_discovery_insights.py
"""
from __future__ import annotations

import os
import sys
import tempfile

# Use stub providers and an isolated throwaway database.
os.environ["DISCOVERY_PROVIDER"] = "stub"
os.environ["FORCE_DISCOVERY_STUB"] = "1"
os.environ["ENRICHMENT_PROVIDER"] = "stub"
os.environ["LLM_PROVIDER"] = "stub"
_tmp_db = os.path.join(tempfile.gettempdir(), "rse_smoke.db")
if os.path.exists(_tmp_db):
    os.remove(_tmp_db)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db}"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.session import SessionLocal, init_db
from app.models.company import Company
from app.models.contact import Contact
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.services.discovery import run_discovery
from app.services.enrichment.base import DiscoveryCriteria
from app.services.insights.engine import generate_outreach


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        principal = Principal(
            name="Dalbir Bains",
            headline="Healthcare operator & board candidate",
            target_sectors=["Home Health", "Behavioral Health"],
            value_props=["M&A integration", "PE-backed scaling"],
            opportunity_types=["advisory", "board"],
        )
        db.add(principal)
        db.commit()
        db.refresh(principal)

        criteria = DiscoveryCriteria(
            industries=["Healthcare Services"],
            healthcare_sectors=["Home Health"],
            titles=["CEO", "Operating Partner"],
            org_limit=4,
            people_limit=8,
        )
        run = run_discovery(db, principal, criteria, requested_by="smoke")

        assert run.status == "completed", run.status
        assert (run.organizations_imported or 0) > 0, "expected organizations"
        assert (run.people_imported or 0) > 0, "expected prospects"
        assert (run.insights_generated or 0) > 0, "expected insights"

        orgs = db.query(Company).count()
        prospects = db.query(Contact).count()
        insights = db.query(RelevanceInsight).count()
        print(f"orgs={orgs} prospects={prospects} insights={insights}")

        top = (
            db.query(Contact)
            .order_by(Contact.relevance_score.desc())
            .first()
        )
        assert top is not None and top.relevance_score is not None
        ins = (
            db.query(RelevanceInsight)
            .filter_by(contact_id=top.id, principal_id=principal.id)
            .first()
        )
        assert ins is not None and ins.why_relevant
        print(f"top prospect: {top.name} ({top.title}) rel={top.relevance_score}")
        print(f"why_relevant: {ins.why_relevant}")

        company = db.get(Company, top.company_id) if top.company_id else None
        email = generate_outreach(principal, top, company, ins)
        assert email.subject and email.body
        print(f"outreach subject: {email.subject}")
        print("\nSmoke test PASSED.")
    finally:
        db.close()
        if os.path.exists(_tmp_db):
            os.remove(_tmp_db)


if __name__ == "__main__":
    main()
