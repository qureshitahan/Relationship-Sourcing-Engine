"""One-off data migration: prod SQLite (possibly partially corrupt) -> Postgres.

Reads each table in FK-safe order (Base.metadata.sorted_tables handles the
topological sort). For tables with a primary key, first lists all ids (a
single query), then fetches + inserts each row ONE AT A TIME wrapped in its
own try/except. This is deliberately slower than a bulk SELECT * so that a
single corrupted page only kills the one row's query, not the whole table's
result set (SQLite corruption errors are not safely recoverable mid-cursor
for the query that hit them, but a fresh query for a different row is fine).

SQLite never enforced foreign keys on this database, so some rows reference
already-deleted parents. Since the goal is to carry over ALL existing data
as-is (not silently drop orphaned rows), FK/trigger enforcement is disabled
for the single connection used throughout the migration, then restored.

Usage:
    python scripts/migrate_sqlite_to_postgres.py "C:\\path\\to\\relationship_engine.db" "<postgres-url>"

Not safely re-runnable against a non-empty target: it INSERTs unconditionally,
so re-running after a partial run will hit primary-key conflicts on rows
already migrated. Truncate the Postgres tables first if re-running.
"""
from __future__ import annotations

import sys
import sqlite3
import time

from sqlalchemy import create_engine, insert, text

sys.path.insert(0, ".")
from app.db.base import Base
from app import models  # noqa: F401 - registers all models on Base.metadata


def migrate(sqlite_path: str, pg_url: str) -> None:
    pg_engine = create_engine(pg_url)

    total_migrated = 0
    total_failed = 0
    summary: list[tuple[str, int, int, int]] = []  # table, total, migrated, failed

    # One connection for the whole migration so the FK-disable setting below
    # actually applies to every insert (it's a per-session setting).
    with pg_engine.connect() as pconn:
        pconn.execute(text("SET session_replication_role = 'replica'"))
        pconn.commit()

        for table in Base.metadata.sorted_tables:
            tname = table.name
            pk_cols = list(table.primary_key.columns)
            started = time.monotonic()

            sconn = sqlite3.connect(sqlite_path)
            sconn.row_factory = sqlite3.Row

            if not pk_cols:
                print(f"[{tname}] SKIP - no primary key, unexpected for this schema")
                sconn.close()
                continue

            pk_col = pk_cols[0].name
            try:
                ids = [
                    r[0]
                    for r in sconn.execute(f'SELECT "{pk_col}" FROM "{tname}"').fetchall()
                ]
            except Exception as exc:  # noqa: BLE001
                print(f"[{tname}] FAILED to list ids at all: {exc}")
                sconn.close()
                summary.append((tname, 0, 0, 0))
                continue

            migrated = 0
            failed_ids: list = []
            batch: list[dict] = []
            BATCH_SIZE = 200

            try:
                for i, rid in enumerate(ids):
                    try:
                        row = sconn.execute(
                            f'SELECT * FROM "{tname}" WHERE "{pk_col}" = ?', (rid,)
                        ).fetchone()
                    except Exception:  # noqa: BLE001 - corrupted page for this row
                        failed_ids.append(rid)
                        continue
                    if row is None:
                        continue
                    batch.append(dict(row))

                    if len(batch) >= BATCH_SIZE:
                        pconn.execute(insert(table), batch)
                        migrated += len(batch)
                        batch = []

                    if (i + 1) % 2000 == 0:
                        print(f"[{tname}] progress {i + 1}/{len(ids)}...")

                if batch:
                    pconn.execute(insert(table), batch)
                    migrated += len(batch)
                pconn.commit()
            except Exception:
                pconn.rollback()
                raise

            elapsed = time.monotonic() - started
            print(
                f"[{tname}] done: {migrated}/{len(ids)} migrated, "
                f"{len(failed_ids)} failed (ids={failed_ids[:10]}) in {elapsed:.1f}s"
            )
            summary.append((tname, len(ids), migrated, len(failed_ids)))
            total_migrated += migrated
            total_failed += len(failed_ids)
            sconn.close()

        pconn.execute(text("SET session_replication_role = 'origin'"))
        pconn.commit()

    print("\n=== SUMMARY ===")
    for tname, total, migrated, failed in summary:
        flag = "  <-- had failures" if failed else ""
        print(f"{tname:25s} total={total:6d} migrated={migrated:6d} failed={failed:4d}{flag}")
    print(f"\nTOTAL migrated={total_migrated} failed={total_failed}")


if __name__ == "__main__":
    sqlite_path = sys.argv[1]
    pg_url = sys.argv[2]
    migrate(sqlite_path, pg_url)
