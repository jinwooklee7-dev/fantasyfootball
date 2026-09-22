"""SQLite access. One file, WAL mode, foreign keys on.

Every write in this project goes through `upsert`, never a blind INSERT, so that
every ingest script is safe to re-run. See CLAUDE.md.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import db_path, schema_path


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def session(path: Path | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_schema(conn: sqlite3.Connection) -> None:
    """Apply db/schema.sql. It is written to be idempotent."""
    conn.executescript(schema_path().read_text(encoding="utf-8"))


def upsert(
    conn: sqlite3.Connection,
    table: str,
    rows: Iterable[dict[str, Any]],
    key: Sequence[str],
) -> int:
    """Insert rows, updating the non-key columns on conflict.

    Returns the number of rows sent. Empty input is a no-op, not an error --
    an ingest that finds nothing new is a normal Tuesday.
    """
    rows = [r for r in rows if r]
    if not rows:
        return 0

    columns = list(rows[0].keys())
    missing = [k for k in key if k not in columns]
    if missing:
        raise ValueError(f"{table}: key columns missing from row: {missing}")

    placeholders = ", ".join("?" for _ in columns)
    collist = ", ".join(columns)
    updatable = [c for c in columns if c not in key]

    if updatable:
        setclause = ", ".join(f"{c} = excluded.{c}" for c in updatable)
        conflict = f"ON CONFLICT({', '.join(key)}) DO UPDATE SET {setclause}"
    else:
        conflict = f"ON CONFLICT({', '.join(key)}) DO NOTHING"

    sql = f"INSERT INTO {table} ({collist}) VALUES ({placeholders}) {conflict}"
    payload = [tuple(r.get(c) for c in columns) for r in rows]
    conn.executemany(sql, payload)
    return len(payload)


def insert_ignore(
    conn: sqlite3.Connection,
    table: str,
    rows: Iterable[dict[str, Any]],
) -> int:
    """For append-only event tables with a UNIQUE constraint (depth_chart_event)."""
    rows = [r for r in rows if r]
    if not rows:
        return 0
    columns = list(rows[0].keys())
    sql = (
        f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})"
    )
    conn.executemany(sql, [tuple(r.get(c) for c in columns) for r in rows])
    return len(rows)


def ensure_columns(
    conn: sqlite3.Connection, table: str, columns: dict[str, str]
) -> list[str]:
    """Add columns that a schema revision introduced. Returns the ones added.

    `CREATE TABLE IF NOT EXISTS` silently does nothing on an existing table, so
    a new column in schema.sql never reaches a database that already exists.
    Rather than asking for a rebuild, add the gap here. `columns` maps name to
    its SQL declaration, e.g. {"team_color": "TEXT"}.
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    added = []
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            added.append(name)
    return added


def scalar(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        scalar(
            conn,
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (name,),
        )
    )
