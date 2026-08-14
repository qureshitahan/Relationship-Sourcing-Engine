"""FastAPI application entrypoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.routes import api_router
from app.core.config import settings
from app.db.session import init_db
from app.services.agent_scheduler import start_agent_scheduler, stop_agent_scheduler
from app.services.automation_scheduler import (
    start_automation_scheduler,
    stop_automation_scheduler,
)
from app.services.email_scheduler import start_scheduler, stop_scheduler
from app.services.linkedin_scheduler import (
    start_linkedin_scheduler,
    stop_linkedin_scheduler,
)


class ServerErrorAsJSON:
    """Answer an uncaught exception with a JSON 500 from INSIDE the CORS layer.

    Starlette's own error handling sits *outside* ``CORSMiddleware``, so a crash
    produced a bare 500 carrying no ``Access-Control-Allow-Origin`` header. The
    browser then refuses to read it and reports "blocked by CORS policy" — which
    is not what went wrong, hides the real error from the console, and sends
    anyone debugging after a phantom CORS misconfiguration. (That is exactly how
    a foreign-key error on campaign delete stayed invisible.)

    Sitting inside CORS means the 500 gets the usual headers on the way out and
    the frontend can read ``detail``. Successful responses pass through
    untouched. The traceback is logged here because swallowing the exception
    stops the server from logging it — the Azure log stream keeps showing it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def _send(message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception:  # noqa: BLE001 - turn any crash into a readable 500
            logging.getLogger(__name__).exception(
                "Unhandled error in %s %s",
                scope.get("method", "?"),
                scope.get("path", "?"),
            )
            if started:
                # Headers are already on the wire; nothing can be changed now.
                raise
            await JSONResponse(
                {"detail": "Internal Server Error"}, status_code=500
            )(scope, receive, send)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Recruiting outreach automation platform (MVP).",
    )

    # Added BEFORE the CORS middleware on purpose: Starlette runs the most
    # recently added middleware outermost, so registering this first places it
    # inside CORSMiddleware — which is the only position where the 500 it returns
    # still picks up the CORS headers on its way back out.
    app.add_middleware(ServerErrorAsJSON)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def _startup() -> None:
        # MVP: create tables on startup. Replace with Alembic migrations later.
        init_db()
        # Daemon threads die on restart; clear any "running" rows left behind so
        # the UI does not show a forever-stuck run that blocks Continue.
        try:
            from app.db.session import SessionLocal
            from app.services.campaign_control import reap_orphaned_runs

            db = SessionLocal()
            try:
                n = reap_orphaned_runs(db)
                if n:
                    import logging

                    logging.getLogger(__name__).warning(
                        "Cleared %s orphaned agent run(s) after startup", n
                    )
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - never block boot on cleanup
            import logging

            logging.getLogger(__name__).exception("Failed to reap orphaned agent runs")
        # Sends scheduled outreach while the backend is running.
        start_scheduler()
        # Runs the autonomous daily outreach agent on schedule.
        start_agent_scheduler()
        # Auto-sends LinkedIn messages after invites are accepted + tracks replies.
        start_linkedin_scheduler()
        # Backend-only automated daily outreach (opt-in via AUTOMATION_ENABLED).
        # reconcile_automation runs unconditionally so the flag is a true master
        # switch: enabled => seed/enable the campaigns, disabled => turn any it
        # created back off. It only ever touches its own "[auto] " campaigns, so
        # existing campaigns are never affected.
        try:
            from app.db.session import SessionLocal
            from app.services.automation import reconcile_automation

            db = SessionLocal()
            try:
                reconcile_automation(db)
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - never block boot on automation setup
            import logging

            logging.getLogger(__name__).exception("Automation reconcile failed")
        # Daily LinkedIn DM job (no-op unless automation is enabled).
        start_automation_scheduler()
        # On-demand: fire ONE full paced run now (email drip + LinkedIn), on top
        # of the daily schedule. Set AUTOMATION_RUN_ON_STARTUP=true to kick a run
        # off immediately; unset it afterwards so a restart doesn't re-fire.
        if settings.automation_enabled and settings.automation_run_on_startup:
            # Guard: fire at most ONCE per UTC day even if the flag is left on and
            # Azure restarts the app — otherwise every restart stacks another full
            # run (duplicate invites, DB-pool pressure).
            from datetime import datetime as _dt

            from app.services.app_settings import get_setting, set_setting

            _today = _dt.utcnow().date().isoformat()
            if get_setting("automation_startup_run_date") != _today:
                set_setting("automation_startup_run_date", _today)
                import threading as _threading

                from app.services.automation import run_all_now

                _threading.Thread(
                    target=run_all_now, name="automation-run-now", daemon=True
                ).start()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        stop_scheduler()
        stop_agent_scheduler()
        stop_linkedin_scheduler()
        stop_automation_scheduler()

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "app": settings.app_name, "env": settings.environment}

    app.include_router(api_router)
    return app


app = create_app()
