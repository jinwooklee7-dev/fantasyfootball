"""Comparing people's names across sources, without ever joining on them.

Two places have to decide "is this the same person?" from free text:
play-by-play injury attribution (TEAM-NUMBER-I.Lastname) and matching a beat
writer to a Bluesky profile. Both are verification, never a join key --
CLAUDE.md is explicit that names collide and are punctuated differently by
every source.

`same_person` is deliberately a gate rather than a score. An earlier version of
the beat-writer matcher added points for followers and domain verification, so
an account with enough of both could outscore the identity check entirely. It
confidently matched three different Johns to John Burn-Murdoch, three different
Mikes to Mike Golic Jr, and both "Clarence Hill Jr." and "Paul Dehner Jr." to an
unrelated journalist whose handle ends in "jr". Evidence may rank candidates; it
may never promote one that fails the name check.
"""

from __future__ import annotations

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "jnr", "snr"}


def tidy(text: str) -> str:
    """Lowercase, and strip the punctuation that varies between sources."""
    out = (text or "").strip().lower()
    for ch in ".'`-_,":
        out = out.replace(ch, "")
    return " ".join(out.split())


def tokens(full_name: str) -> list[str]:
    """Name parts, with generational suffixes removed."""
    parts = [p for p in tidy(full_name).split() if p]
    while len(parts) > 1 and parts[-1] in SUFFIXES:
        parts.pop()
    return parts


def surname(full_name: str) -> str:
    """'Velus Jones Jr.' -> 'jones'. Never 'jr'."""
    parts = tokens(full_name)
    return parts[-1] if parts else ""


def forename(full_name: str) -> str:
    parts = tokens(full_name)
    return parts[0] if parts else ""


def abbreviated_surname(fragment: str) -> str:
    """'S.Darnold' -> 'darnold'; 'Ma.Wilson' -> 'wilson'.

    Play-by-play abbreviates the forename, stretching to two letters when a
    team has two players sharing an initial.
    """
    return tidy(fragment.split(".")[-1]) if fragment else ""


def same_person(claimed: str, candidate: str) -> bool:
    """Is `candidate` plausibly the person named by `claimed`?

    Surname must match, and the forenames must be consistent -- the same word,
    or one a literal prefix of the other ("m." vs "mike"). Surname alone is far
    too loose.

    Nicknames are deliberately NOT resolved: "Mike Klis" and "Michael Klis"
    return False, because "michael" does not start with "mike". That is a false
    negative, and the right way to be wrong here. The output of this is a
    worksheet a person reviews, so a miss costs one manual check, while a false
    positive puts someone else's account into a feed you act on with money.
    """
    want, got = tokens(claimed), tokens(candidate)
    if not want or not got:
        return False
    if surname(claimed) != surname(candidate):
        return False
    if len(want) < 2 or len(got) < 2:
        # Only a surname on one side: too weak to assert identity.
        return False
    a, b = want[0], got[0]
    if a == b:
        return True
    # A literal prefix: "m" vs "mike". Not nicknames -- see the docstring.
    shorter, longer = sorted((a, b), key=len)
    return bool(shorter) and longer.startswith(shorter)
