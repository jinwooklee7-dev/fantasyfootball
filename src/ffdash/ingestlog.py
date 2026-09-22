"""Every ingest writes a row to ingest_log. No exceptions -- that table is how
the UI knows whether what it is showing is fresh or stale.
"""

from __future__ import annotations

import sqlite3
import sys
import traceback
from contextlib import contextmanager
from dataclasses import dataclass

from .timeutil import utc_now_iso


@dataclass
class Run:
    rows: int = 0
    note: str | None = None


@contextmanager
def logged(conn: sqlite3.Connection, source: str):
    """Wrap an ingest. Records start, finish, row count and success.

    A failure is logged with ok=0 and then re-raised -- cron will surface it,
    and the UI will show the stale timestamp rather than a blank panel.
    """
    started = utc_now_iso()
    cur = conn.execute(
        "INSERT INTO ingest_log (source, started_at, ok) VALUES (?, ?, 0)",
        (source, started),
    )
    log_id = cur.lastrowid
    conn.commit()

    run = Run()
    try:
        yield run
    except Exception as exc:
        conn.execute(
            "UPDATE ingest_log SET finished_at = ?, rows_written = ?, ok = 0, note = ? "
            "WHERE id = ?",
            (utc_now_iso(), run.rows, f"{type(exc).__name__}: {exc}"[:500], log_id),
        )
        conn.commit()
        print(f"[{source}] FAILED: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise
    else:
        conn.execute(
            "UPDATE ingest_log SET finished_at = ?, rows_written = ?, ok = 1, note = ? "
            "WHERE id = ?",
            (utc_now_iso(), run.rows, run.note, log_id),
        )
        conn.commit()
        print(f"[{source}] ok, {run.rows} rows" + (f" ({run.note})" if run.note else ""))


def last_success(conn: sqlite3.Connection, source: str) -> str | None:
    row = conn.execute(
        "SELECT finished_at FROM ingest_log WHERE source = ? AND ok = 1 "
        "ORDER BY started_at DESC LIMIT 1",
        (source,),
    ).fetchone()
    return row[0] if row else None
