"""Live progress and manual stop for the LinkedIn bulk approve+send job.

The job is a detached background thread that paces sends ~20s apart, so a run
of any size lives for many minutes. State is kept in the AppSetting store rather
than in module memory because the request asking it to stop is served by a
different thread — and, under a multi-worker deployment, a different process —
than the one doing the sending.

State is scoped PER SENDING ACCOUNT. It used to be one shared record, which meant
two operators sending from two accounts at once overwrote each other's progress
numbers, and either one's Stop halted both batches. Each account now owns its own
record, so stopping Taha's send leaves Usama's running. Callers that pass no
account fall back to the original shared key, keeping older behaviour intact.
"""
from __future__ import annotations

import json
import time
from typing import Optional

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


def key_for(account_id: Optional[str] = None) -> str:
    """The AppSetting key holding one account's send state.

    No account => the original shared key, so a caller that never learned about
    accounts keeps reading and writing exactly what it used to.
    """
    account = (account_id or "").strip()
    return f"{PROGRESS_KEY}:{account}" if account else PROGRESS_KEY


def read_progress(account_id: Optional[str] = None) -> dict:
    raw = get_setting(key_for(account_id))
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return {**_IDLE, **data}
        except (ValueError, TypeError):
            pass
    return dict(_IDLE)


def write_progress(account_id: Optional[str] = None, **changes) -> dict:
    state = {**read_progress(account_id), **changes}
    set_setting(key_for(account_id), json.dumps(state))
    return state


def start(total: int, account_id: Optional[str] = None) -> None:
    """Open a fresh record for this account. Clears any stop request left by a
    previous run, so an old Stop click can never halt the next send before it
    begins."""
    set_setting(
        key_for(account_id),
        json.dumps(
            {
                "status": STATUS_RUNNING,
                "total": total,
                "done": 0,
                "sent": 0,
                "failed": 0,
                "stop_requested": False,
                "account_id": (account_id or "").strip() or None,
            }
        ),
    )


def request_stop(account_id: Optional[str] = None) -> bool:
    """Ask THIS account's running job to stop. False when it has none running.

    Deliberately scoped: another account's batch must keep going.
    """
    if read_progress(account_id).get("status") != STATUS_RUNNING:
        return False
    write_progress(account_id, stop_requested=True)
    return True


def stop_requested(account_id: Optional[str] = None) -> bool:
    return bool(read_progress(account_id).get("stop_requested"))


def finish(*, stopped: bool, account_id: Optional[str] = None) -> None:
    write_progress(
        account_id,
        status=STATUS_STOPPED if stopped else STATUS_DONE,
        stop_requested=False,
    )


def sleep_unless_stopped(
    seconds: float, poll: float = 2.0, account_id: Optional[str] = None
) -> bool:
    """Pace the next send, but stay responsive to Stop.

    Sleeping the full gap in one call would leave Stop with no effect until the
    delay elapsed. Returns True if a stop was requested during the wait.
    """
    waited = 0.0
    while waited < seconds:
        if stop_requested(account_id):
            return True
        chunk = min(poll, seconds - waited)
        time.sleep(chunk)
        waited += chunk
    return stop_requested(account_id)
