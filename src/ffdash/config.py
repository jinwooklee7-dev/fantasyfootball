"""Paths and environment. Everything resolves from the repo root."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(REPO_ROOT / ".env")


def db_path() -> Path:
    raw = os.getenv("FFDASH_DB", "db/ffdash.db")
    p = Path(raw)
    return p if p.is_absolute() else REPO_ROOT / p


def schema_path() -> Path:
    return REPO_ROOT / "db" / "schema.sql"


def data_dir() -> Path:
    return REPO_ROOT / "data"


def public_mode() -> bool:
    """True when the site is reachable by anyone, not just you.

    Turns off the NFL team logos. They are club trademarks: fetching them for a
    tool only you use is one thing, republishing them on a public page is
    another. The coloured initials badges are the fallback and look fine.

    Set FFDASH_PUBLIC=1 before serving anything you have shared a link to.
    """
    return os.getenv("FFDASH_PUBLIC", "").strip().lower() in {"1", "true", "yes", "on"}


def current_season() -> int:
    """The NFL season a given calendar date belongs to.

    A season is named for the year it starts in, so anything before March
    still belongs to the previous year's season.
    """
    override = os.getenv("FFDASH_SEASON")
    if override:
        return int(override)
    today = date.today()
    return today.year if today.month >= 3 else today.year - 1
