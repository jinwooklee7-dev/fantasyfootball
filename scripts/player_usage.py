#!/usr/bin/env python3
"""Trailing usage for one player. The Phase 1 acceptance check.

    uv run scripts/player_usage.py "Puka Nacua"
    uv run scripts/player_usage.py "Bijan Robinson" --weeks 6

Resolves the name ONCE, here at the edge where a human typed it, into our
internal player_id. Everything downstream joins on the id. See CLAUDE.md.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse
import sys

from ffdash.config import current_season
from ffdash.db import session
from ffdash.queries import find_players, trailing_usage


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--weeks", type=int, default=6)
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()
    season = args.season or current_season()

    with session() as conn:
        matches = find_players(conn, args.name)
        if not matches:
            print("no player matching {!r}".format(args.name))
            sys.exit(1)
        if len(matches) > 1:
            print("ambiguous, {} matches:".format(len(matches)))
            for m in matches[:10]:
                print("  {}  {}  {}".format(m["player_id"], m["display_name"], m["position"]))
            sys.exit(1)

        player = matches[0]
        rows = trailing_usage(conn, player["player_id"], season, args.weeks)

        print(
            "{}  {}  {}  (internal id {})".format(
                player["display_name"],
                player["position"] or "?",
                player["current_team"] or "FA",
                player["player_id"],
            )
        )
        if not rows:
            print("  no weeks recorded for season {}".format(season))
            return

        header = "  wk  opp   snap%   tgt  tgt%   rec   recyd  car  rushyd   rz  i10    ppr"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for r in rows:
            print(
                "  {:>2}  {:<4}  {:>5}  {:>4}  {:>4}  {:>4}  {:>6}  {:>3}  {:>6}  {:>3}  {:>3}  {:>5}".format(
                    r["week"],
                    r["opponent"] or "-",
                    _pct(r["offense_pct"]),
                    _num(r["targets"]),
                    _pct(r["target_share"]),
                    _num(r["receptions"]),
                    _num(r["receiving_yards"]),
                    _num(r["carries"]),
                    _num(r["rushing_yards"]),
                    _num(r["rz_touches"]),
                    _num(r["inside10_touches"]),
                    _num(r["fantasy_ppr"]),
                )
            )


def _pct(value) -> str:
    return "-" if value is None else "{:.0f}%".format(float(value) * 100)


def _num(value) -> str:
    if value is None:
        return "-"
    f = float(value)
    return str(int(f)) if f == int(f) else "{:.1f}".format(f)


if __name__ == "__main__":
    main()
