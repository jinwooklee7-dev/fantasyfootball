"""Thin retry layer over nflreadpy.

nflverse data is served from GitHub release assets, which return transient 504s
often enough that an unguarded call will fail a cron run for no real reason.
Every loader goes through `fetch`.

nflreadpy returns Polars. Keep it that way; call .to_pandas() only at an edge
that demands it. See CLAUDE.md.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import polars as pl
from nflreadpy.config import update_config

from .config import REPO_ROOT

_CACHE_DIR = REPO_ROOT / "db" / "nflverse_cache"
_configured = False


def _configure() -> None:
    """Cache downloads on disk so a re-run during the same hour is free."""
    global _configured
    if _configured:
        return
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    update_config(
        cache_mode="filesystem",
        cache_dir=_CACHE_DIR,
        cache_duration=3600,
        timeout=120,
    )
    _configured = True


def fetch(loader: Callable[..., pl.DataFrame], *args: Any, **kwargs: Any) -> pl.DataFrame:
    """Call an nflreadpy loader, retrying transient network failures.

    Backs off 2s, 5s, 12s, 30s, 60s. The 504s arrive in bursts lasting up to a
    minute, so patience beats a tight retry loop.
    """
    _configure()
    delays = [2, 5, 12, 30, 60]
    last: Exception | None = None

    for attempt, delay in enumerate([*delays, None]):
        try:
            return loader(*args, **kwargs)
        except Exception as exc:  # nflreadpy wraps everything as ConnectionError
            if not _is_transient(exc) or delay is None:
                raise
            last = exc
            print(
                f"  {loader.__name__}: {type(exc).__name__} on attempt "
                f"{attempt + 1}, retrying in {delay}s"
            )
            time.sleep(delay)

    raise RuntimeError(f"unreachable") from last


_TRANSIENT_MARKERS = (
    "504", "502", "503", "500", "429",
    "gateway", "timeout", "timed out", "temporarily",
    "connection", "reset", "eof occurred",
)


def _is_transient(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(m in text for m in _TRANSIENT_MARKERS)


def clean_str(value: Any) -> str | None:
    """Polars nulls, NaN and empty strings all become None on the way into SQLite."""
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "null", "na"}:
        return None
    return text


def clean_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return int(f)


def clean_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def has(df: pl.DataFrame, *columns: str) -> bool:
    return all(c in df.columns for c in columns)


def pick(row: dict[str, Any], *names: str) -> Any:
    """First present, non-null value among several candidate column names.

    nflverse renames columns between seasons and datasets more than you would
    like; this keeps the call sites readable.
    """
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return None
