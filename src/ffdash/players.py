"""The player crosswalk. Phase 1 priority #1.

External sources do not agree on player IDs: player_stats and injuries key on
gsis_id, snap counts key on pfr_player_id, odds providers use their own. We
mint one stable internal player_id and map every external id to it.

Never join on name. Names collide, change, and are punctuated differently by
every source. See CLAUDE.md.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .db import upsert
from .nflsource import clean_str
from .timeutil import utc_now_iso

# Columns in load_players() / load_ff_playerids() that are external identifiers,
# mapped to the source name we store in player_xref.
ID_COLUMNS = {
    "gsis_id": "gsis",
    "pfr_id": "pfr",
    "espn_id": "espn",
    "sleeper_id": "sleeper",
    "yahoo_id": "yahoo",
    "sportradar_id": "sportradar",
    "fantasypros_id": "fantasypros",
    "rotowire_id": "rotowire",
    "mfl_id": "mfl",
    "pff_id": "pff",
    "nfl_id": "nfl",
    "esb_id": "esb",
    "smart_id": "smart",
    "otc_id": "otc",
    "cbs_id": "cbs",
    "fantasy_data_id": "fantasy_data",
}


class Crosswalk:
    """In-memory (source, source_id) -> internal player_id lookup.

    Load once per ingest run; resolving row by row against SQLite is needlessly
    slow when the whole map is a few hundred kilobytes.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._map: dict[tuple[str, str], int] = {}
        for source, source_id, pid in conn.execute(
            "SELECT source, source_id, player_id FROM player_xref"
        ):
            self._map[(source, str(source_id))] = pid
        self.misses: dict[str, int] = {}

    def get(self, source: str, source_id: Any) -> int | None:
        sid = clean_str(source_id)
        if sid is None:
            return None
        hit = self._map.get((source, sid))
        if hit is None:
            self.misses[source] = self.misses.get(source, 0) + 1
        return hit

    def miss_summary(self) -> str | None:
        if not self.misses:
            return None
        return "unresolved ids: " + ", ".join(
            f"{k}={v}" for k, v in sorted(self.misses.items())
        )

    def __len__(self) -> int:
        return len(self._map)


def next_player_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(player_id), 0) FROM player").fetchone()
    return int(row[0]) + 1


def resolve_or_create(
    conn: sqlite3.Connection,
    identifiers: dict[str, str],
    display_name: str,
    position: str | None,
    team: str | None,
    next_id: list[int],
) -> int | None:
    """Find the internal id for a player by any of their external ids, or mint one.

    `identifiers` maps source name -> source id. If two of the supplied ids
    already point at different internal players we keep the first and leave the
    conflict alone rather than silently merging two people.
    """
    identifiers = {s: i for s, i in identifiers.items() if i}
    if not identifiers:
        return None

    found: int | None = None
    for source, source_id in identifiers.items():
        row = conn.execute(
            "SELECT player_id FROM player_xref WHERE source = ? AND source_id = ?",
            (source, source_id),
        ).fetchone()
        if row:
            found = int(row[0])
            break

    if found is None:
        found = next_id[0]
        next_id[0] += 1

    now = utc_now_iso()
    upsert(
        conn,
        "player",
        [
            {
                "player_id": found,
                "display_name": display_name,
                "position": position,
                "current_team": team,
                "updated_at": now,
            }
        ],
        key=["player_id"],
    )
    upsert(
        conn,
        "player_xref",
        [
            {"player_id": found, "source": s, "source_id": i}
            for s, i in identifiers.items()
        ],
        key=["source", "source_id"],
    )
    return found
