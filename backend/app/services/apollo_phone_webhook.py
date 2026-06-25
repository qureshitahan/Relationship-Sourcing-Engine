"""Process Apollo async phone-number webhook payloads.

Apollo delivers phone numbers several minutes after a bulk_match / people/match
request with reveal_phone_number=true. The payload matches contacts in our DB
by Apollo person id (Contact.external_id).

Docs: https://docs.apollo.io/docs/retrieve-mobile-phone-numbers-for-contacts
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.contact import Contact
from app.models.enums import AuditAction, PhoneRevealStatus
from app.services.audit import log_action

logger = logging.getLogger(__name__)

_MOBILE_TYPE_HINTS = ("mobile", "cell", "personal")


def pick_best_phone(phone_numbers: List[Dict[str, Any]]) -> Optional[str]:
    """Choose the most useful phone from Apollo's phone_numbers array."""
    if not phone_numbers:
        return None

    def score(entry: Dict[str, Any]) -> int:
        points = 0
        type_cd = (entry.get("type_cd") or "").lower()
        if any(hint in type_cd for hint in _MOBILE_TYPE_HINTS):
            points += 100
        if entry.get("status_cd") == "valid_number":
            points += 50
        if (entry.get("confidence_cd") or "").lower() == "high":
            points += 25
        position = entry.get("position")
        if isinstance(position, int):
            points -= position
        return points

    best = max(phone_numbers, key=score)
    return best.get("sanitized_number") or best.get("raw_number")


def process_apollo_phone_webhook(db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a webhook payload to matching Contact rows. Returns summary stats."""
    people = payload.get("people") or []
    updated = 0
    unavailable = 0
    not_found = 0

    for person in people:
        if not isinstance(person, dict):
            continue

        apollo_id = person.get("id")
        if not apollo_id:
            continue

        # Match all contacts sharing this Apollo person id. Duplicates can exist
        # (e.g. the same person imported for multiple jobs), so update them all
        # instead of using one_or_none(), which would raise on duplicates.
        contacts = (
            db.query(Contact).filter(Contact.external_id == apollo_id).all()
        )
        if not contacts:
            not_found += 1
            logger.info("Apollo phone webhook: no contact for external_id=%s", apollo_id)
            continue

        person_status = (person.get("status") or "").lower()
        if person_status and person_status != "success":
            for contact in contacts:
                contact.phone_reveal_status = PhoneRevealStatus.UNAVAILABLE
            unavailable += 1
            continue

        phone = pick_best_phone(person.get("phone_numbers") or [])
        if phone:
            for contact in contacts:
                contact.phone = phone
                contact.phone_reveal_status = PhoneRevealStatus.REVEALED
            updated += 1
        else:
            for contact in contacts:
                contact.phone_reveal_status = PhoneRevealStatus.UNAVAILABLE
            unavailable += 1

    if updated or unavailable:
        log_action(
            db,
            AuditAction.PHONE_REVEAL,
            entity_type="webhook",
            entity_id=None,
            summary=f"Apollo phone webhook: {updated} revealed, {unavailable} unavailable.",
            detail={
                "updated": updated,
                "unavailable": unavailable,
                "not_found": not_found,
                "credits_consumed": payload.get("credits_consumed"),
            },
        )

    db.flush()
    return {
        "updated": updated,
        "unavailable": unavailable,
        "not_found": not_found,
        "people_received": len(people),
    }
