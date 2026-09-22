#!/usr/bin/env python3
"""Games, kickoff times and the free betting lines.

load_schedules() carries spread_line and total_line -- that is our free game
odds source, stored in odds_game with source='nflverse'. Implied team totals
are computed here, not fetched.

    uv run scripts/ingest_schedules.py [--season 2025]

Cron: every 15 minutes in season (lines move).
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse
from datetime import datetime

import nflreadpy as nfl
import polars as pl

from ffdash.config import current_season
from ffdash.db import session, upsert
from ffdash.ingestlog import logged
from ffdash.nflsource import clean_float, clean_int, clean_str, fetch
from ffdash.odds import implied_totals
from ffdash.timeutil import to_utc_iso, utc_now_iso

# nflverse gives local kickoff date + time; the stadium's zone turns it into UTC.
from zoneinfo import ZoneInfo


def kickoff_utc(row: dict, tz_by_stadium: dict[str, str]) -> str | None:
    day = clean_str(row.get("gameday"))
    clock = clean_str(row.get("gametime"))
    if not day:
        return None
    tz = tz_by_stadium.get(clean_str(row.get("stadium_id")) or "", "America/New_York")
    try:
        naive = datetime.strptime(f"{day} {clock or '13:00'}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    try:
        local = naive.replace(tzinfo=ZoneInfo(tz))
    except Exception:
        local = naive.replace(tzinfo=ZoneInfo("America/New_York"))
    return to_utc_iso(local)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()
    season = args.season or current_season()

    with session() as conn:
        tz_by_stadium = {
            r[0]: r[1] for r in conn.execute("SELECT stadium_id, timezone FROM stadium")
        }
        known_stadiums = set(tz_by_stadium)
        known_teams = {
            r[0] for r in conn.execute("SELECT team_abbr FROM team")
        }

        with logged(conn, "schedules") as run:
            sched = fetch(nfl.load_schedules).filter(pl.col("season") == season)
            now = utc_now_iso()

            games, odds = [], []
            skipped_teams = set()
            unknown_stadiums = set()

            for r in sched.iter_rows(named=True):
                gid = clean_str(r.get("game_id"))
                home = clean_str(r.get("home_team"))
                away = clean_str(r.get("away_team"))
                if not gid or not home or not away:
                    continue
                if home not in known_teams or away not in known_teams:
                    skipped_teams.add(home if home not in known_teams else away)
                    continue

                sid = clean_str(r.get("stadium_id"))
                if sid and sid not in known_stadiums:
                    unknown_stadiums.add(sid)
                    sid = None

                games.append(
                    {
                        "game_id": gid,
                        "season": clean_int(r.get("season")),
                        "week": clean_int(r.get("week")),
                        "season_type": clean_str(r.get("game_type")) or "REG",
                        "kickoff_utc": kickoff_utc(r, tz_by_stadium),
                        "home_team": home,
                        "away_team": away,
                        "stadium_id": sid,
                        "roof_state": clean_str(r.get("roof")),
                        "home_score": clean_int(r.get("home_score")),
                        "away_score": clean_int(r.get("away_score")),
                        "updated_at": now,
                    }
                )

                spread = clean_float(r.get("spread_line"))
                total = clean_float(r.get("total_line"))
                if spread is None and total is None:
                    continue
                home_imp, away_imp = implied_totals(spread, total)
                odds.append(
                    {
                        "game_id": gid,
                        "source": "nflverse",
                        "book": "consensus",
                        "captured_at": now,
                        "spread_line": spread,
                        "total_line": total,
                        "home_implied": home_imp,
                        "away_implied": away_imp,
                    }
                )

            run.rows = upsert(conn, "game", games, key=["game_id"])
            # Odds are a time series: a new captured_at each run preserves line
            # movement. Only write when the numbers actually changed.
            written = write_odds_if_changed(conn, odds)

            notes = [f"{written} odds snapshots"]
            if unknown_stadiums:
                notes.append(f"unmapped stadium_ids: {sorted(unknown_stadiums)}")
            if skipped_teams:
                notes.append(f"unknown teams skipped: {sorted(skipped_teams)}")
            run.note = "; ".join(notes)


def write_odds_if_changed(conn, odds: list[dict]) -> int:
    """Append an odds row only when spread or total moved.

    Running every 15 minutes would otherwise write 96 identical rows a day and
    bury the line movement that is the actual signal.
    """
    written = 0
    for row in odds:
        prev = conn.execute(
            "SELECT spread_line, total_line FROM odds_game "
            "WHERE game_id = ? AND source = ? ORDER BY captured_at DESC LIMIT 1",
            (row["game_id"], row["source"]),
        ).fetchone()
        if prev and (
            _same(prev["spread_line"], row["spread_line"])
            and _same(prev["total_line"], row["total_line"])
        ):
            continue
        upsert(conn, "odds_game", [row], key=["game_id", "source", "book", "captured_at"])
        written += 1
    return written


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) < 1e-6


if __name__ == "__main__":
    main()
