#!/usr/bin/env python3
"""Run every ingest in dependency order. For a first build, or a manual catch-up.

The crosswalk goes first: nothing else can resolve a player without it.

    uv run scripts/refresh_all.py
    uv run scripts/refresh_all.py --season 2025

A failure in one ingest does not stop the others -- a broken weather API should
not cost you the injury report. The exit code is non-zero if anything failed.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Order matters: players (the crosswalk) before anything that resolves a player,
# schedules before weather (weather needs kickoff times and stadiums).
STEPS = [
    ("init_db.py", []),
    # Skips anything already cached, so this is a no-op after the first run.
    ("fetch_logos.py", []),
    ("ingest_players.py", ["--season"]),
    ("ingest_schedules.py", ["--season"]),
    ("ingest_player_stats.py", ["--season"]),
    ("ingest_snaps.py", ["--season"]),
    ("ingest_depth_charts.py", ["--season"]),
    ("ingest_injuries.py", ["--season"]),
    ("ingest_rosters.py", ["--season"]),
    # Needs rosters first: play-by-play names players by jersey number.
    ("ingest_ingame_injuries.py", ["--season"]),
    ("ingest_nextgen.py", ["--season"]),
    ("ingest_weather.py", ["--season"]),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()

    failed = []
    for script, accepts in STEPS:
        cmd = [sys.executable, str(REPO / "scripts" / script)]
        if args.season and "--season" in accepts:
            cmd += ["--season", str(args.season)]

        print("\n=== {} ===".format(script))
        result = subprocess.run(cmd, cwd=REPO)
        if result.returncode != 0:
            failed.append(script)
            print("  !! {} exited {}".format(script, result.returncode))

    print("\n" + "=" * 46)
    if failed:
        print("FAILED: {}".format(", ".join(failed)))
        sys.exit(1)
    print("all ingests ok")


if __name__ == "__main__":
    main()
