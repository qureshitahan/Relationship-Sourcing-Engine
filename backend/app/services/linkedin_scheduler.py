"""Background poller for LinkedIn outreach.

Periodically: (1) auto-sends the queued message once a connection invitation is
accepted, and (2) detects replies to sent messages. In-process daemon thread,
identical in spirit to the email scheduler; isolated so it can never affect the
email/agent schedulers.
"""
from __future__ import annotations

import logging
import threading

from app.core.config import settings
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# LinkedIn state changes slowly (invites accepted over hours/days); poll gently
# to respect Unipile/LinkedIn rate limits.
POLL_INTERVAL_SECONDS = 900  # 15 minutes

_thread: threading.Thread | None = None
_stop = threading.Event()


def _poll_once() -> None:
    from app.api.routes.linkedin import scan_linkedin_updates

    db = SessionLocal()
    try:
        result = scan_linkedin_updates(db)
        if result.get("accepted") or result.get("replied"):
            logger.info(
                "LinkedIn poller: %s accepted->sent, %s new repl(y/ies)",
                result.get("accepted", 0),
                result.get("replied", 0),
            )
    except Exception:  # noqa: BLE001 - keep the daemon alive no matter what
        logger.exception("LinkedIn poller tick failed")
    finally:
        db.close()


def _loop() -> None:
    logger.info("LinkedIn scheduler started (poll every %ss)", POLL_INTERVAL_SECONDS)
    while not _stop.is_set():
        _poll_once()
        _stop.wait(POLL_INTERVAL_SECONDS)


def start_linkedin_scheduler() -> None:
    """Start the poller once (idempotent). No-op unless a real provider is set."""
    global _thread
    if settings.linkedin_provider == "stub":
        # Nothing to poll without a real account; stay dormant.
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="linkedin-scheduler", daemon=True)
    _thread.start()


def stop_linkedin_scheduler() -> None:
    _stop.set()
