"""Inbound webhooks from external providers."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.config import (
    resolve_apollo_phone_webhook_url,
    resolve_vapi_webhook_url,
    settings,
)
from app.db.session import get_db
from app.models.contact import Contact
from app.models.enums import AuditAction, PhoneRevealStatus
from app.services.apollo_phone_webhook import process_apollo_phone_webhook
from app.services.audit import log_action
from app.services.vapi_webhook import process_vapi_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_webhook_token(token: Optional[str], secret: str) -> None:
    secret = (secret or "").strip()
    if not secret:
        return
    if token != secret:
        raise HTTPException(status_code=403, detail="Invalid webhook token")


@router.get("/apollo/phone/status")
def apollo_phone_webhook_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Show whether phone webhooks are configured and how many contacts are pending."""
    webhook_url = resolve_apollo_phone_webhook_url()
    pending = (
        db.query(Contact)
        .filter(Contact.phone_reveal_status == PhoneRevealStatus.PENDING)
        .count()
    )
    revealed = (
        db.query(Contact)
        .filter(Contact.phone_reveal_status == PhoneRevealStatus.REVEALED)
        .count()
    )
    return {
        "phone_reveal_enabled": settings.apollo_reveal_phone_number,
        "webhook_configured": bool(webhook_url),
        "webhook_url_hint": _mask_webhook_url(webhook_url),
        "contacts_pending_phone": pending,
        "contacts_with_phone_revealed": revealed,
    }


@router.post("/apollo/phone")
async def apollo_phone_webhook(
    request: Request,
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Receive async phone numbers from Apollo People Enrichment.

    Apollo may retry on non-2xx responses, so we return 200 after persisting what
    we can even when some people in the payload don't match a contact.
    """
    _verify_webhook_token(token, settings.apollo_phone_webhook_secret)

    try:
        payload = await request.json()
    except Exception as exc:
        logger.warning("Apollo phone webhook: invalid JSON: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    summary = process_apollo_phone_webhook(db, payload)
    db.commit()
    logger.info("Apollo phone webhook processed: %s", summary)
    return {"ok": True, **summary}


@router.get("/vapi/status")
def vapi_webhook_status() -> dict[str, Any]:
    webhook_url = resolve_vapi_webhook_url()
    return {
        "voice_provider": settings.voice_provider,
        "vapi_configured": bool(settings.vapi_api_key and settings.vapi_phone_number_id),
        "webhook_configured": bool(webhook_url),
        "webhook_url_hint": _mask_webhook_url(webhook_url),
    }


@router.post("/vapi")
async def vapi_webhook(
    request: Request,
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Receive Vapi call status + end-of-call-report events."""
    _verify_webhook_token(token, settings.vapi_webhook_secret)

    try:
        payload = await request.json()
    except Exception as exc:
        logger.warning("Vapi webhook: invalid JSON: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    summary = process_vapi_webhook(db, payload)
    if summary.get("handled"):
        log_action(
            db,
            AuditAction.CALL_PLACED,
            entity_type="call",
            entity_id=summary.get("call_id"),
            summary=f"Vapi webhook: {summary.get('type')}",
            detail=summary,
        )
    db.commit()
    logger.info("Vapi webhook processed: %s", summary)
    return {"ok": True, **summary}


def _mask_webhook_url(url: str) -> str:
    if not url:
        return ""
    if "token=" in url:
        base, _, _rest = url.partition("token=")
        return f"{base}token=***"
    return url
