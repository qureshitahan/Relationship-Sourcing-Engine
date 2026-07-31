"""Database engine, session factory, and FastAPI dependency."""
from __future__ import annotations

import os
from typing import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# SQLite needs check_same_thread=False for FastAPI, plus a busy timeout so
# concurrent requests (e.g. discovery + UI reads) don't fail with "database is locked".
connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False, "timeout": 30}
    # Ensure the directory for the SQLite file exists.
    db_path = settings.database_url.replace("sqlite:///", "")
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

engine = create_engine(settings.database_url, connect_args=connect_args, future=True)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """WAL mode + busy_timeout reduce 'database is locked' under concurrent load."""
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. For MVP we use create_all; swap to Alembic later."""
    # Import models so they register on the metadata before create_all.
    from app import models  # noqa: F401

    Base = models.Base
    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()


# Minimal additive migrations for SQLite (create_all won't add columns to
# pre-existing tables). Each entry: table -> {column: column DDL type}.
# The relationship-sourcing schema is created fresh, so this is intentionally
# empty; add entries here when evolving an existing deployment.
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "principals": {
        "linkedin_url": "VARCHAR(512)",
        "phone": "VARCHAR(64)",
        "email_signature": "TEXT",
        "objective": "TEXT",
        "document_focus": "TEXT",
        "mailbox_daily_cap": "INTEGER DEFAULT 50",
        "outreach_mailbox_id": "VARCHAR(64)",
    },
    "contacts": {
        "location": "VARCHAR(255)",
        "has_email": "BOOLEAN DEFAULT 0",
        "variant_id": "INTEGER",
        "phone_reveal_status": "VARCHAR(32)",
        "external_id": "VARCHAR(255)",
        "campaign_id": "INTEGER",
        "bulk_campaign_id": "INTEGER",
        "notes": "TEXT",
    },
    "relevance_insights": {
        "snapshot": "TEXT",
        "key_facts": "JSON",
        "sources": "JSON",
        "identity_verified": "BOOLEAN DEFAULT 1",
        "identity_warnings": "JSON",
    },
    "principal_documents": {
        "relevance_score": "REAL",
        "relevance_note": "TEXT",
    },
    "email_drafts": {
        "from_mailbox": "VARCHAR(64)",
        "conversation_id": "VARCHAR(255)",
        "internet_message_id": "VARCHAR(512)",
        "replied_at": "DATETIME",
        "reply_snippet": "TEXT",
        "reply_body": "TEXT",
        "last_reply_check_at": "DATETIME",
        "scheduled_at": "DATETIME",
        "outlook_scheduled": "BOOLEAN DEFAULT 0",
        "open_count": "INTEGER DEFAULT 0",
        "first_opened_at": "DATETIME",
        "last_opened_at": "DATETIME",
        "approved_at": "DATETIME",
        # Campaign scoping + A/B copy / send-time learning.
        "campaign_id": "INTEGER",
        "bulk_campaign_id": "INTEGER",
        "copy_variant_id": "INTEGER",
        "send_bucket_index": "INTEGER",
    },
    "agent_configs": {
        "name": "VARCHAR(255)",
        "weekdays_only": "BOOLEAN DEFAULT 0",
        "paused": "BOOLEAN DEFAULT 0",
        "playbook_id": "INTEGER",
        "mode": "VARCHAR(20) DEFAULT 'research'",
        "sanity_min": "REAL DEFAULT 20",
        "draft_batch_size": "INTEGER DEFAULT 8",
        "daily_send_cap": "INTEGER DEFAULT 50",
        "followup_schedule_days": "JSON",
        "timezone": "VARCHAR(64) DEFAULT 'America/New_York'",
        "auto_schedule": "BOOLEAN DEFAULT 1",
        "run_hour_local": "INTEGER DEFAULT 9",
        "send_window_start_local": "INTEGER DEFAULT 9",
        "send_window_end_local": "INTEGER DEFAULT 17",
        "digest_recipients": "JSON",
    },
    "agent_runs": {
        "playbook_id": "INTEGER",
        "variant_id": "INTEGER",
        "campaign_id": "INTEGER",
    },
    "discovery_runs": {
        "job_kind": "VARCHAR(30)",
        "job_status": "VARCHAR(20)",
        "job_total": "INTEGER",
        "job_done": "INTEGER",
        "job_error": "TEXT",
        "job_cancel_requested": "BOOLEAN DEFAULT 0",
    },
    "linkedin_messages": {
        # Which connected LinkedIn account (Unipile account_id) sent this, so
        # reply/invite tracking polls with the right account when several are used.
        "from_account": "VARCHAR(64)",
    },
}


def _drop_agent_configs_principal_unique() -> None:
    """Drop the legacy UNIQUE(principal_id) on agent_configs (SQLite rebuild).

    The column used to be unique=True (one campaign per principal). SQLite bakes
    that into the table via an auto-index that can't be dropped in place, so we
    rebuild the table from the current model when the old constraint is present.
    """
    inspector = inspect(engine)
    if "agent_configs" not in inspector.get_table_names():
        return

    indexes = inspector.get_indexes("agent_configs")
    try:
        uniques = inspector.get_unique_constraints("agent_configs")
    except Exception:  # noqa: BLE001
        uniques = []
    has_unique = any(
        ix.get("unique") and ix.get("column_names") == ["principal_id"] for ix in indexes
    ) or any(uc.get("column_names") == ["principal_id"] for uc in uniques)
    if not has_unique:
        return

    from app import models  # local import to avoid cycles

    old_cols = [c["name"] for c in inspector.get_columns("agent_configs")]
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS agent_configs_old")
        conn.exec_driver_sql("ALTER TABLE agent_configs RENAME TO agent_configs_old")
        # Named indexes follow the rename and keep their names; drop them so the
        # fresh table can recreate identically-named indexes without collision.
        leftover_idx = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='agent_configs_old' AND name NOT LIKE 'sqlite_autoindex%'"
        ).fetchall()
        for (idx_name,) in leftover_idx:
            conn.exec_driver_sql(f"DROP INDEX IF EXISTS {idx_name}")

    # Recreate agent_configs fresh from the model (no unique on principal_id).
    models.Base.metadata.tables["agent_configs"].create(bind=engine)

    new_cols = [c["name"] for c in inspect(engine).get_columns("agent_configs")]
    shared = [c for c in old_cols if c in new_cols]
    col_list = ", ".join(shared)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"INSERT INTO agent_configs ({col_list}) SELECT {col_list} FROM agent_configs_old"
        )
        conn.exec_driver_sql("DROP TABLE agent_configs_old")


def _apply_lightweight_migrations() -> None:
    """Add any missing columns to existing tables (idempotent, SQLite-safe)."""
    _drop_agent_configs_principal_unique()
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDITIVE_COLUMNS.items():
            if table not in existing_tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table)}
            for column, ddl_type in columns.items():
                if column not in present:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl_type}'))
    _backfill_campaigns()
    _backfill_contact_campaign_ids()


def _backfill_contact_campaign_ids() -> None:
    """Stamp campaign_id on contacts from their discovery run's agent run."""
    inspector = inspect(engine)
    if "contacts" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("contacts")}
    if "campaign_id" not in cols or "agent_runs" not in inspector.get_table_names():
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE contacts
                SET campaign_id = (
                    SELECT ar.campaign_id FROM agent_runs ar
                    WHERE ar.discovery_run_id = contacts.discovery_run_id
                      AND ar.campaign_id IS NOT NULL
                    ORDER BY ar.id DESC
                    LIMIT 1
                )
                WHERE campaign_id IS NULL AND discovery_run_id IS NOT NULL
                """
            )
        )


def _backfill_campaigns() -> None:
    """One-time, idempotent backfill so existing data becomes campaign-scoped.

    - Name any unnamed campaign (AgentConfig) after its active playbook.
    - Stamp legacy agent_runs / email_drafts with the principal's primary
      campaign id (there was historically one config per principal).
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "agent_configs" not in tables:
        return

    with engine.begin() as conn:
        # 1) Name unnamed campaigns from their active playbook (fallback "Campaign").
        if "agent_playbooks" in tables:
            conn.execute(
                text(
                    """
                    UPDATE agent_configs
                    SET name = COALESCE(
                        (SELECT p.name FROM agent_playbooks p
                         WHERE p.id = agent_configs.playbook_id),
                        'Campaign'
                    )
                    WHERE name IS NULL OR name = ''
                    """
                )
            )

        # Map each principal -> its primary (lowest-id) campaign.
        primary_rows = conn.execute(
            text(
                """
                SELECT principal_id, MIN(id) AS campaign_id
                FROM agent_configs
                GROUP BY principal_id
                """
            )
        ).all()
        primary_by_principal = {r[0]: r[1] for r in primary_rows}

        # 2) Backfill agent_runs.campaign_id.
        if "agent_runs" in tables:
            for principal_id, campaign_id in primary_by_principal.items():
                # Prefer a campaign whose playbook matches the run's playbook.
                conn.execute(
                    text(
                        """
                        UPDATE agent_runs
                        SET campaign_id = COALESCE(
                            (SELECT c.id FROM agent_configs c
                             WHERE c.principal_id = agent_runs.principal_id
                               AND c.playbook_id = agent_runs.playbook_id
                             LIMIT 1),
                            :campaign_id
                        )
                        WHERE campaign_id IS NULL AND principal_id = :principal_id
                        """
                    ),
                    {"principal_id": principal_id, "campaign_id": campaign_id},
                )

        # 3) Backfill email_drafts.campaign_id from the principal's primary campaign.
        if "email_drafts" in tables:
            for principal_id, campaign_id in primary_by_principal.items():
                conn.execute(
                    text(
                        """
                        UPDATE email_drafts
                        SET campaign_id = :campaign_id
                        WHERE campaign_id IS NULL AND principal_id = :principal_id
                        """
                    ),
                    {"principal_id": principal_id, "campaign_id": campaign_id},
                )
