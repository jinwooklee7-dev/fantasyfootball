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

from .names import abbreviated_surname as desc_surname  # noqa: F401
from .names import surname as roster_surname  # noqa: F401

INJURED = re.compile(
    r"\b([A-Z]{2,3})-(\d{1,2})-([A-Za-z][A-Za-z.'\-]*)\s+was injured during the play"
)
RETURNED = re.compile(
    r"\b([A-Z]{2,3})-(\d{1,2})-([A-Za-z][A-Za-z.'\-]*)\s+has returned to the game"
)

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
