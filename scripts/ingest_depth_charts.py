#!/usr/bin/env python3
"""Depth charts as an EVENT LOG, not a weekly snapshot.

From 2025 nflverse no longer assigns depth charts to a week; each update is
appended with its own ISO8601 timestamp. That is a feature -- it lets us see the
exact day a backup moved up. Rows are inserted, never updated. See CLAUDE.md.

    uv run scripts/ingest_depth_charts.py [--season 2025]

Cron: daily.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse

import nflreadpy as nfl

from ffdash.config import current_season
from ffdash.db import insert_ignore, scalar, session
from ffdash.ingestlog import logged
from ffdash.nflsource import clean_int, clean_str, fetch
from ffdash.players import Crosswalk
from ffdash.timeutil import to_utc_iso


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()
    season = args.season or current_season()

    with session() as conn:
        with logged(conn, "depth_charts") as run:
            xw = Crosswalk(conn)
            known_teams = {r[0] for r in conn.execute("SELECT team_abbr FROM team")}
            depth = fetch(nfl.load_depth_charts, seasons=[season])
            before = scalar(conn, "SELECT COUNT(*) FROM depth_chart_event")

            rows = []
            for r in depth.iter_rows(named=True):
                pid = xw.get("gsis", r.get("gsis_id"))
                team = clean_str(r.get("team"))
                observed = _iso(r.get("dt"))
                if pid is None or observed is None or team not in known_teams:
                    continue
                rows.append(
                    {
                        "observed_at": observed,
                        "team_abbr": team,
                        "player_id": pid,
                        "position": clean_str(r.get("pos_abb")),
                        "depth_rank": clean_int(r.get("pos_rank")),
                    }
                )

            insert_ignore(conn, "depth_chart_event", rows)
            after = scalar(conn, "SELECT COUNT(*) FROM depth_chart_event")
            run.rows = after - before
            run.note = f"season {season}; {len(rows)} seen, {after - before} new"
            if xw.miss_summary():
                run.note += f"; {xw.miss_summary()}"


def _iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return to_utc_iso(value)
    return clean_str(value)


if __name__ == "__main__":
    main()
