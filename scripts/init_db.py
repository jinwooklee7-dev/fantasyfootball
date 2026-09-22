#!/usr/bin/env python3
"""Create the database and seed reference tables. Safe to run twice.

    uv run scripts/init_db.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import csv

import nflreadpy as nfl

from ffdash.config import data_dir, db_path
from ffdash.db import apply_schema, ensure_columns, scalar, session
from ffdash.ingestlog import logged
from ffdash.nflsource import clean_float, clean_str, fetch
from ffdash.timeutil import utc_now_iso


def seed_teams(conn) -> int:
    teams = fetch(nfl.load_teams)
    rows = []
    seen = set()
    for r in teams.iter_rows(named=True):
        abbr = clean_str(r.get("team_abbr"))
        if not abbr or abbr in seen:
            continue
        seen.add(abbr)
        rows.append(
            {
                "team_abbr": abbr,
                "full_name": clean_str(r.get("team_name")) or abbr,
                "conference": clean_str(r.get("team_conf")),
                "division": clean_str(r.get("team_division")),
                "nick": clean_str(r.get("team_nick")),
                "team_color": clean_str(r.get("team_color")),
                "team_color2": clean_str(r.get("team_color2")),
                # ESPN's 500px PNGs are the cleanest of the four sets nflverse
                # offers. fetch_logos.py caches them locally.
                "logo_url": clean_str(r.get("team_logo_espn")),
            }
        )
    from ffdash.db import upsert

    return upsert(conn, "team", rows, key=["team_abbr"])


def seed_stadiums(conn) -> int:
    """Load data/stadiums.csv. verified_at stays NULL until a human checks it --
    the file ships with best-effort coordinates and several venues are in flux.
    """
    from ffdash.db import upsert

    path = data_dir() / "stadiums.csv"
    rows = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(_strip_comments(fh)):
            sid = clean_str(r.get("stadium_id"))
            if not sid:
                continue
            rows.append(
                {
                    "stadium_id": sid,
                    "name": clean_str(r.get("name")) or sid,
                    "team_abbrs": clean_str(r.get("team_abbrs")) or "",
                    "latitude": clean_float(r.get("latitude")),
                    "longitude": clean_float(r.get("longitude")),
                    "roof": clean_str(r.get("roof")) or "open",
                    "timezone": clean_str(r.get("timezone")) or "America/New_York",
                }
            )
    written = upsert(conn, "stadium", rows, key=["stadium_id"])

    # Drop stadium rows the CSV no longer defines, so a re-key does not leave
    # orphans behind. Games reference stadiums by the nflverse id only.
    keep = [r["stadium_id"] for r in rows]
    placeholders = ", ".join("?" for _ in keep)
    conn.execute(
        f"DELETE FROM stadium WHERE stadium_id NOT IN ({placeholders}) "
        "AND stadium_id NOT IN (SELECT DISTINCT stadium_id FROM game "
        "WHERE stadium_id IS NOT NULL)",
        keep,
    )
    return written


def _strip_comments(fh):
    """The seed CSVs carry '#' comment blocks that csv.DictReader would choke on."""
    for line in fh:
        if not line.lstrip().startswith("#"):
            yield line


def main() -> None:
    with session() as conn:
        apply_schema(conn)
        # Columns added after the first release; a no-op on a fresh database.
        added = ensure_columns(
            conn,
            "team",
            {
                "nick": "TEXT",
                "team_color": "TEXT",
                "team_color2": "TEXT",
                "logo_url": "TEXT",
            },
        )
        if added:
            print(f"added team columns: {', '.join(added)}")
        conn.commit()
        print(f"schema applied to {db_path()}")

        with logged(conn, "init_db.teams") as run:
            run.rows = seed_teams(conn)

        with logged(conn, "init_db.stadiums") as run:
            run.rows = seed_stadiums(conn)
            unverified = scalar(
                conn, "SELECT COUNT(*) FROM stadium WHERE verified_at IS NULL"
            )
            run.note = f"{unverified} stadiums not yet human-verified"

    print(f"\ndone at {utc_now_iso()}")
    print("NOTE: stadium coordinates and roof types are best-effort seed values.")
    print("      Verify them before the season, then set stadium.verified_at.")


if __name__ == "__main__":
    main()
