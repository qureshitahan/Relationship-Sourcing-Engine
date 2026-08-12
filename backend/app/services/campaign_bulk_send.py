"""Server-side "Approve & send all" for one campaign's drafts.

The campaign panel used to run this loop inside the browser: two HTTP calls per
draft (approve, then send), so a 200-draft campaign meant ~400 round trips driven
from the tab. Closing the tab, sleeping the laptop or dropping the connection
stopped it partway — with no record of how far it got, and no way to cancel.

The work itself is unchanged. Same drafts, same order, and per draft exactly what
``POST /api/emails/{id}/status`` followed by ``POST /api/emails/{id}/send`` did,
down to ``approved_by`` and the audit entry, so an email approved here is
indistinguishable from one approved by hand. Two things differ, both only because
the loop now lives on this side of HTTP: ``perform_send`` receives a shared
``provider_cache`` (its documented use for bulk loops — one authenticated
connection for the batch instead of one per email), and there are no per-draft
round trips.

There is deliberately NO pacing delay. The browser loop had none and keeping
today's speed was a condition of moving it, so ``bulk_email_send_delay_seconds``
(which the Discover-page bulk send applies) is intentionally not used here. Wire
it in if the mailbox ever needs the protection.

Progress lives in the ``app_settings`` key/value table under
``campaign_bulk_send:<campaign_id>``. That needs no schema change, and it keeps
this job invisible to campaign status: an ``AgentRun`` row would make the
dashboard derive ``status = "running"`` (``agent/dashboard.py``), which blocks
"Run now" and rewrites the page — exactly the kind of side effect this must not
have.

A restart kills the worker, because it is a daemon thread. So the state records
which process owns a run, and a "running" record stamped by any other process is
reported as ``interrupted`` rather than looking live forever. Nothing is lost when
that happens: drafts that had not been sent are still drafts, so starting again
continues from where it stopped.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.email_draft import EmailDraft
from app.models.enums import AuditAction, EmailStatus
from app.services.app_settings import get_setting, set_setting

logger = logging.getLogger(__name__)

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUS_INTERRUPTED = "interrupted"

# Identifies THIS server process. A "running" record stamped by a different
# process belonged to a worker that a restart killed, so it is reported as
# interrupted instead of hanging on "running" forever.
_PROCESS_TOKEN = uuid.uuid4().hex

# Serializes check-then-start so two clicks cannot both clear the already-running
# guard and double-send to real recipients.
_START_LOCK = threading.Lock()

# The panel only shows the first few reasons, so keep the stored blob small.
_MAX_ERRORS = 10


class BulkSendError(Exception):
    """Raised when a bulk send cannot start. ``status_code`` shapes the HTTP error."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _key(campaign_id: int) -> str:
    return f"campaign_bulk_send:{campaign_id}"


def _read(campaign_id: int) -> Optional[dict]:
    raw = get_setting(_key(campaign_id))
    if not raw:
        return None
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):  # a hand-edited or truncated value
        return None
    return state if isinstance(state, dict) else None


def _write(campaign_id: int, state: dict) -> None:
    set_setting(_key(campaign_id), json.dumps(state))


def _patch(campaign_id: int, **fields) -> None:
    state = _read(campaign_id) or {}
    state.update(fields)
    _write(campaign_id, state)


def _finish(campaign_id: int, status: str, **fields) -> None:
    _patch(
        campaign_id,
        status=status,
        finished_at=datetime.utcnow().isoformat(),
        **fields,
    )


def _cancel_requested(campaign_id: int) -> bool:
    state = _read(campaign_id)
    return bool(state and state.get("cancel_requested"))


def state_for(campaign_id: int) -> Optional[dict]:
    """Current bulk-send state, with a run no live worker owns marked interrupted."""
    state = _read(campaign_id)
    if state is None:
        return None
    if state.get("status") == STATUS_RUNNING and state.get("process") != _PROCESS_TOKEN:
        state["status"] = STATUS_INTERRUPTED
        state["finished_at"] = datetime.utcnow().isoformat()
        state["error"] = (
            "The server restarted while this was sending. Nothing was lost — the "
            "emails that had not gone out are still drafts, so sending again "
            "continues from where it stopped."
        )
        _write(campaign_id, state)
    return state


def is_running(campaign_id: int) -> bool:
    state = state_for(campaign_id)
    return bool(state and state.get("status") == STATUS_RUNNING)


def pending_draft_count(db: Session, campaign_id: int) -> int:
    """Drafts of this campaign still awaiting approval — the job's work list."""
    return int(
        db.execute(
            select(func.count(EmailDraft.id)).where(
                EmailDraft.campaign_id == campaign_id,
                EmailDraft.status == EmailStatus.DRAFT,
            )
        ).scalar_one()
    )


def request_cancel(campaign_id: int) -> bool:
    """Ask a running bulk send to stop after the draft it is on. False if none."""
    state = state_for(campaign_id)
    if not state or state.get("status") != STATUS_RUNNING:
        return False
    state["cancel_requested"] = True
    _write(campaign_id, state)
    return True


def start(db: Session, campaign_id: int) -> dict:
    """Approve + send every draft of this campaign, in the background."""
    total = pending_draft_count(db, campaign_id)
    if total == 0:
        raise BulkSendError("No drafts are waiting for approval in this campaign.")
    with _START_LOCK:
        if is_running(campaign_id):
            raise BulkSendError(
                "A bulk send is already running for this campaign.", status_code=409
            )
        state = {
            "status": STATUS_RUNNING,
            "process": _PROCESS_TOKEN,
            "total": total,
            "done": 0,
            "sent": 0,
            "failed": 0,
            "errors": [],
            "cancel_requested": False,
            "started_at": datetime.utcnow().isoformat(),
            "finished_at": None,
            "error": None,
        }
        _write(campaign_id, state)
        threading.Thread(
            target=_worker,
            args=(campaign_id,),
            name=f"campaign-bulk-send-{campaign_id}",
            daemon=True,
        ).start()
    return state


def _who(db: Session, draft: EmailDraft) -> str:
    """Recipient label for a failure line — looked up only when one fails."""
    from app.models.contact import Contact

    if draft.contact_id:
        contact = db.get(Contact, draft.contact_id)
        if contact and contact.name:
            return contact.name
    return f"#{draft.id}"


def _worker(campaign_id: int) -> None:
    # Imported here, not at module scope: routes import services, so the reverse
    # at import time would be a cycle.
    from app.api.routes.emails import SendError, perform_send
    from app.services.audit import log_action

    db = SessionLocal()
    # Reused across every draft in this batch (see perform_send's provider_cache
    # param) — for Gmail this keeps one authenticated SMTP connection open for the
    # whole batch instead of reconnecting per email.
    provider_cache: dict = {}
    done = sent = failed = 0
    errors: list[str] = []
    try:
        drafts = list(
            db.execute(
                select(EmailDraft)
                .where(
                    EmailDraft.campaign_id == campaign_id,
                    EmailDraft.status == EmailStatus.DRAFT,
                )
                .order_by(EmailDraft.id)
            ).scalars().all()
        )
        _patch(campaign_id, total=len(drafts))
        for draft in drafts:
            if _cancel_requested(campaign_id):
                _finish(
                    campaign_id,
                    STATUS_CANCELLED,
                    done=done,
                    sent=sent,
                    failed=failed,
                    errors=errors,
                )
                return
            try:
                # Exactly what POST /api/emails/{id}/status does for "approved":
                # same fields, same actor, same audit entry.
                draft.status = EmailStatus.APPROVED
                draft.approved_by = "user"
                draft.approved_at = datetime.utcnow()
                log_action(
                    db,
                    AuditAction.EMAIL_APPROVAL,
                    entity_type="email_draft",
                    entity_id=draft.id,
                    actor="user",
                    summary=f"Email status -> {EmailStatus.APPROVED}",
                )
                db.commit()
                perform_send(db, draft, provider_cache=provider_cache)
                sent += 1
            except SendError as exc:
                db.rollback()
                failed += 1
                if len(errors) < _MAX_ERRORS:
                    errors.append(f"{_who(db, draft)}: {exc.message}")
            except Exception as exc:  # noqa: BLE001 - keep sending the rest
                db.rollback()
                logger.exception(
                    "Campaign %s bulk send failed for draft %s", campaign_id, draft.id
                )
                failed += 1
                if len(errors) < _MAX_ERRORS:
                    errors.append(f"{_who(db, draft)}: {exc}")
            done += 1
            _patch(campaign_id, done=done, sent=sent, failed=failed, errors=errors)
        _finish(
            campaign_id, STATUS_DONE, done=done, sent=sent, failed=failed, errors=errors
        )
    except Exception as exc:  # noqa: BLE001 - the state must never be left "running"
        logger.exception("Campaign %s bulk send worker failed", campaign_id)
        _finish(
            campaign_id,
            STATUS_DONE,
            done=done,
            sent=sent,
            failed=failed,
            errors=errors,
            error=str(exc),
        )
    finally:
        for provider in provider_cache.values():
            try:
                provider.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.warning(
                    "Failed to close email provider connection", exc_info=True
                )
        db.close()
