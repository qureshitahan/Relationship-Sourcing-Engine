"""Daily scheduler for the automated LinkedIn DM job (opt-in).

Separate from ``linkedin_scheduler`` (which only polls replies/acceptances) so
it can never affect the existing pollers. Fires ``run_linkedin_daily`` once per
weekday at ``settings.automation_linkedin_hour_utc``. Email campaigns are driven
by the existing agent scheduler; this thread only handles LinkedIn.

In-process daemon thread (like the other schedulers): only runs while the
backend is up, and does nothing unless ``automation_enabled`` is true.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from app.core.config import settings
from app.services.app_settings import get_setting, set_setting

logger = logging.getLogger(__name__)

# Check a few times an hour so we don't miss the target hour by much.
POLL_INTERVAL_SECONDS = 300
# Date guard (ISO date string) so the daily run fires at most once per day even
# across the 5-minute polls and process restarts.
_LAST_RUN_KEY = "automation_linkedin_last_run"

_thread: threading.Thread | None = None
_stop = threading.Event()


def _tick_once() -> None:
    now = datetime.utcnow()
    if now.hour != settings.automation_linkedin_hour_utc:
        return
    if now.weekday() >= 5:  # Mon-Fri only
        return
    today = now.date().isoformat()
    if get_setting(_LAST_RUN_KEY) == today:
        return
    # Claim the day BEFORE launching so a slow run can't be double-fired.
    set_setting(_LAST_RUN_KEY, today)
    from app.services.automation import run_linkedin_daily

    logger.info("Automation LinkedIn scheduler: launching daily run for %s", today)
    threading.Thread(
        target=run_linkedin_daily, name="automation-linkedin-run", daemon=True
    ).start()


def _loop() -> None:
    logger.info(
        "Automation LinkedIn scheduler started (poll every %ss)", POLL_INTERVAL_SECONDS
    )
    while not _stop.is_set():
        try:
            _tick_once()
        except Exception:  # noqa: BLE001 - keep the daemon alive no matter what
            logger.exception("Automation LinkedIn scheduler tick failed")
        _stop.wait(POLL_INTERVAL_SECONDS)


def start_automation_scheduler() -> None:
    """Start the LinkedIn automation thread once (idempotent). No-op unless the
    automation is enabled and a real LinkedIn provider is configured."""
    global _thread
    if not settings.automation_enabled:
        return
    if settings.linkedin_provider == "stub":
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(
        target=_loop, name="automation-linkedin-scheduler", daemon=True
    )
    _thread.start()


def stop_automation_scheduler() -> None:
    _stop.set()
