#!/usr/bin/env python3
"""Import a Sleeper league: its scoring settings and every roster.

Sleeper's read API is public -- no key, no auth, no account linkage. We only
ever read.

Why the scoring settings matter more than the rosters: nflverse gives us
full-PPR points, and most formats differ from that by exactly the reception
bonus. A real league usually does not. Bowers' Castle pays 5 points for a
passing touchdown (nflverse assumes 4) and a 0.75 tight end premium, which makes
the stored figure wrong for every QB and every TE on the page. With the settings
imported we compute from components instead. See ffdash.sleeper.

Players are resolved through the crosswalk on Sleeper's own id -- never on
name. load_ff_playerids() supplies that mapping.

    uv run scripts/ingest_sleeper.py 1312030763641757696
    uv run scripts/ingest_sleeper.py --league-id 1312030763641757696

Cron: weekly is plenty. Rosters change on waivers; scoring settings almost
never change mid-season.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse
import json
import os

import httpx

from ffdash.db import session, upsert
from ffdash.ingestlog import logged
from ffdash.nflsource import clean_int, clean_str
from ffdash.players import Crosswalk
from ffdash.sleeper import describe, is_superflex, unsupported_settings
from ffdash.timeutil import utc_now_iso

API = "https://api.sleeper.app/v1"

CREATE_LEAGUE = """
CREATE TABLE IF NOT EXISTS sleeper_league (
  league_id   TEXT PRIMARY KEY,
  name        TEXT,
  season      TEXT,
  total_teams INTEGER,
  scoring     TEXT,          -- JSON, the league's own settings
  positions   TEXT,          -- JSON roster slots
  superflex   INTEGER NOT NULL DEFAULT 0,
  updated_at  TEXT NOT NULL
)
"""

CREATE_ROSTER = """
CREATE TABLE IF NOT EXISTS sleeper_roster (
  league_id   TEXT NOT NULL REFERENCES sleeper_league(league_id),
  roster_id   INTEGER NOT NULL,
  owner_name  TEXT,
  team_name   TEXT,
  wins        INTEGER,
  losses      INTEGER,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (league_id, roster_id)
)
"""

CREATE_SLOT = """
CREATE TABLE IF NOT EXISTS sleeper_roster_player (
  league_id   TEXT NOT NULL,
  roster_id   INTEGER NOT NULL,
  player_id   INTEGER NOT NULL REFERENCES player(player_id),
  sleeper_id  TEXT NOT NULL,
  starter     INTEGER NOT NULL DEFAULT 0,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (league_id, roster_id, player_id)
)
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("league_id", nargs="?", default=None)
    ap.add_argument("--league-id", dest="flag_id", default=None)
    args = ap.parse_args()
    league_id = args.league_id or args.flag_id or os.getenv("SLEEPER_LEAGUE_ID")
    if not league_id:
        ap.error("pass a league id, or set SLEEPER_LEAGUE_ID in .env")

    with session() as conn:
        for sql in (CREATE_LEAGUE, CREATE_ROSTER, CREATE_SLOT):
            conn.execute(sql)

        with logged(conn, "sleeper") as run:
            now = utc_now_iso()
            with httpx.Client(timeout=30) as client:
                league = client.get(f"{API}/league/{league_id}").json()
                if not league:
                    raise ValueError(f"league {league_id} not found")
                rosters = client.get(f"{API}/league/{league_id}/rosters").json() or []
                users = client.get(f"{API}/league/{league_id}/users").json() or []

            by_user = {u["user_id"]: u for u in users}
            scoring = league.get("scoring_settings") or {}
            positions = league.get("roster_positions") or []

            upsert(
                conn,
                "sleeper_league",
                [
                    {
                        "league_id": league_id,
                        "name": clean_str(league.get("name")),
                        "season": clean_str(league.get("season")),
                        "total_teams": clean_int(league.get("total_rosters")),
                        "scoring": json.dumps(scoring, sort_keys=True),
                        "positions": json.dumps(positions),
                        "superflex": 1 if is_superflex(positions) else 0,
                        "updated_at": now,
                    }
                ],
                key=["league_id"],
            )

            xw = Crosswalk(conn)
            roster_rows, slot_rows = [], []
            unresolved = 0

            for r in rosters:
                roster_id = clean_int(r.get("roster_id"))
                if roster_id is None:
                    continue
                user = by_user.get(r.get("owner_id")) or {}
                settings = r.get("settings") or {}
                roster_rows.append(
                    {
                        "league_id": league_id,
                        "roster_id": roster_id,
                        "owner_name": clean_str(user.get("display_name")),
                        "team_name": clean_str(
                            (user.get("metadata") or {}).get("team_name")
                        )
                        or clean_str(user.get("display_name")),
                        "wins": clean_int(settings.get("wins")),
                        "losses": clean_int(settings.get("losses")),
                        "updated_at": now,
                    }
                )

                starters = {str(s) for s in (r.get("starters") or []) if s}
                for sleeper_id in r.get("players") or []:
                    sid = clean_str(sleeper_id)
                    if not sid:
                        continue
                    player_id = xw.get("sleeper", sid)
                    if player_id is None:
                        unresolved += 1
                        continue
                    slot_rows.append(
                        {
                            "league_id": league_id,
                            "roster_id": roster_id,
                            "player_id": player_id,
                            "sleeper_id": sid,
                            "starter": 1 if sid in starters else 0,
                            "updated_at": now,
                        }
                    )

            upsert(conn, "sleeper_roster", roster_rows, key=["league_id", "roster_id"])
            # Drop players who are no longer rostered anywhere in this league,
            # so a dropped player does not linger as someone's asset forever.
            conn.execute(
                "DELETE FROM sleeper_roster_player WHERE league_id = ?", (league_id,)
            )
            run.rows = upsert(
                conn,
                "sleeper_roster_player",
                slot_rows,
                key=["league_id", "roster_id", "player_id"],
            )

            note = [
                "{} ({})".format(league.get("name"), describe(scoring)),
                "{} rosters".format(len(roster_rows)),
                "{} rostered players".format(len(slot_rows)),
            ]
            if is_superflex(positions):
                note.append("SUPERFLEX")
            if unresolved:
                note.append("{} unresolved sleeper ids".format(unresolved))
            missing = unsupported_settings(scoring)
            if missing:
                note.append("UNSUPPORTED scoring: " + ", ".join(missing))
            run.note = "; ".join(note)


if __name__ == "__main__":
    main()
