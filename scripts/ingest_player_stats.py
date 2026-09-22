#!/usr/bin/env python3
"""Weekly player stats, including target share and PPR points.

Red zone and inside-10 touches are not in load_player_stats(); they are derived
from play-by-play here, because the usage block needs them and nothing else
carries them for free.

    uv run scripts/ingest_player_stats.py [--season 2025] [--corrections]

Cron: nightly, PLUS a separate Wed->Thu overnight entry with --corrections.
The NFL issues stat corrections after games are scored and nflverse recommends
re-pulling in that window. If our Week N numbers disagree with everyone else's,
this is why. See CLAUDE.md.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse

import nflreadpy as nfl
import polars as pl

from ffdash.config import current_season
from ffdash.db import session, upsert
from ffdash.ingestlog import logged
from ffdash.nflsource import clean_float, clean_int, clean_str, fetch
from ffdash.players import Crosswalk
from ffdash.timeutil import utc_now_iso


def red_zone_touches(
    season: int,
) -> tuple[dict[tuple[str, int], dict[str, int]], set[tuple[str, int]], list[str]]:
    """Red zone usage from play-by-play.

    Returns (touches, covered, warnings):
      touches  (gsis_id, week) -> {'rz': n, 'in10': n}
      covered  (team, week) pairs whose play-by-play looks trustworthy
      warnings human-readable notes about team-weeks we do not trust

    A 'touch' is a carry or a target. Two-point conversions are excluded; they
    carry no fantasy rushing/receiving credit in standard scoring.

    On coverage: nflverse play-by-play for the current season is a live feed and
    individual team-weeks arrive incomplete. A team that ran 80 plays but shows
    zero snaps inside the 20 has broken field-position data, not a red zone
    drought -- in 2026 Week 1-2 that was ATL, whose yardline never went below 15
    while every other team reached the 1-4. Writing 0 for those players would put
    a confidently wrong number on the start/sit page, so their touches stay NULL
    and render as "no data" instead.
    """
    pbp = fetch(nfl.load_pbp, seasons=[season])
    needed = {"yardline_100", "week", "posteam", "rusher_player_id", "receiver_player_id"}
    if not needed.issubset(pbp.columns):
        return {}, set(), ["play-by-play missing columns: {}".format(
            sorted(needed - set(pbp.columns))
        )]

    # A team-week is trusted only if its play-by-play shows SCRIMMAGE plays
    # inside the 20. Counting every play type is too lenient: ATL Week 1 2026
    # has exactly one play inside the 20 and it is an extra point, which would
    # wave through a team-week whose field position data is plainly broken.
    scrimmage = (pl.col("rush_attempt").fill_null(0) == 1) | (
        pl.col("pass_attempt").fill_null(0) == 1
    )
    coverage = (
        pbp.filter(pl.col("posteam").is_not_null() & pl.col("week").is_not_null())
        .group_by(["posteam", "week"])
        .agg(
            pl.len().alias("plays"),
            ((pl.col("yardline_100") <= 20) & scrimmage).sum().alias("rz_plays"),
        )
    )

    covered: set[tuple[str, int]] = set()
    warnings: list[str] = []
    for row in coverage.iter_rows(named=True):
        team = clean_str(row["posteam"])
        week = clean_int(row["week"])
        if team is None or week is None:
            continue
        plays = clean_int(row["plays"]) or 0
        rz_plays = clean_int(row["rz_plays"]) or 0
        if rz_plays > 0:
            covered.add((team, week))
        elif plays >= 30:
            # Enough plays to be a real game, yet never inside the 20.
            warnings.append("{} wk{}".format(team, week))

    plays = pbp.filter(
        pl.col("yardline_100").is_not_null()
        & (pl.col("yardline_100") <= 20)
        & (pl.col("two_point_attempt").fill_null(0) == 0)
    ).select(["week", "yardline_100", "rusher_player_id", "receiver_player_id"])

    out: dict[tuple[str, int], dict[str, int]] = {}
    for row in plays.iter_rows(named=True):
        week = clean_int(row["week"])
        if week is None:
            continue
        inside10 = row["yardline_100"] <= 10
        for key in ("rusher_player_id", "receiver_player_id"):
            pid = clean_str(row[key])
            if not pid:
                continue
            entry = out.setdefault((pid, week), {"rz": 0, "in10": 0})
            entry["rz"] += 1
            if inside10:
                entry["in10"] += 1
    return out, covered, warnings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument(
        "--corrections",
        action="store_true",
        help="Wed->Thu re-pull: stamps corrected_at on every row rewritten.",
    )
    args = ap.parse_args()
    season = args.season or current_season()
    source = "player_stats.corrections" if args.corrections else "player_stats"

    with session() as conn:
        with logged(conn, source) as run:
            xw = Crosswalk(conn)
            stats = fetch(nfl.load_player_stats, seasons=[season])
            rz, covered, rz_warnings = red_zone_touches(season)
            now = utc_now_iso()

            rows = []
            for r in stats.iter_rows(named=True):
                gsis = clean_str(r.get("player_id"))
                pid = xw.get("gsis", gsis)
                week = clean_int(r.get("week"))
                if pid is None or week is None:
                    continue

                team = clean_str(r.get("team"))
                # 0 when we trust this team-week's play-by-play, NULL when we do
                # not -- "no red zone work" and "no data" must not look alike.
                if (team, week) in covered:
                    touches = rz.get((gsis, week), {"rz": 0, "in10": 0})
                else:
                    touches = {}
                rows.append(
                    {
                        "player_id": pid,
                        "season": season,
                        "week": week,
                        "team_abbr": team,
                        "opponent": clean_str(r.get("opponent_team")),
                        "targets": clean_int(r.get("targets")),
                        "receptions": clean_int(r.get("receptions")),
                        "receiving_yards": clean_float(r.get("receiving_yards")),
                        "receiving_tds": clean_int(r.get("receiving_tds")),
                        "carries": clean_int(r.get("carries")),
                        "rushing_yards": clean_float(r.get("rushing_yards")),
                        "rushing_tds": clean_int(r.get("rushing_tds")),
                        "attempts": clean_int(r.get("attempts")),
                        "completions": clean_int(r.get("completions")),
                        "passing_yards": clean_float(r.get("passing_yards")),
                        "passing_tds": clean_int(r.get("passing_tds")),
                        "interceptions": clean_int(r.get("passing_interceptions")),
                        "target_share": clean_float(r.get("target_share")),
                        "air_yards_share": clean_float(r.get("air_yards_share")),
                        "rz_touches": touches.get("rz"),
                        "inside10_touches": touches.get("in10"),
                        "fantasy_ppr": clean_float(r.get("fantasy_points_ppr")),
                        "corrected_at": now if args.corrections else None,
                        "updated_at": now,
                    }
                )

            if args.corrections:
                run.rows = upsert(
                    conn, "player_week_stat", rows, key=["player_id", "season", "week"]
                )
            else:
                # A normal nightly run must not clear a corrected_at stamp set by
                # the Wed->Thu pull, so leave that column out of the update.
                run.rows = _upsert_preserving_corrections(conn, rows)

            note = ["season {}".format(season), "rz from pbp"]
            if rz_warnings:
                note.append(
                    "rz UNTRUSTED (no red zone plays in feed): "
                    + ", ".join(sorted(rz_warnings))
                )
            if xw.miss_summary():
                note.append(xw.miss_summary())
            run.note = "; ".join(note)


def _upsert_preserving_corrections(conn, rows: list[dict]) -> int:
    for r in rows:
        r.pop("corrected_at", None)
    return upsert(conn, "player_week_stat", rows, key=["player_id", "season", "week"])


if __name__ == "__main__":
    main()
