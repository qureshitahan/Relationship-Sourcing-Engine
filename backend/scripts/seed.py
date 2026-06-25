"""Seed script: end-to-end smoke test of the relationship-sourcing pipeline.

Run from the backend/ directory:  python -m scripts.seed

It will:
  1. Create tables.
  2. Create a sample principal (Dalbir Bains) and an ICP search definition.
  3. Run a discovery (uses the stub provider unless Apollo is configured).
  4. Score relevance insights for each prospect.
  5. Draft a personalized outreach email for the top prospect.

Forces stub providers so it runs offline with no API keys.
"""
from __future__ import annotations

import os
import sys

# Force offline stub providers for a deterministic local smoke test.
os.environ["DISCOVERY_PROVIDER"] = "stub"
os.environ["FORCE_DISCOVERY_STUB"] = "1"
os.environ.setdefault("ENRICHMENT_PROVIDER", "stub")
os.environ.setdefault("LLM_PROVIDER", "stub")

# Allow running as `python -m scripts.seed` or `python scripts/seed.py`.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.db.session import SessionLocal, init_db
from app.models.contact import Contact
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.models.search_definition import SearchDefinition
from app.services.discovery import run_discovery
from app.services.enrichment.base import DiscoveryCriteria
from app.services.insights.engine import generate_outreach


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        principal = db.execute(
            select(Principal).where(Principal.name == "Dalbir Bains")
        ).scalar_one_or_none()
        if principal is None:
            principal = Principal(
                name="Dalbir Bains",
                headline="Healthcare operator, former founder/CEO, board & advisory candidate",
                linkedin_url="https://www.linkedin.com/in/dalbir-bains/",
                objective=(
                    "Secure independent board and advisory roles at PE-backed healthcare "
                    "services companies (home health, behavioral health, pharma services), "
                    "by building relationships with the investors, operating partners, and "
                    "executives who influence board appointments."
                ),
                background=(
                    "Private-equity-backed healthcare operator and platform architect with "
                    "deep M&A, growth, and operational transformation experience across "
                    "healthcare services."
                ),
                focus_areas=["Healthcare Services", "Home Health", "Behavioral Health"],
                target_sectors=["Home Health", "Behavioral Health", "Pharma Services"],
                investment_themes=["Platform consolidation", "Roll-ups"],
                acquisition_themes=["Carve-outs", "Founder-led businesses"],
                target_titles=["CEO", "Founder", "Operating Partner", "Board Member"],
                target_seniorities=["c_suite", "owner", "partner"],
                geographies=["United States"],
                opportunity_types=["advisory", "board", "consulting", "acquisition"],
                value_props=["M&A integration", "PE-backed scaling", "Operational transformation"],
            )
            db.add(principal)
            db.commit()
            db.refresh(principal)
            print(f"Created principal: {principal.name}")

        definition = db.execute(
            select(SearchDefinition).where(
                SearchDefinition.principal_id == principal.id,
                SearchDefinition.name == "Healthcare platforms & investors",
            )
        ).scalar_one_or_none()
        if definition is None:
            definition = SearchDefinition(
                principal_id=principal.id,
                name="Healthcare platforms & investors",
                industries=["Healthcare Services"],
                healthcare_sectors=["Home Health", "Behavioral Health"],
                company_types=["operating_company", "private_equity"],
                geographies=["United States"],
                titles=["CEO", "Operating Partner", "Board Member"],
                seniorities=["c_suite", "partner", "owner"],
                themes=["platform consolidation"],
                employee_min=50,
                employee_max=5000,
            )
            db.add(definition)
            db.commit()
            db.refresh(definition)
            print(f"Created search definition: {definition.name}")

        criteria = DiscoveryCriteria(
            industries=definition.industries,
            company_types=definition.company_types,
            healthcare_sectors=definition.healthcare_sectors,
            geographies=definition.geographies,
            titles=definition.titles,
            seniorities=definition.seniorities,
            themes=definition.themes,
            employee_min=definition.employee_min,
            employee_max=definition.employee_max,
            org_limit=4,
            people_limit=8,
        )

        print("\nRunning discovery (stub provider)...")
        run = run_discovery(
            db,
            principal,
            criteria,
            search_definition_id=definition.id,
            requested_by="seed",
        )
        print(
            f"  status={run.status} orgs={run.organizations_imported} "
            f"prospects={run.people_imported} insights={run.insights_generated}"
        )

        top = db.execute(
            select(Contact).order_by(Contact.relevance_score.desc().nullslast())
        ).scalars().first()
        if top:
            insight = db.execute(
                select(RelevanceInsight).where(
                    RelevanceInsight.contact_id == top.id,
                    RelevanceInsight.principal_id == principal.id,
                )
            ).scalar_one_or_none()
            print(
                f"\nTop prospect: {top.name} ({top.title}) "
                f"role={top.role_category} relevance={top.relevance_score}"
            )
            if insight:
                print(f"  Why relevant: {insight.why_relevant}")
            from app.models.company import Company

            company = db.get(Company, top.company_id) if top.company_id else None
            email = generate_outreach(db, principal, top, company, insight)
            print(f"\nDraft outreach subject: {email.subject}")
            print(email.body)

        print("\nSeed complete. Start the API with: uvicorn app.main:app --reload")
    finally:
        db.close()


if __name__ == "__main__":
    main()
