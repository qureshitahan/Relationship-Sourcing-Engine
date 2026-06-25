"""Process inbound Vapi server URL webhooks (call status + transcripts)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.call import Call
from app.models.enums import AuditAction, CallStatus

logger = logging.getLogger(__name__)

_ENDED_REASON_STATUS = {
    "no-answer": CallStatus.NO_ANSWER,
    "no answer": CallStatus.NO_ANSWER,
    "voicemail": CallStatus.NO_ANSWER,
    "busy": CallStatus.NO_ANSWER,
    "failed": CallStatus.FAILED,
    "error": CallStatus.FAILED,
}


def _extract_message(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message")
    if isinstance(message, dict):
        return message
    return payload


def _find_call(db: Session, message: dict[str, Any]) -> Optional[Call]:
    call_obj = message.get("call") if isinstance(message.get("call"), dict) else {}
    provider_id = call_obj.get("id")
    metadata = call_obj.get("metadata") if isinstance(call_obj.get("metadata"), dict) else {}

    internal_id = metadata.get("relationship_call_id")
    if internal_id:
        try:
            row = db.get(Call, int(internal_id))
            if row:
                return row
        except (TypeError, ValueError):
            pass

    if provider_id:
        return db.execute(
            select(Call).where(Call.provider_call_id == str(provider_id))
        ).scalar_one_or_none()
    return None


def _transcript_from_message(message: dict[str, Any]) -> Optional[str]:
    artifact = message.get("artifact")
    if isinstance(artifact, dict):
        transcript = artifact.get("transcript")
        if transcript:
            return str(transcript)
        messages = artifact.get("messages")
        if isinstance(messages, list):
            lines = []
            for turn in messages:
                if not isinstance(turn, dict):
                    continue
                role = turn.get("role") or "unknown"
                text = turn.get("message") or turn.get("content") or ""
                if text:
                    lines.append(f"{role}: {text}")
            if lines:
                return "\n".join(lines)
    return None


def process_vapi_webhook(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Update Call rows from Vapi end-of-call-report / status-update events."""
    message = _extract_message(payload)
    msg_type = message.get("type") or payload.get("type")
    if not msg_type:
        return {"handled": False, "reason": "missing message type"}

    call = _find_call(db, message)
    if call is None:
        logger.info("Vapi webhook %s: no matching call", msg_type)
        return {"handled": False, "reason": "call not found", "type": msg_type}

    updated: dict[str, Any] = {"call_id": call.id, "type": msg_type}

    if msg_type == "status-update":
        status = (message.get("status") or "").lower()
        if status == "in-progress" and call.status == CallStatus.APPROVED:
            call.status = CallStatus.DIALING
            updated["status"] = call.status
        elif status == "ringing" and call.status in (CallStatus.APPROVED, CallStatus.QUEUED):
            call.status = CallStatus.DIALING
            updated["status"] = call.status

    elif msg_type == "end-of-call-report":
        ended_reason = (message.get("endedReason") or "").lower()
        transcript = _transcript_from_message(message)
        if transcript:
            call.transcript = transcript
            updated["transcript_saved"] = True

        call.status = _ENDED_REASON_STATUS.get(ended_reason, CallStatus.COMPLETED)
        call.outcome_notes = ended_reason or call.outcome_notes
        updated["status"] = call.status
        updated["ended_reason"] = ended_reason

        blob = (transcript or "").lower()
        if any(w in blob for w in ("interested", "follow up", "follow-up", "schedule")):
            call.meeting_requested = True
            updated["meeting_requested"] = True
        if any(w in blob for w in ("speak to", "talk to", "human", "real person", "transfer")):
            call.human_handoff_needed = True
            updated["human_handoff_needed"] = True

    else:
        return {"handled": False, "reason": "ignored event type", "type": msg_type}

    db.flush()
    return {"handled": True, **updated}
