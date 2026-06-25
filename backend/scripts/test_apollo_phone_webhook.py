"""Test the Apollo phone webhook handler locally (no live Apollo call).

Simulates Apollo POSTing phone numbers to our webhook after async enrichment.

Usage:
    cd backend && . .venv/bin/activate && PYTHONPATH=. python scripts/test_apollo_phone_webhook.py
"""
from __future__ import annotations

from app.core.config import get_settings

get_settings.cache_clear()

from fastapi.testclient import TestClient

from app.core.config import resolve_apollo_phone_webhook_url, settings
from app.db.session import SessionLocal, init_db
from app.main import app
from app.models.contact import Contact
from app.models.enums import PhoneRevealStatus


def main() -> None:
    init_db()
    db = SessionLocal()
    contact = (
        db.query(Contact)
        .filter(Contact.external_id.isnot(None))
        .first()
    )
    if not contact:
        print("No contact with external_id found. Enrich a company first.")
        db.close()
        return

    apollo_id = contact.external_id
    contact.phone = None
    contact.phone_reveal_status = PhoneRevealStatus.PENDING
    db.commit()
    db.refresh(contact)
    print(f"Using contact id={contact.id} external_id={apollo_id} name={contact.name}")

    webhook_url = resolve_apollo_phone_webhook_url()
    print(f"Configured webhook URL: {webhook_url or '(set APP_PUBLIC_URL for live Apollo)'}")

    payload = {
        "status": "success",
        "total_requested_enrichments": 1,
        "unique_enriched_records": 1,
        "missing_records": 0,
        "credits_consumed": 1,
        "people": [
            {
                "id": apollo_id,
                "status": "success",
                "phone_numbers": [
                    {
                        "raw_number": "+1 555-123-4567",
                        "sanitized_number": "+15551234567",
                        "status_cd": "valid_number",
                        "confidence_cd": "high",
                        "type_cd": "mobile",
                        "position": 0,
                    }
                ],
            }
        ],
    }

    client = TestClient(app)
    secret = settings.apollo_phone_webhook_secret
    path = "/api/webhooks/apollo/phone"
    if secret:
        path = f"{path}?token={secret}"

    resp = client.post(path, json=payload)
    print(f"POST {path} -> HTTP {resp.status_code}")
    print(resp.json())

    db.refresh(contact)
    print(f"After webhook: phone={contact.phone} status={contact.phone_reveal_status}")
    ok = contact.phone == "+15551234567" and contact.phone_reveal_status == PhoneRevealStatus.REVEALED
    print("PASS" if ok else "FAIL")
    db.close()


if __name__ == "__main__":
    main()
