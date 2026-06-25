"""End-to-end test for the Apollo enrichment integration.

Runs three checks against the live Apollo API using the configured APOLLO_API_KEY:
  1. Company enrichment by known domain (figma.com).
  2. Company enrichment by name only (no domain) -> resolves domain then enriches.
  3. Contact discovery (People Search) for that company's domain.

Then exercises the real DB flow (enrich_and_find_contacts) against the first
company already imported in the database, mirroring what the "Enrich" button does.

Usage:
    cd backend && . .venv/bin/activate && python scripts/test_apollo_enrichment.py
"""
from __future__ import annotations

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.company import Company
from app.services.contacts import enrich_and_find_contacts
from app.services.enrichment import get_enrichment_provider


def line(char: str = "-") -> None:
    print(char * 70)


def test_provider_direct() -> None:
    print(f"\nProvider: {settings.enrichment_provider} | key set: {bool(settings.apollo_api_key)}")
    provider = get_enrichment_provider()

    line("=")
    print("1) Company enrichment BY DOMAIN (figma.com)")
    line()
    r = provider.enrich_company("Figma", domain="figma.com")
    print(f"  found={r.found} source={r.source}")
    print(f"  domain={r.domain} industry={r.industry} employees={r.employee_count}")
    print(f"  hq={r.headquarters} phone={r.phone} revenue={r.revenue} funding={r.funding}")
    print(f"  linkedin={r.linkedin_url}")

    line("=")
    print("2) Company enrichment BY NAME ONLY (Stripe)")
    line()
    r2 = provider.enrich_company("Stripe")
    print(f"  found={r2.found} source={r2.source}")
    print(f"  domain={r2.domain} industry={r2.industry} employees={r2.employee_count}")
    print(f"  hq={r2.headquarters} revenue={r2.revenue}")

    line("=")
    print("3) Contact discovery (People Search) for figma.com")
    line()
    titles = ["Chief Executive Officer", "Founder", "Operating Partner", "Board Member"]
    contacts = provider.find_contacts("Figma", domain="figma.com", target_titles=titles)
    print(f"  contacts returned: {len(contacts)}")
    for c in contacts[:10]:
        print(f"   - {c.name} | {c.title} | email={c.email} | conf={c.confidence_score}")
    if not contacts:
        print("  (none — People Search requires a MASTER key; trial keys return nothing)")


def test_db_flow() -> None:
    line("=")
    print("4) Full DB flow: enrich_and_find_contacts on first company in DB")
    line()
    db = SessionLocal()
    try:
        company = db.query(Company).first()
        if not company:
            print("  No companies in DB — run a job search/import first. Skipping.")
            return
        print(f"  Company: {company.name} (id={company.id}) existing domain={company.domain}")
        created = enrich_and_find_contacts(db, company)
        db.commit()
        db.refresh(company)
        print(f"  enrichment_status={company.enrichment_status} source={company.enrichment_source}")
        print(f"  domain={company.domain} industry={company.industry} employees={company.employee_count}")
        print(f"  hq={company.headquarters} revenue={company.revenue}")
        print(f"  contacts created: {len(created)}")
        for c in created[:10]:
            print(f"   - {c.name} | {c.title} | usefulness={c.usefulness_score} | {c.rank_reason}")
    finally:
        db.close()


if __name__ == "__main__":
    test_provider_direct()
    test_db_flow()
    print("\nDone.")
