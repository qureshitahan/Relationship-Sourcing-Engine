"""Seed ONE approved outreach draft for a local UI test of the Send button.

Creates (idempotently) a throwaway principal + company + prospect whose email is
a recipient you control, plus an APPROVED email draft addressed to them. The
draft then appears on the Drafts page (Approved tab) where you can click
"Send now" to exercise the real send path end-to-end via the Gmail provider.

IMPORTANT: run this against the SAME database the backend uses. Locally, set a
local SQLite path in both terminals first, e.g. (PowerShell):
    $env:DATABASE_URL = "sqlite:///./data/rse_local.db"

Usage (from the backend/ directory):
    python scripts/seed_test_draft.py your.test.inbox@gmail.com
    # or rely on TEST_RECIPIENT / default to the configured Gmail address
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.db.session import SessionLocal, init_db
from app.models.company import Company
from app.models.contact import Contact
from app.models.email_draft import EmailDraft
from app.models.enums import EmailStatus, ProspectStatus
from app.models.principal import Principal

PRINCIPAL_NAME = "Dalbir Bains (Local Test)"
COMPANY_NAME = "RSE Local Test Org"


def main() -> None:
    recipient = (
        (sys.argv[1] if len(sys.argv) > 1 else "")
        or os.environ.get("TEST_RECIPIENT", "")
        or settings.gmail_address
        or settings.outreach_from_email
    ).strip()
    if not recipient:
        raise SystemExit("No recipient. Pass one as the first argument.")

    init_db()
    db = SessionLocal()
    try:
        principal = (
            db.query(Principal).filter(Principal.name == PRINCIPAL_NAME).first()
        )
        if principal is None:
            principal = Principal(
                name=PRINCIPAL_NAME,
                headline="Healthcare operator (local test)",
                linkedin_url="https://www.linkedin.com/in/dalbir-bains/",
                phone="+1-000-000-0000",
            )
            db.add(principal)
            db.flush()

        company = db.query(Company).filter(Company.name == COMPANY_NAME).first()
        if company is None:
            company = Company(name=COMPANY_NAME, industry="Test")
            db.add(company)
            db.flush()

        contact = (
            db.query(Contact)
            .filter(Contact.email == recipient, Contact.company_id == company.id)
            .first()
        )
        if contact is None:
            contact = Contact(
                company_id=company.id,
                name="Local Test Recipient",
                title="Send-button test",
                email=recipient,
                has_email=True,
                status=ProspectStatus.REVIEW,
                approved_for_outreach=True,
            )
            db.add(contact)
            db.flush()

        stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        draft = EmailDraft(
            principal_id=principal.id,
            company_id=company.id,
            contact_id=contact.id,
            subject=f"[RSE UI test] Send-button check — {stamp}",
            body=(
                "Hi there,\n\n"
                "This approved draft was seeded to test the Send button in the UI.\n"
                "If you received it, the Gmail integration works end-to-end from the app.\n\n"
                "Best,\nDalbir"
            ),
            status=EmailStatus.APPROVED,
            approved_by="local-test",
            approved_at=datetime.utcnow(),
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)

        print("Seeded an APPROVED draft for the UI Send test:")
        print(f"  draft id  : {draft.id}")
        print(f"  recipient : {recipient}")
        print(f"  principal : {principal.name} (#{principal.id})")
        print()
        print("Now open the frontend -> Drafts -> 'Approved' tab -> click 'Send now'.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
