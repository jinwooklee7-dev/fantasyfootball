"""Parsing in-game injuries out of play-by-play descriptions.

The league writes them into the play text in two forms:

    SEA-14-S.Darnold was injured during the play.
    ** Injury Update: LAC-99-J.Caldwell has returned to the game.

A player is treated as having LEFT the game when he is injured on some play and
no return is recorded on any later play of the same game.

Identity: the description names a player as TEAM-NUMBER-I.Lastname, which is not
an id. Resolution is on (team, week, jersey number) from the weekly roster --
never on the name, per CLAUDE.md. The surname is compared only to verify the
match, and a disagreement is reported rather than trusted, because attributing
an injury to the wrong player is worse than missing one.
"""

from __future__ import annotations

import re

INJURED = re.compile(
    r"\b([A-Z]{2,3})-(\d{1,2})-([A-Za-z][A-Za-z.'\-]*)\s+was injured during the play"
)
RETURNED = re.compile(
    r"\b([A-Z]{2,3})-(\d{1,2})-([A-Za-z][A-Za-z.'\-]*)\s+has returned to the game"
)

# Generational suffixes sit where the surname would otherwise be.
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _tidy(text: str) -> str:
    return text.strip().lower().replace("'", "").replace("-", "").replace(".", "")


def desc_surname(fragment: str) -> str:
    """'S.Darnold' -> 'darnold'; 'Ma.Wilson' -> 'wilson'.

    Play-by-play abbreviates the forename, and stretches to two letters when a
    team has two players sharing an initial.
    """
    return _tidy(fragment.split(".")[-1])


def roster_surname(full_name: str) -> str:
    """'Sam Darnold' -> 'darnold'; 'Velus Jones Jr.' -> 'jones'."""
    parts = [p for p in _tidy(full_name).split() if p]
    while len(parts) > 1 and parts[-1] in SUFFIXES:
        parts.pop()
    return parts[-1] if parts else ""


def find_events(text: str) -> list[tuple[str, str, int, str]]:
    """Every injury event in one play description.

    Returns (kind, team, jersey, named) where kind is 'injured' or 'returned'.
    A single description can carry both -- a play on which one player goes down
    while another is announced as returning.
    """
    out = []
    for pattern, kind in ((INJURED, "injured"), (RETURNED, "returned")):
        for match in pattern.finditer(text or ""):
            out.append((kind, match.group(1), int(match.group(2)), match.group(3)))
    return out


def left_the_game(injured_play: int, returned_play: int | None) -> bool:
    """Did he stay off? True when no return follows the last injury."""
    if returned_play is None:
        return True
    return returned_play <= injured_play
