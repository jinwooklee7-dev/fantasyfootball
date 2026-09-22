#!/usr/bin/env python3
"""Weekly roster status: who is on IR, who was inactive, who is on the practice squad.

This is the source the injury report does NOT have. load_injuries() carries
practice participation and the Out/Doubtful/Questionable game designation, and
nothing else -- a player on injured reserve simply stops appearing, which reads
identically to "healthy, nothing to report".

load_rosters_weekly() carries a status per player per week:

    ACT  active            RES  reserve / injured reserve
    INA  inactive          DEV  practice squad (development)
    PUP  phys. unable      SUS  suspended
    CUT  released          RET  retired            EXE  exempt list

Two things fall out of it. RES is the IR flag the start/sit page needs, and INA
is the gameday inactives list -- it is populated only for weeks that have been
played (Weeks 1-2 of 2026 carry ~200 each; Week 3, unplayed, carries none),
which is the signature of the 90-minutes-before-kickoff list. That also fills
injury_report.is_inactive, which was previously left NULL.

    uv run scripts/ingest_rosters.py [--season 2026]

Cron: daily, and again on Sunday inside the inactives window.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse

import nflreadpy as nfl

from ffdash.config import current_season
from ffdash.db import ensure_columns, session, upsert
from ffdash.ingestlog import logged
from ffdash.nflsource import clean_int, clean_str, fetch
from ffdash.players import Crosswalk
from ffdash.timeutil import utc_now_iso

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS roster_status (
  player_id   INTEGER NOT NULL REFERENCES player(player_id),
  season      INTEGER NOT NULL,
  week        INTEGER NOT NULL,
  team_abbr   TEXT,
  status      TEXT,          -- ACT | RES | INA | DEV | PUP | SUS | CUT | RET | EXE
  status_abbr TEXT,          -- league's finer-grained code, e.g. R48
  depth_pos   TEXT,
  jersey      INTEGER,        -- needed to resolve players named in play-by-play
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (player_id, season, week)
)
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()
    season = args.season or current_season()

    with session() as conn:
        conn.execute(CREATE_SQL)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_roster_week "
            "ON roster_status(season, week, team_abbr)"
        )
        ensure_columns(conn, "roster_status", {"jersey": "INTEGER"})
        # Play-by-play names players as TEAM-NUMBER-I.Lastname, so the jersey is
        # the join key back to a real player id.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_roster_jersey "
            "ON roster_status(season, week, team_abbr, jersey)"
        )

        with logged(conn, "rosters") as run:
            xw = Crosswalk(conn)
            rosters = fetch(nfl.load_rosters_weekly, seasons=[season])
            now = utc_now_iso()

            rows = []
            for r in rosters.iter_rows(named=True):
                pid = xw.get("gsis", r.get("gsis_id"))
                week = clean_int(r.get("week"))
                if pid is None or week is None:
                    continue
                rows.append(
                    {
                        "player_id": pid,
                        "season": season,
                        "week": week,
                        "team_abbr": clean_str(r.get("team")),
                        "status": clean_str(r.get("status")),
                        "status_abbr": clean_str(r.get("status_description_abbr")),
                        "depth_pos": clean_str(r.get("depth_chart_position")),
                        "jersey": clean_int(r.get("jersey_number")),
                        "updated_at": now,
                    }
                )

            run.rows = upsert(
                conn, "roster_status", rows, key=["player_id", "season", "week"]
            )

            # Backfill the inactives flag the injury feed cannot provide. Only
            # for player-weeks that already have an injury row; a player can be
            # inactive without ever appearing on an injury report.
            inactive = conn.execute(
                """
                UPDATE injury_report SET is_inactive = 1
                WHERE (player_id, season, week) IN (
                    SELECT player_id, season, week FROM roster_status
                    WHERE status = 'INA' AND season = ?
                )
                """,
                (season,),
            ).rowcount

            counts = {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT status, COUNT(*) FROM roster_status WHERE season = ? "
                    "GROUP BY status ORDER BY COUNT(*) DESC",
                    (season,),
                )
            }
            run.note = "season {}; {}; {} injury rows marked inactive".format(
                season,
                ", ".join(f"{k}={v}" for k, v in counts.items()),
                inactive,
            )
            if xw.miss_summary():
                run.note += "; " + xw.miss_summary()


if __name__ == "__main__":
    main()
