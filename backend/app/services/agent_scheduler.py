"""Background scheduler that fires the autonomous agent once per day.

In-process daemon thread (like the email scheduler): it only runs while the
backend is up. Each enabled :class:`AgentConfig` runs once on the day its
``run_hour_utc`` arrives; ``last_run_at`` guards against double-runs.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.agent_config import AgentConfig

logger = logging.getLogger(__name__)

# Check a few times an hour so we don't miss the target hour by much.
POLL_INTERVAL_SECONDS = 300

_thread: threading.Thread | None = None
_stop = threading.Event()


def _tick_once() -> None:
    from app.services.agent.orchestrator import create_run, execute_run

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        configs = db.execute(
            select(AgentConfig).where(AgentConfig.enabled.is_(True))
        ).scalars().all()
        for config in configs:
            if now.hour != config.run_hour_utc:
                continue
            if config.last_run_at and config.last_run_at.date() == now.date():
                continue
            if not config.playbook_id:
                logger.info(
                    "Agent scheduler skipping campaign %s (principal %s) — no playbook",
                    config.id,
                    config.principal_id,
                )
                continue
            run = create_run(
                db,
                config.principal_id,
                "scheduled",
                playbook_id=config.playbook_id,
                campaign_id=config.id,
            )
            logger.info(
                "Agent scheduler launching run %s for campaign %s (principal %s)",
                run.id,
                config.id,
                config.principal_id,
            )
            # Run in a background thread so long runs don't block HTTP or reload.
            threading.Thread(
                target=execute_run,
                args=(run.id,),
                name=f"agent-run-{run.id}",
                daemon=True,
            ).start()
    finally:
        db.close()


def _loop() -> None:
    logger.info("Agent scheduler started (poll every %ss)", POLL_INTERVAL_SECONDS)
    while not _stop.is_set():
        try:
            _tick_once()
        except Exception:  # noqa: BLE001 - keep the daemon alive no matter what
            logger.exception("Agent scheduler tick failed")
        _stop.wait(POLL_INTERVAL_SECONDS)


def start_agent_scheduler() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="agent-scheduler", daemon=True)
    _thread.start()


def stop_agent_scheduler() -> None:
    _stop.set()
