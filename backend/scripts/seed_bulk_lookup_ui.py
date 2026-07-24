"""Seed a bulk campaign with one lookup in each state, for checking the review UI.

    DATABASE_URL=sqlite:///./data/ui_check.db python scripts/seed_bulk_lookup_ui.py

Uses fixed, obviously-fake data so no provider is called and no credits are spent.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.models.bulk_campaign import BulkCampaign, BulkLookup  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.models.contact import Contact  # noqa: E402
from app.models.enums import (  # noqa: E402
    BulkCampaignStatus,
    BulkLookupStatus,
    ProspectStatus,
)

PEOPLE = [
    {
        "name": "Dr. Jeffrey Egler",
        "title": "CMO",
        "company": "Noom",
        "source_text": "Dr. Jeffrey Egler — CMO, Noom (digital health, 503A relationship) — pro",
        "status": BulkLookupStatus.FOUND,
        "email": "jeff.egler@noom.example",
        "email_status": "verified",
        "confidence": 0.99,
        "resolved_org": "Noom",
        "resolved_domain": "noom.com",
        "linkedin_url": "https://www.linkedin.com/in/example-egler/",
        "reason": "Company press release names him as CMO; LinkedIn lists Noom as current employer.",
        "evidence": [
            {"title": "Noom appoints Dr. Jeffrey Egler as CMO", "url": "https://example.com/noom-cmo"},
            {"title": "noom.com leadership", "url": "https://example.com/noom-leadership"},
        ],
    },
    {
        "name": "Dr. Alex Tatum",
        "title": "Private-practice urologist",
        "company": None,
        "source_text": "Dr. Alex Tatum — Private-practice urologist, Indianapolis; AAPM partner pharmacies — pro",
        "status": BulkLookupStatus.FOUND,
        "email": "a.tatem@urologyin.example",
        "email_status": "guessed",
        "confidence": 0.72,
        "resolved_name": "Alexander J. Tatem",
        "resolved_title": "Urologist, Men's Health Center",
        "resolved_org": "Urology of Indiana",
        "resolved_domain": "urologyin.com",
        "reason": "'Tatum' appears to be a phonetic misspelling of Tatem, the only matching Indianapolis urologist.",
        "evidence": [{"title": "Urology of Indiana physicians", "url": "https://example.com/uoi"}],
    },
    {
        "name": "Dr. Skelcy",
        "title": "Physician",
        "company": None,
        "source_text": 'Dr. "Skelcy/Scalzi/Stahlberg" (approx.) — Physician — voted NO',
        "status": BulkLookupStatus.AMBIGUOUS,
        "confidence": 0.15,
        "reason": "The pasted list gives three possible spellings and no employer, so several different people fit.",
    },
    {
        "name": "Kea Stevenson",
        "title": "Designated Federal Officer (DFO)",
        "company": "FDA",
        "source_text": "Kea Stevenson — Designated Federal Officer (DFO), FDA — ran logistics & read votes",
        "status": BulkLookupStatus.NOT_FOUND,
        "confidence": 0.88,
        "resolved_org": "U.S. Food and Drug Administration",
        "resolved_domain": "fda.hhs.gov",
        "reason": "Identified the person, but no address is on file for them.",
        "evidence": [{"title": "FDA advisory committee roster", "url": "https://example.com/fda-roster"}],
    },
]


def company_id(db, name: str | None) -> int | None:
    if not name:
        return None
    normalized = name.lower()
    company = db.query(Company).filter_by(normalized_name=normalized).first()
    if company is None:
        company = Company(name=name, normalized_name=normalized, enrichment_source="bulk_paste")
        db.add(company)
        db.flush()
    return company.id


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        campaign = BulkCampaign(
            name="FDA event follow-up",
            mailbox_id="sahar",
            status=BulkCampaignStatus.COLLECTING,
            purpose=(
                "I met these people at the FDA peptide advisory committee meeting "
                "yesterday. Say it was good to meet them and that I'd like to keep in touch."
            ),
        )
        db.add(campaign)
        db.commit()

        for person in PEOPLE:
            contact = Contact(
                name=person["name"],
                title=person["title"],
                has_email=False,
                notes=person["source_text"],
                source="bulk_paste",
                bulk_campaign_id=campaign.id,
                company_id=company_id(db, person["company"]),
                status=ProspectStatus.APPROVED,
                approved_for_outreach=True,
            )
            db.add(contact)
            db.flush()
            db.add(
                BulkLookup(
                    campaign_id=campaign.id,
                    contact_id=contact.id,
                    status=person["status"],
                    source_text=person["source_text"],
                    resolved_name=person.get("resolved_name"),
                    resolved_title=person.get("resolved_title"),
                    resolved_org=person.get("resolved_org"),
                    resolved_domain=person.get("resolved_domain"),
                    linkedin_url=person.get("linkedin_url"),
                    confidence=person.get("confidence"),
                    reason=person.get("reason"),
                    evidence=person.get("evidence"),
                    email=person.get("email"),
                    email_status=person.get("email_status"),
                )
            )
        db.commit()
        print(f"Seeded campaign {campaign.id} with {len(PEOPLE)} lookups.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
