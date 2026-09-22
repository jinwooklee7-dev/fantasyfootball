#!/usr/bin/env python3
"""Build the player crosswalk. Run this before any stats ingest.

Combines load_players() (authoritative for name/position/team, carries gsis and
pfr ids) with load_ff_playerids() (the fantasy-side crosswalk: sleeper, yahoo,
espn, fantasypros...). Every external id lands in player_xref pointing at our
own stable internal player_id.

    uv run scripts/ingest_players.py

Cron: weekly.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
from collections import defaultdict

import nflreadpy as nfl

from ffdash.db import scalar, session
from ffdash.ingestlog import logged
from ffdash.nflsource import clean_str, fetch
from ffdash.players import ID_COLUMNS, next_player_id, resolve_or_create


def collect_identifiers(row: dict) -> dict[str, str]:
    out = {}
    for column, source in ID_COLUMNS.items():
        value = clean_str(row.get(column))
        if value:
            # Some id columns arrive as floats ('12345.0') after a parquet round trip.
            if value.endswith(".0"):
                value = value[:-2]
            out[source] = value
    return out


def main() -> None:
    with session() as conn:
        with logged(conn, "players") as run:
            players = fetch(nfl.load_players)
            ffids = fetch(nfl.load_ff_playerids)

            # Index the fantasy crosswalk by gsis so we can merge its extra ids in.
            ff_by_gsis: dict[str, dict] = {}
            for r in ffids.iter_rows(named=True):
                g = clean_str(r.get("gsis_id"))
                if g:
                    ff_by_gsis[g] = r

            next_id = [next_player_id(conn)]
            written = 0
            merged = 0

            for r in players.iter_rows(named=True):
                name = clean_str(r.get("display_name"))
                gsis = clean_str(r.get("gsis_id"))
                if not name or not gsis:
                    continue

                identifiers = collect_identifiers(r)
                extra = ff_by_gsis.get(gsis)
                if extra:
                    merged += 1
                    identifiers.update(collect_identifiers(extra))

                pid = resolve_or_create(
                    conn,
                    identifiers=identifiers,
                    display_name=name,
                    position=clean_str(r.get("position"))
                    or clean_str(r.get("ngs_position")),
                    team=clean_str(r.get("latest_team")),
                    next_id=next_id,
                )
                if pid is not None:
                    written += 1

            run.rows = written
            by_source: dict[str, int] = defaultdict(int)
            for source, count in conn.execute(
                "SELECT source, COUNT(*) FROM player_xref GROUP BY source"
            ):
                by_source[source] = count
            run.note = (
                f"{merged} matched to ff_playerids; xref: "
                + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items()))
            )

        print(f"  players: {scalar(conn, 'SELECT COUNT(*) FROM player')}")
        print(f"  xref rows: {scalar(conn, 'SELECT COUNT(*) FROM player_xref')}")


if __name__ == "__main__":
    main()
