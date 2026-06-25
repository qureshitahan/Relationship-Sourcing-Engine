"""Background scheduler that sends due scheduled email drafts.

This is an in-process daemon thread: it only sends while the backend is
running. That makes it reliable on an always-on deployed server, but on a
laptop it won't fire if the machine sleeps or the process is stopped. Drafts
stay SCHEDULED until the process is up again, then send on the next tick.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.email_draft import EmailDraft
from app.models.enums import EmailStatus

logger = logging.getLogger(__name__)

# How often to check for due emails (seconds). Minute resolution is plenty for
# outreach scheduling and keeps Graph API traffic minimal.
POLL_INTERVAL_SECONDS = 60
# Poll the mailbox for replies less often than scheduled sends (Graph quota).
REPLY_POLL_INTERVAL_SECONDS = 600

_thread: threading.Thread | None = None
_stop = threading.Event()
_last_reply_poll: datetime | None = None


def _poll_replies_once() -> None:
    """Automatically detect replies to sent outreach (Microsoft Graph)."""
    from app.api.routes.emails import scan_replies

    db = SessionLocal()
    try:
        result = scan_replies(db)
        if result.get("replied"):
            logger.info("Reply poller: %s new repl(y/ies) detected", result["replied"])
    finally:
        db.close()


def _send_due_once() -> None:
    """Find and send any scheduled drafts whose time has arrived."""
    # Import here to avoid a circular import (routes import db at module load).
    from app.api.routes.emails import SendError, perform_send

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        due = db.execute(
            select(EmailDraft).where(
                EmailDraft.status == EmailStatus.SCHEDULED,
                EmailDraft.scheduled_at.isnot(None),
                EmailDraft.scheduled_at <= now,
            )
        ).scalars().all()
        for draft in due:
            # Outlook-deferred sends are delivered server-side; we only sync the
            # local status to SENT once the scheduled time has passed (no resend).
            if draft.outlook_scheduled:
                draft.status = EmailStatus.SENT
                if not draft.sent_at:
                    draft.sent_at = draft.scheduled_at or now
                db.commit()
                logger.info("Outlook-scheduled email %s marked sent", draft.id)
                continue
            try:
                perform_send(db, draft)
                logger.info("Scheduled email %s sent", draft.id)
            except SendError as exc:
                # Stop retrying every tick: revert to APPROVED so the user can
                # see it didn't go out and re-send/re-schedule manually.
                logger.warning(
                    "Scheduled email %s failed to send: %s", draft.id, exc.message
                )
                db.rollback()
                draft.status = EmailStatus.APPROVED
                draft.scheduled_at = None
                db.commit()
            except Exception:  # noqa: BLE001 - never let one bad draft kill the loop
                logger.exception("Unexpected error sending scheduled email %s", draft.id)
                db.rollback()
    finally:
        db.close()


def _loop() -> None:
    global _last_reply_poll
    logger.info("Email scheduler started (poll every %ss)", POLL_INTERVAL_SECONDS)
    while not _stop.is_set():
        try:
            _send_due_once()
        except Exception:  # noqa: BLE001 - keep the daemon alive no matter what
            logger.exception("Email scheduler tick failed")
        try:
            now = datetime.utcnow()
            if (
                _last_reply_poll is None
                or (now - _last_reply_poll).total_seconds() >= REPLY_POLL_INTERVAL_SECONDS
            ):
                _poll_replies_once()
                _last_reply_poll = now
        except Exception:  # noqa: BLE001 - keep the daemon alive no matter what
            logger.exception("Reply poller tick failed")
        _stop.wait(POLL_INTERVAL_SECONDS)


def start_scheduler() -> None:
    """Start the background scheduler thread once (idempotent)."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="email-scheduler", daemon=True)
    _thread.start()


def stop_scheduler() -> None:
    _stop.set()
