"""FastAPI application entrypoint."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.core.config import settings
from app.db.session import init_db
from app.services.agent_scheduler import start_agent_scheduler, stop_agent_scheduler
from app.services.email_scheduler import start_scheduler, stop_scheduler


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
        # Sends scheduled outreach while the backend is running.
        start_scheduler()
        # Runs the autonomous daily outreach agent on schedule.
        start_agent_scheduler()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        stop_scheduler()
        stop_agent_scheduler()

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "app": settings.app_name, "env": settings.environment}

    app.include_router(api_router)
    return app


app = create_app()
