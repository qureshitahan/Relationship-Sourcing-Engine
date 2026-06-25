"""Call queue: script generation, approval, Vapi placement, status updates."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import resolve_vapi_webhook_url, settings
from app.db.session import get_db
from app.models.call import Call
from app.models.company import Company
from app.models.contact import Contact
from app.models.enums import AuditAction, CallStatus
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.schemas.entities import CallOut, Page
from app.schemas.requests import CallGenerateRequest, CallStatusRequest
from app.services.audit import log_action
from app.services.voice import generate_call_script
from app.services.voice_providers import CallPlacementContext, get_voice_provider

router = APIRouter(prefix="/calls", tags=["calls"])


def _call_out(db: Session, call: Call) -> CallOut:
    contact = db.get(Contact, call.contact_id) if call.contact_id else None
    company = db.get(Company, call.company_id) if call.company_id else None
    principal = db.get(Principal, call.principal_id) if call.principal_id else None
    return CallOut(
        id=call.id,
        principal_id=call.principal_id,
        company_id=call.company_id,
        contact_id=call.contact_id,
        insight_id=call.insight_id,
        phone_number=call.phone_number,
        script=call.script,
        status=call.status,
        transcript=call.transcript,
        outcome_notes=call.outcome_notes,
        human_handoff_needed=call.human_handoff_needed,
        meeting_requested=call.meeting_requested,
        provider=call.provider,
        provider_call_id=call.provider_call_id,
        placed_at=call.placed_at,
        created_at=call.created_at,
        principal_name=principal.name if principal else None,
        contact_name=contact.name if contact else None,
        contact_title=contact.title if contact else None,
        company_name=company.name if company else None,
    )


@router.get("/config", response_model=dict)
def call_config() -> dict:
    """Show whether voice calling is configured (for the Call Queue UI)."""
    return {
        "voice_provider": settings.voice_provider,
        "vapi_configured": bool(
            settings.vapi_api_key and settings.vapi_phone_number_id
        ),
        "webhook_configured": bool(resolve_vapi_webhook_url()),
        "webhook_url_hint": _mask_url(resolve_vapi_webhook_url()),
    }


@router.get("", response_model=Page[CallOut])
def list_calls(
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    query = select(Call)
    count_query = select(func.count()).select_from(Call)
    if status:
        query = query.where(Call.status == status)
        count_query = count_query.where(Call.status == status)
    query = query.order_by(Call.created_at.desc()).limit(limit).offset(offset)
    items = db.execute(query).scalars().all()
    total = db.execute(count_query).scalar_one()
    return Page[CallOut](
        items=[_call_out(db, c) for c in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/generate", response_model=CallOut, status_code=201)
def generate_call(payload: CallGenerateRequest, db: Session = Depends(get_db)):
    """Generate a call script and queue the call (status=queued, unapproved)."""
    principal = db.get(Principal, payload.principal_id)
    contact = db.get(Contact, payload.contact_id)
    if not principal or not contact:
        raise HTTPException(status_code=404, detail="Principal or prospect not found")

    company = db.get(Company, contact.company_id) if contact.company_id else None
    insight = db.get(RelevanceInsight, payload.insight_id) if payload.insight_id else None
    if insight is None:
        insight = db.execute(
            select(RelevanceInsight).where(
                RelevanceInsight.principal_id == principal.id,
                RelevanceInsight.contact_id == contact.id,
            )
        ).scalar_one_or_none()

    script = generate_call_script(principal, company, contact, insight)
    call = Call(
        principal_id=principal.id,
        company_id=contact.company_id,
        contact_id=contact.id,
        insight_id=insight.id if insight else None,
        phone_number=contact.phone or (company.phone if company else None),
        script=script,
        status=CallStatus.QUEUED,
    )
    db.add(call)
    db.flush()
    log_action(
        db,
        AuditAction.CALL_SCRIPT,
        entity_type="call",
        entity_id=call.id,
        summary=f"Generated call script for {contact.name} on behalf of {principal.name}",
    )
    db.commit()
    db.refresh(call)
    return _call_out(db, call)


@router.post("/{call_id}/status", response_model=CallOut)
def update_call_status(call_id: int, payload: CallStatusRequest, db: Session = Depends(get_db)):
    """Approve a call or record its outcome (interested, handoff, meeting, etc.)."""
    call = db.get(Call, call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    call.status = payload.status
    if payload.transcript is not None:
        call.transcript = payload.transcript
    if payload.outcome_notes is not None:
        call.outcome_notes = payload.outcome_notes
    if payload.human_handoff_needed is not None:
        call.human_handoff_needed = payload.human_handoff_needed
    if payload.meeting_requested is not None:
        call.meeting_requested = payload.meeting_requested
    if payload.status == CallStatus.APPROVED:
        call.approved_by = payload.approved_by
        call.approved_at = datetime.utcnow()

    action = (
        AuditAction.CALL_APPROVAL
        if payload.status == CallStatus.APPROVED
        else AuditAction.CALL_PLACED
    )
    log_action(
        db,
        action,
        entity_type="call",
        entity_id=call.id,
        actor=payload.approved_by or "user",
        summary=f"Call status -> {payload.status}",
    )
    db.commit()
    db.refresh(call)
    return _call_out(db, call)


@router.post("/{call_id}/place", response_model=CallOut)
def place_call(call_id: int, db: Session = Depends(get_db)):
    """Place an approved outbound call via Vapi. Requires revealed phone number."""
    call = db.get(Call, call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    if call.status != CallStatus.APPROVED:
        raise HTTPException(
            status_code=400,
            detail="Call must be approved before placing. Current status: "
            f"{call.status}",
        )
    if not call.phone_number:
        raise HTTPException(
            status_code=400,
            detail="No phone number on file. Reveal the prospect's phone first.",
        )
    if call.provider_call_id and call.status in (CallStatus.DIALING, CallStatus.COMPLETED):
        raise HTTPException(status_code=400, detail="Call already placed.")

    principal = db.get(Principal, call.principal_id) if call.principal_id else None
    contact = db.get(Contact, call.contact_id) if call.contact_id else None
    company = db.get(Company, call.company_id) if call.company_id else None
    insight = db.get(RelevanceInsight, call.insight_id) if call.insight_id else None

    if not principal or not contact:
        raise HTTPException(status_code=400, detail="Call is missing principal or prospect.")

    provider = get_voice_provider()
    ctx = CallPlacementContext(
        call_id=call.id,
        to_number=call.phone_number,
        script=call.script or "",
        principal_name=principal.name,
        prospect_name=contact.name,
        prospect_title=contact.title,
        company_name=company.name if company else None,
        insight_snapshot=insight.snapshot if insight else None,
        talking_points=list(insight.talking_points or []) if insight else [],
        metadata={
            "principal_id": principal.id,
            "contact_id": contact.id,
            "principal": principal,
            "contact": contact,
            "company": company,
            "insight": insight,
        },
    )
    result = provider.place_call(ctx=ctx)
    if not result.placed:
        call.status = CallStatus.FAILED
        call.outcome_notes = result.error
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=result.error or "Voice provider failed to place call",
        )

    call.status = CallStatus.DIALING
    call.provider = result.provider
    call.provider_call_id = result.provider_call_id
    call.placed_at = datetime.utcnow()

    log_action(
        db,
        AuditAction.CALL_PLACED,
        entity_type="call",
        entity_id=call.id,
        summary=f"Placed call to {contact.name} via {result.provider}",
        detail={"provider_call_id": result.provider_call_id},
    )
    db.commit()
    db.refresh(call)
    return _call_out(db, call)


def _mask_url(url: str) -> str:
    if not url:
        return ""
    if "token=" in url:
        base, _, _ = url.partition("token=")
        return f"{base}token=***"
    return url
