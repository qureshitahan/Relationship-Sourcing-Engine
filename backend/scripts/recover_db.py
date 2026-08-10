"""Rebuild a corrupt SQLite database into a clean one, keeping every readable row.

Why this exists: the Azure deployment runs SQLite on /home, an SMB share, which
is what corrupted the file in the first place (see db/session.py). The boot-time
repairs in that module patch individual damaged spots so the app keeps serving;
this script fixes the *file* in one pass instead, which is the right move once
the damage is spread across several tables.

It is deliberately row-at-a-time rather than a bulk `INSERT ... SELECT`: on a
damaged page a bulk copy aborts the whole table, while row-at-a-time loses only
the rows that physically cannot be read, and reports exactly which those are.

Usage (from backend/):
    python -m scripts.recover_db <corrupt.db> <clean.db>

Nothing is written to the source file — it is opened read-only.
"""
from __future__ import annotations

import os
import sqlite3
import sys


def _schema_statements(src: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """Return (table DDL, everything-else DDL) from the source schema.

    Indexes/triggers/views are created *after* the rows are copied: building an
    index once at the end is far faster than maintaining it per INSERT, and a
    corrupt source index cannot poison the fresh file this way.
    """
    tables: list[str] = []
    rest: list[str] = []
    rows = src.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for obj_type, _name, sql in rows:
        (tables if obj_type == "table" else rest).append(sql)
    return tables, rest


def _source_count(src: sqlite3.Connection, table: str) -> int | None:
    """count(*) on the source, or None when the damage prevents even counting.

    Needed for honest reporting: rows sitting on a corrupt page can vanish from
    a `SELECT rowid` listing entirely, so "rows we failed to read" undercounts
    the real loss. Comparing against count(*) surfaces those silent gaps.
    """
    try:
        return int(src.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
    except sqlite3.DatabaseError:
        return None


def _copy_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> tuple[int, int]:
    """Copy one table row by row. Returns (copied, lost)."""
    cols = [r[1] for r in src.execute(f'PRAGMA table_info("{table}")').fetchall()]
    if not cols:
        return 0, 0
    col_list = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" for _ in cols)
    insert = f'INSERT OR IGNORE INTO "{table}" ({col_list}) VALUES ({placeholders})'

    # Walk by rowid so one unreadable page costs us that page's rows, not the
    # whole table: a plain cursor iteration stops dead at the first bad page.
    try:
        ids = [r[0] for r in src.execute(f'SELECT rowid FROM "{table}" ORDER BY rowid')]
    except sqlite3.DatabaseError as exc:
        print(f"  ! {table}: cannot even list rowids ({exc}); skipping table")
        return 0, -1

    copied = lost = 0
    for rowid in ids:
        try:
            row = src.execute(
                f'SELECT {col_list} FROM "{table}" WHERE rowid = ?', (rowid,)
            ).fetchone()
        except sqlite3.DatabaseError:
            lost += 1
            continue
        if row is None:
            continue
        try:
            dst.execute(insert, row)
            copied += 1
        except sqlite3.DatabaseError:
            lost += 1
    dst.commit()
    return copied, lost


def recover(source: str, target: str) -> int:
    if not os.path.exists(source):
        print(f"source not found: {source}")
        return 2
    if os.path.exists(target):
        print(f"refusing to overwrite existing target: {target}")
        return 2

    # Read-only URI so a damaged source is never modified by this script.
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    dst.execute("PRAGMA journal_mode=DELETE")
    dst.execute("PRAGMA synchronous=FULL")

    try:
        tables_ddl, rest_ddl = _schema_statements(src)
        for sql in tables_ddl:
            dst.execute(sql)
        dst.commit()

        names = [
            r[0]
            for r in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]

        total_copied = total_missing = 0
        for table in names:
            before = _source_count(src, table)
            copied, lost = _copy_table(src, dst, table)
            total_copied += copied
            # Truth = source count minus what landed, not just read failures:
            # rows on a corrupt page can disappear from the rowid listing.
            missing = (before - copied) if before is not None else max(lost, 0)
            if missing > 0:
                total_missing += missing
            flag = "" if missing <= 0 else f"  <-- {missing} row(s) LOST (unrecoverable)"
            print(f"  {table:<24} {copied:>7} rows{flag}")

        print("\nrebuilding indexes/triggers/views...")
        failed_ddl = 0
        for sql in rest_ddl:
            try:
                dst.execute(sql)
            except sqlite3.DatabaseError as exc:
                failed_ddl += 1
                print(f"  ! could not create: {str(exc)[:80]}")
        dst.commit()

        # Prove the result is clean rather than assuming it.
        check = dst.execute("PRAGMA integrity_check").fetchall()
        verdict = str(check[0][0]) if check else "unknown"
        print(f"\ncopied {total_copied} rows, LOST {total_missing}, DDL failures {failed_ddl}")
        print(f"integrity_check on the NEW file: {verdict}")
        return 0 if verdict.lower() == "ok" else 1
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(recover(sys.argv[1], sys.argv[2]))
