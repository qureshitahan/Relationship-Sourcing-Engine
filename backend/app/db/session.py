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


# Single-column indexes added to a model after a deployment already has data.
# create_all's index=True only lands on freshly-created tables, so an existing
# database needs this to actually get the index too.
_ADDITIVE_INDEXES: dict[str, list[str]] = {
    # Discovery dedup (_find_contact) looks up every candidate by linkedin_url;
    # without an index that's a full table scan per candidate, and it gets
    # slower as the table grows and as broader search filters surface more
    # candidates per run — this is why discovery got slower after filters were
    # widened, not just from the extra volume.
    "contacts": ["linkedin_url"],
}


def _add_missing_indexes() -> None:
    """Add any missing additive single-column indexes (idempotent)."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDITIVE_INDEXES.items():
            if table not in existing_tables:
                continue
            single_col_indexed = {
                ix["column_names"][0]
                for ix in inspector.get_indexes(table)
                if len(ix.get("column_names") or []) == 1
            }
            for column in columns:
                if column in single_col_indexed:
                    continue
                conn.execute(
                    text(f'CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table} ({column})')
                )


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
    """Heal agent_runs rows the ORM can no longer load.

    Legacy of the SQLite-on-Azure corruption incident: a single damaged row
    makes EVERY full-table query fail — the campaigns list, the run history,
    and the response step of campaign creation all 500 — while targeted per-id
    lookups still work, so the damage stays invisible until those pages break.

    Probe with the same query shape the dashboards use; a healthy table returns
    early. On failure, triage row by row:

      * soft damage (row reads, but e.g. a truncated JSON ``summary`` breaks the
        ORM load) → clear the summary in place, counters preserved;
      * hard damage (the page itself errors on read OR on write — in-place
        UPDATE/DELETE raise "database disk image is malformed") → rebuild the
        table: rename it aside, recreate fresh from the model, copy the
        readable rows back one by one, and drop the damaged remains. Same
        rebuild pattern as :func:`_drop_agent_configs_principal_unique`.

    The caller's step guard logs and continues on error, so boot never blocks.
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

    if not settings.database_url.startswith("sqlite"):
        return  # rebuild below is SQLite-specific; other backends: investigate manually

    with engine.connect() as conn:
        ids = [r[0] for r in conn.exec_driver_sql("SELECT id FROM agent_runs ORDER BY id")]

    def _orm_loads(run_id: int) -> bool:
        s = SessionLocal()
        try:
            s.get(AgentRun, run_id)
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            s.close()

    good: list[int] = []
    healed: list[int] = []
    bad: list[int] = []
    for run_id in ids:
        try:
            with engine.connect() as conn:
                conn.execute(
                    text("SELECT * FROM agent_runs WHERE id = :id"), {"id": run_id}
                ).fetchall()
            raw_ok = True
        except Exception:  # noqa: BLE001 - page-level damage
            raw_ok = False
        if raw_ok and _orm_loads(run_id):
            good.append(run_id)
            continue
        if raw_ok:
            # Soft damage: try clearing only the corrupt JSON payload in place.
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE agent_runs SET summary = NULL WHERE id = :id"),
                        {"id": run_id},
                    )
                if _orm_loads(run_id):
                    healed.append(run_id)
                    good.append(run_id)
                    continue
            except Exception:  # noqa: BLE001 - write to the row also fails
                pass
        bad.append(run_id)

    if not bad:
        # Rows all read individually yet the scan fails: btree structure damage.
        with engine.begin() as conn:
            conn.exec_driver_sql("REINDEX")
        log.warning("agent_runs repair: no bad rows; REINDEX attempted")
        return

    # Hard-damaged rows cannot be UPDATEd or DELETEd in place — rebuild the
    # table around them from the rows that are still readable.
    from app import models

    old_cols = [c["name"] for c in inspector.get_columns("agent_runs")]
    copied = 0
    skipped: list[int] = list(bad)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS agent_runs_damaged")
            conn.exec_driver_sql("ALTER TABLE agent_runs RENAME TO agent_runs_damaged")
            # Named indexes follow the rename and keep their names; drop them so
            # the fresh table can recreate identically-named indexes safely.
            leftover_idx = conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='agent_runs_damaged' AND name NOT LIKE 'sqlite_autoindex%'"
            ).fetchall()
            for (idx_name,) in leftover_idx:
                conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{idx_name}"')

        models.Base.metadata.tables["agent_runs"].create(bind=engine)

        new_cols = [c["name"] for c in inspect(engine).get_columns("agent_runs")]
        shared = ", ".join(c for c in old_cols if c in new_cols)
        for run_id in good:
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            f"INSERT INTO agent_runs ({shared}) "
                            f"SELECT {shared} FROM agent_runs_damaged WHERE id = :id"
                        ),
                        {"id": run_id},
                    )
                copied += 1
            except Exception:  # noqa: BLE001 - one bad row must not abort the rebuild
                skipped.append(run_id)
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("DROP TABLE agent_runs_damaged")
        except Exception:  # noqa: BLE001 - dropping damaged pages can itself fail
            log.warning("could not drop agent_runs_damaged; quarantined table left behind")
        log.warning(
            "agent_runs rebuild: copied %s row(s), healed summary on %s, skipped unreadable %s",
            copied,
            healed or "none",
            sorted(set(skipped)) or "none",
        )
        return
    except Exception:  # noqa: BLE001 - fall through to the last-resort reset
        log.exception("agent_runs rebuild failed; falling back to a clean empty table")

    # Last resort. agent_runs is history only — no other table's integrity
    # depends on it — so an empty table beats a broken one that 500s every
    # campaign page. DROP frees pages without parsing row content, so it
    # succeeds on damage that defeats every read-based recovery above.
    for table in ("agent_runs", "agent_runs_damaged"):
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
        except Exception:  # noqa: BLE001
            log.warning("could not drop %s during reset", table)
    models.Base.metadata.tables["agent_runs"].create(bind=engine)
    log.warning("agent_runs reset: table recreated empty; run history lost, app functional")


def _repair_corrupt_indexes() -> None:
    """Rebuild indexes that raise "malformed" when the query planner uses them.

    Index-level damage is the common residue of the WAL-on-network-share
    incident, and it is the *recoverable* kind: the table's rows are intact — an
    unfiltered scan succeeds — while any filtered query that walks the damaged
    index errors. Symptom seen in production: ``/api/prospects`` worked but
    ``?campaign_id=`` 500'd, because only the latter uses the damaged index.

    ``REINDEX`` rebuilds an index from the table it belongs to, so nothing is
    discarded. ``PRAGMA integrity_check`` names the offending indexes, and a
    healthy database returns "ok" immediately, keeping this cheap on every boot.
    """
    log = logging.getLogger(__name__)
    if not settings.database_url.startswith("sqlite"):
        return

    # A quarantined remains-table from a previous rebuild still carries the
    # corrupt pages, poisoning integrity_check and REINDEX below. Best effort:
    # DROP frees pages without parsing row content.
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS agent_runs_damaged")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not drop quarantined agent_runs_damaged: %s", exc)

    # Fast path: a healthy database answers "ok" and we stop here, so this costs
    # one cheap check per boot. Note the check ITSELF raises on some damage —
    # that is a positive corruption signal, not a reason to give up (measured:
    # integrity_check errors on damage that REINDEX then repairs completely).
    try:
        with engine.connect() as conn:
            rows = conn.exec_driver_sql("PRAGMA integrity_check(100)").fetchall()
        reports = [str(r[0]) for r in rows]
        if [r.lower() for r in reports] == ["ok"]:
            return
        log.warning(
            "integrity_check reported %s problem(s), first: %s",
            len(reports),
            reports[0][:120],
        )
    except Exception as exc:  # noqa: BLE001 - the failure is itself the signal
        log.warning("integrity_check failed (%s); treating as corruption", exc)

    # REINDEX rebuilds each index from the table's own rows, so no data is
    # discarded. Per table rather than globally: one unrecoverable table must
    # not stop every other table from being repaired.
    try:
        tables = sorted(inspect(engine).get_table_names())
    except Exception as exc:  # noqa: BLE001
        log.warning("could not list tables for index repair: %s", exc)
        return

    repaired: list[str] = []
    failed: list[str] = []
    for table in tables:
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(f'REINDEX "{table}"')
            repaired.append(table)
        except Exception:  # noqa: BLE001 - keep going; report at the end
            failed.append(table)
    log.warning(
        "index repair: reindexed %s table(s); failed on %s",
        len(repaired),
        failed or "none",
    )

    # Escalation for tables REINDEX could not fix: replace each named index
    # outright. DROP discards the damaged b-tree without fully parsing it, and
    # CREATE rebuilds from the (healthy) table rows — measured to succeed on
    # damage classes where an in-place REINDEX still errors. sqlite_master keeps
    # each index's original DDL; autoindexes (sql IS NULL) can't be dropped and
    # are skipped.
    for table in failed:
        try:
            with engine.connect() as conn:
                index_ddl = conn.exec_driver_sql(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                    (table,),
                ).fetchall()
        except Exception as exc:  # noqa: BLE001
            log.warning("could not list indexes of %s: %s", table, exc)
            continue
        rebuilt: list[str] = []
        unrecovered: list[str] = []
        for index_name, ddl in index_ddl:
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{index_name}"')
                    conn.exec_driver_sql(ddl)
                rebuilt.append(index_name)
            except Exception:  # noqa: BLE001 - keep going; report at the end
                unrecovered.append(index_name)
        log.warning(
            "index replace on %s: rebuilt %s; unrecovered %s",
            table,
            rebuilt or "none",
            unrecovered or "none",
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
        _add_missing_indexes,
        # After columns exist (the ORM probe names them all), quarantine any
        # rows a prior corruption event left unreadable...
        _repair_unreadable_agent_runs,
        # ...then rebuild any index it also damaged, so filtered queries
        # (campaign pages, prospect lists) stop erroring.
        _repair_corrupt_indexes,
        _backfill_campaigns,
        # Merge duplicate companies before anything reads them by dedup key;
        # a duplicate aborts the whole discovery run (MultipleResultsFound).
        _dedupe_companies,
        _backfill_contact_campaign_ids,
        # Runs after campaign ids are stamped, so a contact's campaign is known
        # before we use it to decide which principal owns its research.
        _backfill_insight_principal,
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


def _backfill_insight_principal() -> None:
    """Re-point research that was saved against the wrong principal.

    Until routes/prospects.py was fixed, the Research button sent no principal
    and the backend fell back to "oldest principal by id" — on a multi-principal
    deployment almost never the right one. The damage is invisible from the
    Prospects page, because ``Contact.relevance_score`` is shared across
    principals while ``RelevanceInsight`` is per-principal: the prospect looks
    researched, then drafting rejects it with "research failed".

    Move each misattributed insight onto the principal that actually owns the
    prospect — its campaign first, else its discovery run. Nothing is
    re-researched, so this costs no tokens.

    Correlated-subquery form so it runs on both SQLite and Postgres. The NOT
    EXISTS guard respects UNIQUE(principal_id, contact_id): where the right
    principal already has its own research, the stale row is left alone rather
    than colliding.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if not {"relevance_insights", "contacts"} <= tables:
        return

    owner_sql = """
        SELECT CASE
                 WHEN ac.principal_id IS NOT NULL THEN ac.principal_id
                 ELSE dr.principal_id
               END
        FROM contacts c
        LEFT JOIN agent_configs ac ON ac.id = c.campaign_id
        LEFT JOIN discovery_runs dr ON dr.id = c.discovery_run_id
        WHERE c.id = relevance_insights.contact_id
    """
    with engine.begin() as conn:
        result = conn.execute(
            text(
                f"""
                UPDATE relevance_insights
                SET principal_id = ({owner_sql})
                WHERE contact_id IS NOT NULL
                  AND ({owner_sql}) IS NOT NULL
                  AND ({owner_sql}) <> principal_id
                  AND NOT EXISTS (
                      SELECT 1 FROM relevance_insights other
                      WHERE other.contact_id = relevance_insights.contact_id
                        AND other.principal_id = ({owner_sql})
                  )
                """
            )
        )
        moved = result.rowcount or 0
    if moved:
        logging.getLogger(__name__).warning(
            "insight principal backfill: moved %s insight(s) onto the owning principal",
            moved,
        )


#: Every table that points at ``companies.id``. Used by the dedup merge below
#: to re-point children off duplicate rows before those rows are deleted.
_COMPANY_CHILD_TABLES = (
    "contacts",
    "relevance_insights",
    "email_drafts",
    "linkedin_messages",
    "calls",
    "suppressions",
)


def _dedupe_companies() -> None:
    """Merge duplicate ``companies`` rows, then make the dedup key unique.

    ``Company.normalized_name`` was only indexed, never unique, so nothing at the
    database level stopped two rows sharing a key — concurrent discovery runs and
    the SQLite->Postgres migration both produced them. ``get_or_create_company``
    then raised ``MultipleResultsFound`` and aborted the entire discovery run the
    first time it touched an affected company, which is why "Run now" looked like
    it did nothing.

    Oldest row wins (same rule as ``get_or_create_company``). Children are
    re-pointed onto the survivor before the duplicates are deleted, so no contact,
    insight, draft, or message is lost. The unique index is created last and only
    if the merge left the column clean — an index that fails to build must not
    fail boot, and the ``first()`` read is already duplicate-tolerant regardless.
    """
    log = logging.getLogger(__name__)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "companies" not in tables:
        return

    # Resolve the child list against the live schema, not the model list: an
    # older database can have the table without the column (or not at all), and
    # one bad UPDATE would roll the whole merge back and leave the duplicates.
    children = [
        t
        for t in _COMPANY_CHILD_TABLES
        if t in tables and "company_id" in {c["name"] for c in inspector.get_columns(t)}
    ]

    with engine.begin() as conn:
        dupes = conn.execute(
            text(
                """
                SELECT normalized_name, MIN(id) AS keep_id
                FROM companies
                GROUP BY normalized_name
                HAVING COUNT(*) > 1
                """
            )
        ).all()
        merged = 0
        for normalized_name, keep_id in dupes:
            params = {"keep": keep_id, "name": normalized_name}
            for child in children:
                conn.execute(
                    text(
                        f"""
                        UPDATE {child}
                        SET company_id = :keep
                        WHERE company_id IN (
                            SELECT id FROM companies
                            WHERE normalized_name = :name AND id <> :keep
                        )
                        """
                    ),
                    params,
                )
            result = conn.execute(
                text(
                    "DELETE FROM companies "
                    "WHERE normalized_name = :name AND id <> :keep"
                ),
                params,
            )
            merged += result.rowcount or 0
        if merged:
            log.warning(
                "company dedup: merged %s duplicate row(s) across %s name(s)",
                merged, len(dupes),
            )

    # Separate transaction: if the unique index still cannot be built (a name
    # slipped in between the merge and here), boot must continue anyway.
    already_unique = any(
        ix.get("unique") and ix.get("column_names") == ["normalized_name"]
        for ix in inspector.get_indexes("companies")
    )
    if already_unique:
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_companies_normalized_name ON companies (normalized_name)"
                )
            )
    except Exception as exc:  # noqa: BLE001 - duplicates must not block boot
        log.warning("Could not create unique index on companies.normalized_name: %s", exc)


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
