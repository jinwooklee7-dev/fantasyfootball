#!/usr/bin/env python3
"""Official injury reports.

A note on shape. The schema keys injury_report on (player, season, week,
report_date) so the Wed/Thu/Fri practice progression can be read back. nflverse
does NOT carry a report date -- load_injuries() is one row per player per week,
holding the latest practice status and game designation.

So report_date is the date we OBSERVED the row. Running this daily builds the
W/Th/F progression up naturally, one snapshot per day, and re-running on the
same day updates that day's row rather than duplicating it. History is thin to
begin with for the same reason; it accumulates from the day you start.

is_inactive stays NULL: the 90-minutes-before-kickoff inactives list is not in
this feed. It needs its own source and is out of scope for Phases 0-3.

    uv run scripts/ingest_injuries.py [--season 2025]

Cron: daily, after 07:00 UTC.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse
from datetime import datetime, timezone

import nflreadpy as nfl

from ffdash.config import current_season
from ffdash.db import session, upsert
from ffdash.ingestlog import logged
from ffdash.nflsource import clean_int, clean_str, fetch
from ffdash.players import Crosswalk
from ffdash.timeutil import utc_now_iso

# nflverse spells these out in full; the UI wants the short form.
PRACTICE = {
    "did not participate in practice": "DNP",
    "limited participation in practice": "Limited",
    "full participation in practice": "Full",
}

GAME_STATUS = {
    "out": "Out",
    "doubtful": "Doubtful",
    "questionable": "Questionable",
    "injured reserve": "Out",
    "reserve/injured": "Out",
}


def normalise(value, table):
    text = clean_str(value)
    if text is None:
        return None
    return table.get(text.lower(), text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()
    season = args.season or current_season()

    observed_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with session() as conn:
        with logged(conn, "injuries") as run:
            xw = Crosswalk(conn)
            inj = fetch(nfl.load_injuries, seasons=[season])
            now = utc_now_iso()

            rows = []
            for r in inj.iter_rows(named=True):
                pid = xw.get("gsis", r.get("gsis_id"))
                week = clean_int(r.get("week"))
                if pid is None or week is None:
                    continue
                practice = normalise(r.get("practice_status"), PRACTICE)
                game = normalise(r.get("report_status"), GAME_STATUS)
                body = clean_str(r.get("report_primary_injury")) or clean_str(
                    r.get("practice_primary_injury")
                )
                if practice is None and game is None and body is None:
                    continue
                rows.append(
                    {
                        "player_id": pid,
                        "season": season,
                        "week": week,
                        "report_date": observed_date,
                        "practice_status": practice,
                        "game_status": game,
                        "body_part": body,
                        "is_inactive": None,
                        "updated_at": now,
                    }
                )

            run.rows = upsert(
                conn,
                "injury_report",
                rows,
                key=["player_id", "season", "week", "report_date"],
            )
            run.note = "season {}; observed {}".format(season, observed_date)
            if xw.miss_summary():
                run.note += "; " + xw.miss_summary()


if __name__ == "__main__":
    main()
