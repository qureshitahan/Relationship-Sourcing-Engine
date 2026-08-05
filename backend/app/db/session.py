"""Database engine, session factory, and FastAPI dependency."""
from __future__ import annotations

import logging
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

_engine_kwargs: dict = {"connect_args": connect_args, "future": True}
if not settings.database_url.startswith("sqlite"):
    # Postgres (Azure): give the pool headroom + recycle so concurrent background
    # jobs (agent runs, LinkedIn worker, bulk reveals) don't starve web requests
    # with "QueuePool limit ... connection timed out", and stale/broken
    # connections are re-established instead of erroring.
    _engine_kwargs.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=30,
    )

engine = create_engine(settings.database_url, **_engine_kwargs)


def _sqlite_journal_mode() -> str:
    """Journal mode for the SQLite connection.

    WAL gives better read/write concurrency locally, but it CANNOT be used on a
    network filesystem: WAL relies on a shared-memory index (the ``-shm`` file)
    that cannot be maintained over SMB, which is exactly how Azure App Service
    mounts ``/home``. Using WAL there corrupts the database file under write load
    ("database disk image is malformed") — the outage we just recovered from. So
    on such paths we fall back to the classic rollback journal (DELETE), which
    works correctly over a network share. ``SQLITE_JOURNAL_MODE`` forces a mode.
    """
    override = (settings.sqlite_journal_mode or "").strip().upper()
    if override:
        return override
    # Azure App Service serves the app from /home, mounted over SMB. Treat any
    # absolute /home path as a network share and avoid WAL there.
    if "/home/" in settings.database_url.lower():
        return "DELETE"
    return "WAL"


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """Tune SQLite per deployment: a network-safe journal mode + a busy_timeout
    so concurrent access waits for the lock instead of failing, and durable
    commits on the (weaker-fsync) network share."""
    if not settings.database_url.startswith("sqlite"):
        return
    mode = _sqlite_journal_mode()
    cursor = dbapi_connection.cursor()
    cursor.execute(f"PRAGMA journal_mode={mode}")
    cursor.execute("PRAGMA busy_timeout=30000")
    # WAL pairs with NORMAL (its recommended setting); the rollback journal on a
    # network share uses FULL so an interrupted write can always be rolled back.
    cursor.execute(f"PRAGMA synchronous={'NORMAL' if mode == 'WAL' else 'FULL'}")
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
    """Create all tables + apply lightweight migrations — resiliently.

    A corrupted SQLite file (e.g. "database disk image is malformed") must NEVER
    make the whole app fail to boot and take every page down. So create_all and
    each migration/backfill step are individually guarded: the server still
    starts and serves whatever parts of the database are readable, and errors are
    logged rather than raised. For MVP we use create_all; swap to Alembic later.
    """
    # Import models so they register on the metadata before create_all.
    from app import models  # noqa: F401

    try:
        models.Base.metadata.create_all(bind=engine)
    except Exception:  # noqa: BLE001 - never let a schema op crash boot
        logging.getLogger(__name__).exception(
            "create_all failed during init_db; continuing so the app can boot"
        )
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


def _add_missing_columns() -> None:
    """Add any missing additive columns to existing tables (idempotent)."""
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


def _is_corruption_error(exc: Exception) -> bool:
    """True if the exception looks like SQLite file corruption (any of the
    several ways a damaged page surfaces), so we know to try a REINDEX repair."""
    msg = str(exc).lower()
    signatures = (
        "malformed",            # "database disk image is malformed"
        "disk image",
        "is corrupt",
        "database corruption",
        "string or blob too big",  # a bogus cell length read from a bad page
    )
    return any(s in msg for s in signatures)


def _attempt_reindex_repair() -> None:
    """Best-effort, non-destructive repair: REINDEX rebuilds all indexes from
    the table rows, which fixes index-level corruption (a common result of the
    WAL-on-network-share problem). Transactional, so a failure rolls back and
    leaves the file no worse. Only ever attempted when corruption is detected."""
    with engine.begin() as conn:
        conn.exec_driver_sql("REINDEX")


def _repair_unreadable_agent_runs() -> None:
    """Quarantine agent_runs rows the ORM can no longer load.

    Legacy of the SQLite-on-Azure corruption incident: a single damaged row
    (typically a truncated JSON ``summary``) makes EVERY full-table query fail —
    the campaigns list, the run history, and the response step of campaign
    creation all 500 — while targeted per-id lookups still work, so the damage
    stays invisible until those pages break.

    Probe with the same query shape the dashboards use; a healthy table returns
    early. On failure, hunt row-by-row and heal with the least destructive step
    that works: clear the row's ``summary`` in place via raw SQL (no JSON parse),
    and only if the row STILL cannot be loaded, delete that row. Never raises —
    the caller's step guard logs and continues so boot is never blocked.
    """
    log = logging.getLogger(__name__)
    inspector = inspect(engine)
    if "agent_runs" not in inspector.get_table_names():
        return

    from app.models.agent_run import AgentRun  # local import to avoid cycles
    from sqlalchemy import select

    probe = SessionLocal()
    try:
        probe.execute(select(AgentRun)).scalars().all()
        return  # every row loads — nothing to repair
    except Exception as exc:  # noqa: BLE001 - fall through to the row hunt
        probe.rollback()
        log.warning("agent_runs full scan failed (%s); hunting unreadable rows", exc)
    finally:
        probe.close()

    with engine.connect() as conn:
        ids = [r[0] for r in conn.exec_driver_sql("SELECT id FROM agent_runs ORDER BY id")]

    def _loads(run_id: int) -> bool:
        s = SessionLocal()
        try:
            s.get(AgentRun, run_id)
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            s.close()

    healed: list[int] = []
    deleted: list[int] = []
    for run_id in ids:
        if _loads(run_id):
            continue
        # Least destructive first: drop only the corrupt JSON payload.
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE agent_runs SET summary = NULL WHERE id = :id"),
                {"id": run_id},
            )
        if _loads(run_id):
            healed.append(run_id)
            continue
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM agent_runs WHERE id = :id"), {"id": run_id})
        deleted.append(run_id)
    log.warning(
        "agent_runs repair: cleared summary on %s, deleted unreadable %s",
        healed or "none",
        deleted or "none",
    )


def _apply_lightweight_migrations() -> None:
    """Add missing columns + backfill campaign scoping, each step isolated.

    Every step is wrapped so a failure — most importantly a corrupted-page error
    hit by a one-time backfill — is logged and skipped instead of crashing app
    startup (which would 500 every page). On the FIRST corruption error we make a
    single best-effort REINDEX repair attempt and retry that step; whether or not
    it succeeds, boot always continues so the readable data stays available.
    """
    log = logging.getLogger(__name__)
    steps = (
        _drop_agent_configs_principal_unique,
        _add_missing_columns,
        # After columns exist (the ORM probe names them all), quarantine any
        # rows a prior corruption event left unreadable.
        _repair_unreadable_agent_runs,
        _backfill_campaigns,
        _backfill_contact_campaign_ids,
    )
    repair_tried = False
    for step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - a migration must never crash boot
            if _is_corruption_error(exc) and not repair_tried:
                repair_tried = True
                log.warning(
                    "Corruption during '%s' (%s); attempting one-time REINDEX repair",
                    step.__name__, exc,
                )
                try:
                    _attempt_reindex_repair()
                    step()  # retry once now that indexes are rebuilt
                    log.warning("REINDEX repair succeeded; '%s' completed", step.__name__)
                    continue
                except Exception:  # noqa: BLE001
                    log.exception(
                        "REINDEX repair/retry of '%s' failed; skipping so the app can boot",
                        step.__name__,
                    )
                    continue
            log.exception(
                "DB migration step '%s' failed; continuing so the app can boot",
                step.__name__,
            )


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
