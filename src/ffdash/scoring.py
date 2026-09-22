"""Fantasy scoring formats.

nflverse gives us full-PPR points. The other common formats differ from it by
exactly the reception bonus, so they are a subtraction rather than a
re-implementation of the whole scoring table:

    standard   = ppr - 1.0 * receptions
    half PPR   = ppr - 0.5 * receptions
    full PPR   = ppr

That matters. Recomputing points from components would mean duplicating the
league's scoring rules -- two-point conversions, return touchdowns, fumble
recoveries, the lot -- and quietly disagreeing with everyone else by a point or
two. Deriving from the number nflverse already computed cannot drift.

Anything more exotic (six-point passing touchdowns, tight-end premium, bonuses
for long plays) genuinely does need the components, and is not supported here.
"""

from __future__ import annotations

FORMATS = {
    "ppr": {"label": "PPR", "per_reception": 1.0, "long": "Full PPR"},
    "half": {"label": "½ PPR", "per_reception": 0.5, "long": "Half PPR"},
    "std": {"label": "Std", "per_reception": 0.0, "long": "Standard (no PPR)"},
}

DEFAULT_FORMAT = "ppr"


def normalise(name: str | None) -> str:
    key = (name or "").strip().lower()
    aliases = {
        "full": "ppr",
        "1ppr": "ppr",
        "0.5ppr": "half",
        "half_ppr": "half",
        "halfppr": "half",
        "standard": "std",
        "non-ppr": "std",
        "nonppr": "std",
        "none": "std",
    }
    key = aliases.get(key, key)
    return key if key in FORMATS else DEFAULT_FORMAT


def per_reception(fmt: str) -> float:
    return FORMATS[normalise(fmt)]["per_reception"]


def adjust(ppr_points, receptions, fmt: str = DEFAULT_FORMAT):
    """Convert stored full-PPR points into another format.

    Returns None when either input is missing, so a player with no stat line
    renders as a dash instead of silently scoring zero.
    """
    if ppr_points is None:
        return None
    bonus = per_reception(fmt)
    if bonus == 1.0:
        return float(ppr_points)
    if receptions is None:
        # No reception count means we cannot convert. Full PPR is what we hold,
        # so returning it unchanged would overstate every other format.
        return None
    return float(ppr_points) - (1.0 - bonus) * float(receptions)


def label(fmt: str) -> str:
    return FORMATS[normalise(fmt)]["label"]


def long_label(fmt: str) -> str:
    return FORMATS[normalise(fmt)]["long"]
