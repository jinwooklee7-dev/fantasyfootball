#!/usr/bin/env python3
"""Snap counts. Keyed on pfr_player_id, NOT gsis -- resolved via the crosswalk.

nflverse snap counts carry no route data, so snap_count.routes_run and
route_pct stay NULL. Route participation is not available free; the usage block
leans on snap % and target share instead.

    uv run scripts/ingest_snaps.py [--season 2025]

Cron: 4x daily.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse

import nflreadpy as nfl

from ffdash.config import current_season
from ffdash.db import session, upsert
from ffdash.ingestlog import logged
from ffdash.nflsource import clean_float, clean_int, clean_str, fetch
from ffdash.players import Crosswalk
from ffdash.timeutil import utc_now_iso


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()
    season = args.season or current_season()

    with session() as conn:
        with logged(conn, "snaps") as run:
            xw = Crosswalk(conn)
            snaps = fetch(nfl.load_snap_counts, seasons=[season])
            now = utc_now_iso()

            rows = []
            for r in snaps.iter_rows(named=True):
                pid = xw.get("pfr", r.get("pfr_player_id"))
                week = clean_int(r.get("week"))
                if pid is None or week is None:
                    continue
                pct = clean_float(r.get("offense_pct"))
                rows.append(
                    {
                        "player_id": pid,
                        "season": season,
                        "week": week,
                        "offense_snaps": clean_int(r.get("offense_snaps")),
                        # nflverse gives this as a 0-1 fraction; store it that way
                        # and format at render time.
                        "offense_pct": pct,
                        "routes_run": None,
                        "route_pct": None,
                        "updated_at": now,
                    }
                )

            run.rows = upsert(
                conn, "snap_count", rows, key=["player_id", "season", "week"]
            )
            run.note = f"season {season}"
            if xw.miss_summary():
                run.note += f"; {xw.miss_summary()}"


if __name__ == "__main__":
    main()
