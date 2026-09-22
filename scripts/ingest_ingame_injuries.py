#!/usr/bin/env python3
"""In-game injuries, parsed out of play-by-play descriptions.

The gap this fills: a player who leaves a game hurt appears in NO other feed we
ingest. The weekly injury report covers practice and game designations, which
are published before kickoff. Roster status covers IR and inactives. A back who
walks off in the second quarter and never returns shows up in none of them --
his snap count may even look normal if he went down late.

The league's own play descriptions carry it, in two forms:

    SEA-14-S.Darnold was injured during the play.
    ** Injury Update: LAC-99-J.Caldwell has returned to the game.

So a player is treated as having LEFT the game when he is injured on some play
and no return is recorded on any later play of that game.

On identity: the description names a player as TEAM-NUMBER-I.Lastname, which is
not an id. We resolve on (team, week, jersey number) from the weekly roster --
never on the name, per CLAUDE.md. The surname in the description is used only to
verify the match, and disagreements are reported rather than trusted.

    uv run scripts/ingest_ingame_injuries.py [--season 2026]

Cron: nightly, after the play-by-play pull.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse
import re

import nflreadpy as nfl
import polars as pl

from ffdash.config import current_season
from ffdash.ingame import (
    desc_surname,
    find_events,
    left_the_game,
    roster_surname,
)
from ffdash.db import session, upsert
from ffdash.ingestlog import logged
from ffdash.nflsource import clean_int, clean_str, fetch
from ffdash.timeutil import utc_now_iso

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS ingame_injury (
  player_id   INTEGER NOT NULL REFERENCES player(player_id),
  season      INTEGER NOT NULL,
  week        INTEGER NOT NULL,
  game_id     TEXT,
  injured_play INTEGER,        -- play_id of the last injury on this player
  returned     INTEGER NOT NULL DEFAULT 0,
  left_game    INTEGER NOT NULL DEFAULT 0,
  detail       TEXT,           -- the play description, trimmed
  updated_at   TEXT NOT NULL,
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

        with logged(conn, "ingame_injuries") as run:
            # (team, week, jersey) -> (player_id, surname)
            roster: dict[tuple[str, int, int], tuple[int, str]] = {}
            for r in conn.execute(
                """
                SELECT r.team_abbr, r.week, r.jersey, r.player_id, p.display_name
                FROM roster_status r JOIN player p ON p.player_id = r.player_id
                WHERE r.season = ? AND r.jersey IS NOT NULL
                """,
                (season,),
            ):
                roster[(r["team_abbr"], r["week"], r["jersey"])] = (
                    r["player_id"],
                    roster_surname(r["display_name"]),
                )
            if not roster:
                run.note = "no roster jerseys; run ingest_rosters.py first"
                return

            pbp = fetch(nfl.load_pbp, seasons=[season])
            if "desc" not in pbp.columns:
                run.note = "play-by-play has no description column"
                return

            plays = pbp.filter(
                pl.col("desc").is_not_null()
                & pl.col("desc").str.contains("(?i)injur")
            ).select(["game_id", "week", "play_id", "desc"])

            # Per player-game: the latest injury, and the latest return.
            injured: dict[tuple[int, int], dict] = {}
            returned: dict[tuple[int, int], int] = {}
            unresolved = 0
            mismatched = []

            for row in plays.iter_rows(named=True):
                week = clean_int(row["week"])
                play_id = clean_int(row["play_id"]) or 0
                text = row["desc"] or ""
                if week is None:
                    continue

                for kind, team, number, named in find_events(text):
                        entry = roster.get((team, week, number))
                        if entry is None:
                            unresolved += 1
                            continue
                        player_id, expected = entry
                        if desc_surname(named) != expected:
                            # Jersey and name disagree: report it rather than
                            # silently attributing an injury to the wrong player.
                            mismatched.append(
                                f"{team}-{number} desc={named} roster={expected}"
                            )
                            continue

                        key = (player_id, week)
                        if kind == "injured":
                            previous = injured.get(key)
                            if previous is None or play_id >= previous["injured_play"]:
                                injured[key] = {
                                    "game_id": clean_str(row["game_id"]),
                                    "injured_play": play_id,
                                    "detail": text.strip()[:400],
                                }
                        else:
                            returned[key] = max(returned.get(key, 0), play_id)

            now = utc_now_iso()
            rows = []
            left_count = 0
            for (player_id, week), info in injured.items():
                came_back = not left_the_game(
                    info["injured_play"], returned.get((player_id, week))
                )
                if not came_back:
                    left_count += 1
                rows.append(
                    {
                        "player_id": player_id,
                        "season": season,
                        "week": week,
                        "game_id": info["game_id"],
                        "injured_play": info["injured_play"],
                        "returned": 1 if came_back else 0,
                        "left_game": 0 if came_back else 1,
                        "detail": info["detail"],
                        "updated_at": now,
                    }
                )

            run.rows = upsert(
                conn, "ingame_injury", rows, key=["player_id", "season", "week"]
            )
            note = [
                "season {}".format(season),
                "{} injured, {} did not return".format(len(rows), left_count),
            ]
            if unresolved:
                note.append("{} unresolved jersey/team".format(unresolved))
            if mismatched:
                note.append(
                    "{} jersey/name mismatches: {}".format(
                        len(mismatched), "; ".join(sorted(set(mismatched))[:3])
                    )
                )
            run.note = "; ".join(note)


if __name__ == "__main__":
    main()
