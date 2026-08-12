"""Live progress and manual stop for the LinkedIn bulk approve+send job.

The job is a detached background thread that paces sends ~20s apart, so a run
of any size lives for many minutes. State is kept in the AppSetting store rather
than in module memory because the request asking it to stop is served by a
different thread — and, under a multi-worker deployment, a different process —
than the one doing the sending.
"""
from __future__ import annotations

import json
import time

from app.services.app_settings import get_setting, set_setting

PROGRESS_KEY = "linkedin_bulk_send_progress"

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_STOPPED = "stopped"

_IDLE: dict = {
    "status": STATUS_IDLE,
    "total": 0,
    "done": 0,
    "sent": 0,
    "failed": 0,
    "stop_requested": False,
}


def read_progress() -> dict:
    raw = get_setting(PROGRESS_KEY)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return {**_IDLE, **data}
        except (ValueError, TypeError):
            pass
    return dict(_IDLE)


def write_progress(**changes) -> dict:
    state = {**read_progress(), **changes}
    set_setting(PROGRESS_KEY, json.dumps(state))
    return state


def start(total: int) -> None:
    """Open a fresh record. Clears any stop request left by a previous run, so
    an old Stop click can never halt the next send before it begins."""
    set_setting(
        PROGRESS_KEY,
        json.dumps(
            {
                "status": STATUS_RUNNING,
                "total": total,
                "done": 0,
                "sent": 0,
                "failed": 0,
                "stop_requested": False,
            }
        ),
    )


def request_stop() -> bool:
    """Ask a running job to stop. False when nothing is running to stop."""
    if read_progress().get("status") != STATUS_RUNNING:
        return False
    write_progress(stop_requested=True)
    return True


def stop_requested() -> bool:
    return bool(read_progress().get("stop_requested"))


def finish(*, stopped: bool) -> None:
    write_progress(
        status=STATUS_STOPPED if stopped else STATUS_DONE, stop_requested=False
    )


def sleep_unless_stopped(seconds: float, poll: float = 2.0) -> bool:
    """Pace the next send, but stay responsive to Stop.

    Sleeping the full gap in one call would leave Stop with no effect until the
    delay elapsed. Returns True if a stop was requested during the wait.
    """
    waited = 0.0
    while waited < seconds:
        if stop_requested():
            return True
        chunk = min(poll, seconds - waited)
        time.sleep(chunk)
        waited += chunk
    return stop_requested()
