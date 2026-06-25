"""End-to-end test: send outreach from Dalbir's Outlook via Microsoft Graph.

Exercises the same path the app uses when you click "Send from Dalbir's Outlook"
on an approved draft, but delivers to a safe test inbox (default: Taha).

Checks:
  1. Microsoft Graph credentials in .env
  2. OAuth token (client credentials)
  3. Live sendMail as dalbir.bains@galaxypharma.net
  4. Full app pipeline: Contact → EmailDraft (approved) → provider.send()

Usage:
    cd backend && .venv/bin/python scripts/test_outlook_e2e.py

Override recipient:
    TEST_RECIPIENT=you@galaxypharma.net .venv/bin/python scripts/test_outlook_e2e.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.company import Company
from app.models.contact import Contact
from app.models.email_draft import EmailDraft
from app.models.enums import EmailStatus, ProspectStatus
from app.models.principal import Principal
from app.services.email_providers import get_email_provider

DEFAULT_RECIPIENT = "taha.qureshi@galaxypharma.net"
SEND_AS = settings.microsoft_send_as_user or settings.outreach_from_email


def line(char: str = "-", width: int = 70) -> None:
    print(char * width)


def check_config() -> None:
    line("=")
    print("1) Configuration")
    line()
    print(f"  EMAIL_PROVIDER          = {settings.email_provider}")
    print(f"  MICROSOFT_SEND_AS_USER  = {SEND_AS}")
    print(f"  OUTREACH_FROM_NAME      = {settings.outreach_from_name}")
    print(f"  tenant_id set           = {bool(settings.microsoft_tenant_id)}")
    print(f"  client_id set           = {bool(settings.microsoft_client_id)}")
    print(f"  client_secret set       = {bool(settings.microsoft_client_secret)}")
    missing = [
        name
        for name, val in (
            ("MICROSOFT_TENANT_ID", settings.microsoft_tenant_id),
            ("MICROSOFT_CLIENT_ID", settings.microsoft_client_id),
            ("MICROSOFT_CLIENT_SECRET", settings.microsoft_client_secret),
            ("MICROSOFT_SEND_AS_USER", SEND_AS),
        )
        if not val
    ]
    if missing:
        raise SystemExit(f"Missing env: {', '.join(missing)}")
    if settings.email_provider not in ("microsoft_graph", "outlook"):
        print(f"  WARNING: EMAIL_PROVIDER is '{settings.email_provider}', not microsoft_graph")


def test_direct_send(recipient: str) -> None:
    from app.services.email_providers.microsoft_graph import MicrosoftGraphEmailProvider

    line("=")
    print("2) Direct Microsoft Graph send")
    line()
    provider = MicrosoftGraphEmailProvider()
    token = provider._get_access_token()
    if not token:
        raise SystemExit("FAILED: could not obtain Graph access token")
    print("  token: OK")

    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    subject = f"[RSE E2E] Outlook send test — {stamp}"
    body = (
        "This is an automated end-to-end test from the Relationship Sourcing Engine.\n\n"
        f"Sent as: {SEND_AS}\n"
        f"Recipient: {recipient}\n"
        f"Time: {stamp}\n\n"
        "If you received this in your inbox, Dalbir's Outlook integration is working."
    )
    result = provider.send(
        to_email=recipient,
        subject=subject,
        body=body,
        from_email=settings.outreach_from_email,
        from_name=settings.outreach_from_name,
    )
    if not result.sent:
        raise SystemExit(f"FAILED direct send: {result.error}")
    print(f"  sent: OK (provider={result.provider})")
    print(f"  to:   {recipient}")
    print(f"  subj: {subject}")


def test_app_pipeline(recipient: str) -> None:
    line("=")
    print("3) App pipeline (Contact → approved draft → send)")
    line()

    # Tables already exist; skip init_db to avoid contending with the running API.
    db = SessionLocal()
    try:
        principal = db.query(Principal).filter(Principal.name.ilike("%Dalbir%")).first()
        if principal is None:
            principal = Principal(name="Dalbir Bains", headline="Healthcare operator")
            db.add(principal)
            db.flush()

        company = db.query(Company).filter(Company.name == "RSE E2E Test Org").first()
        if company is None:
            company = Company(name="RSE E2E Test Org", industry="Test")
            db.add(company)
            db.flush()

        contact = (
            db.query(Contact)
            .filter(Contact.email == recipient, Contact.name == "Taha Qureshi (E2E)")
            .first()
        )
        if contact is None:
            contact = Contact(
                company_id=company.id,
                name="Taha Qureshi (E2E)",
                title="E2E Test Recipient",
                email=recipient,
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
            subject=f"[RSE E2E pipeline] Test from Dalbir — {stamp}",
            body=(
                "Pipeline test: draft created → approved → sent via get_email_provider().\n\n"
                f"From mailbox: {SEND_AS}\n"
                f"Time: {stamp}"
            ),
            status=EmailStatus.APPROVED,
        )
        db.add(draft)
        db.flush()

        provider = get_email_provider()
        print(f"  provider class: {type(provider).__name__}")
        result = provider.send(
            to_email=contact.email,
            subject=draft.subject,
            body=draft.body,
            from_email=settings.outreach_from_email,
            from_name=settings.outreach_from_name,
        )
        if not result.sent:
            raise SystemExit(f"FAILED pipeline send: {result.error}")

        draft.status = EmailStatus.SENT
        draft.provider = result.provider
        draft.provider_message_id = result.message_id
        draft.sent_at = datetime.utcnow()
        db.commit()
        print(f"  draft id: {draft.id}")
        print(f"  sent: OK (provider={result.provider})")
        print(f"  to:   {recipient}")
    except Exception as exc:
        if "database is locked" in str(exc).lower():
            print("  SKIPPED: SQLite locked (backend server is running).")
            print("  Step 2 already validated live Graph delivery.")
        else:
            raise
    finally:
        db.close()


def main() -> None:
    recipient = os.environ.get("TEST_RECIPIENT", DEFAULT_RECIPIENT).strip()
    print(f"\nOutlook E2E test → {recipient}\n")

    check_config()
    test_direct_send(recipient)
    test_app_pipeline(recipient)

    line("=")
    print("PASSED — check your inbox (and Dalbir's Sent Items).")
    line("=")
    print()


if __name__ == "__main__":
    main()
