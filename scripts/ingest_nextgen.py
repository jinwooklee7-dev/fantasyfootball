#!/usr/bin/env python3
"""Next Gen Stats. Supplementary context for the usage block.

Stored long (one row per metric) in its own table rather than bolted onto
player_week_stat -- the columns differ by stat type and would otherwise be two
thirds NULL.

    uv run scripts/ingest_nextgen.py [--season 2025]

Cron: nightly.
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

# (stat type, nflverse column) -> our metric name. Kept explicit so an upstream
# rename surfaces as a reported missing column rather than a silently empty panel.
METRICS = {
    "receiving": {
        "avg_separation": "avg_separation",
        "avg_cushion": "avg_cushion",
        "avg_intended_air_yards": "avg_intended_air_yards",
        "percent_share_of_intended_air_yards": "air_yards_share_ngs",
        "avg_yac_above_expectation": "yac_above_expected",
    },
    "rushing": {
        "efficiency": "rush_efficiency",
        "percent_attempts_gte_eight_defenders": "pct_stacked_box",
        "rush_yards_over_expected_per_att": "ryoe_per_att",
        "avg_time_to_los": "avg_time_to_los",
    },
    "passing": {
        "avg_time_to_throw": "avg_time_to_throw",
        "completion_percentage_above_expectation": "cpoe_ngs",
        "aggressiveness": "aggressiveness",
    },
}

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS nextgen_stat (
  player_id  INTEGER NOT NULL REFERENCES player(player_id),
  season     INTEGER NOT NULL,
  week       INTEGER NOT NULL,
  metric     TEXT NOT NULL,
  value      REAL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (player_id, season, week, metric)
)
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()
    season = args.season or current_season()

    with session() as conn:
        conn.execute(CREATE_SQL)

        with logged(conn, "nextgen") as run:
            xw = Crosswalk(conn)
            now = utc_now_iso()
            rows = []
            missing = []

            for stat_type, mapping in METRICS.items():
                ngs = fetch(
                    nfl.load_nextgen_stats, seasons=[season], stat_type=stat_type
                )
                absent = [c for c in mapping if c not in ngs.columns]
                if absent:
                    missing.append(stat_type + ":" + ",".join(absent))

                for r in ngs.iter_rows(named=True):
                    pid = xw.get("gsis", clean_str(r.get("player_gsis_id")))
                    week = clean_int(r.get("week"))
                    # NGS carries season totals as week 0; skip them.
                    if pid is None or not week:
                        continue
                    for column, metric in mapping.items():
                        value = clean_float(r.get(column))
                        if value is None:
                            continue
                        rows.append(
                            {
                                "player_id": pid,
                                "season": season,
                                "week": week,
                                "metric": metric,
                                "value": value,
                                "updated_at": now,
                            }
                        )

            run.rows = upsert(
                conn,
                "nextgen_stat",
                rows,
                key=["player_id", "season", "week", "metric"],
            )
            run.note = "season {}".format(season)
            if missing:
                run.note += "; MISSING upstream columns: " + "; ".join(missing)


if __name__ == "__main__":
    main()
