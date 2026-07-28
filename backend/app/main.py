"""FastAPI application entrypoint."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.core.config import settings
from app.db.session import init_db
from app.services.agent_scheduler import start_agent_scheduler, stop_agent_scheduler
from app.services.email_scheduler import start_scheduler, stop_scheduler
from app.services.linkedin_scheduler import (
    start_linkedin_scheduler,
    stop_linkedin_scheduler,
)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Recruiting outreach automation platform (MVP).",
    )

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

    @app.on_event("shutdown")
    def _shutdown() -> None:
        stop_scheduler()
        stop_agent_scheduler()
        stop_linkedin_scheduler()

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "app": settings.app_name, "env": settings.environment}

    app.include_router(api_router)
    return app


app = create_app()
