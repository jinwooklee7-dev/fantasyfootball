"""Sleeper league scoring, computed from the league's own settings.

Everywhere else we derive points from nflverse's `fantasy_points_ppr`, because
half-PPR and standard differ from it by exactly the reception bonus. That trick
stops working the moment a league changes anything else, and real leagues do:

    Bowers' Castle (12-team):  pass_td = 5.0   (nflverse assumes 4)
                               bonus_rec_te = 0.75  (tight end premium)
                               fum_lost = -2.0

Against that league, the stored PPR figure is wrong for every quarterback and
every tight end. So for a league we compute from components and ignore the
precomputed number entirely.

Only offensive skill scoring is implemented. Kickers and team defences need
inputs we do not ingest (field goal distances, points allowed), and a league
where those matter for a start/sit call is not the case this tool serves.
"""

from __future__ import annotations

# Sleeper's setting keys, mapped to the stat column that pays them out. Kept
# explicit so a league using a key we do not support is reported rather than
# silently scored as zero.
SUPPORTED = {
    "pass_yd": "passing_yards",
    "pass_td": "passing_tds",
    "pass_int": "interceptions",
    "pass_2pt": None,        # folded into two_pt
    "rush_yd": "rushing_yards",
    "rush_td": "rushing_tds",
    "rush_2pt": None,
    "rec": "receptions",
    "rec_yd": "receiving_yards",
    "rec_td": "receiving_tds",
    "rec_2pt": None,
    "fum_lost": "fumbles_lost",
    "bonus_rec_te": None,    # per reception, tight ends only
}

# Settings that only ever apply to kickers or defences. Present in every league,
# irrelevant to a skill-position start/sit call, and not a reason to warn.
IRRELEVANT_PREFIXES = (
    "fgm", "fgmiss", "xpm", "xpmiss", "pts_allow", "def_", "st_", "idp_",
)
IRRELEVANT_KEYS = {
    "sack", "int", "safe", "ff", "fum_rec", "fum_rec_td", "blk_kick", "def_td",
    "tkl", "tkl_solo", "tkl_ast", "tkl_loss", "qb_hit",
}


def is_irrelevant(key: str) -> bool:
    return key.startswith(IRRELEVANT_PREFIXES) or key in IRRELEVANT_KEYS


def unsupported_settings(scoring: dict) -> list[str]:
    """Non-zero settings that would change a skill player's score and that we
    do not implement. Reported so the number is never quietly wrong."""
    out = []
    for key, value in (scoring or {}).items():
        if not value or key in SUPPORTED or is_irrelevant(key):
            continue
        out.append(key)
    return sorted(out)


def score(stat: dict, scoring: dict, position: str | None = None) -> float | None:
    """Points for one player-week under a league's settings.

    Returns None when there is no stat line at all, so a player who did not
    play reads as a dash rather than a real zero.
    """
    if not stat:
        return None

    def value(column: str) -> float:
        raw = stat.get(column)
        return float(raw) if raw is not None else 0.0

    def rule(key: str) -> float:
        return float((scoring or {}).get(key) or 0.0)

    points = 0.0
    points += value("passing_yards") * rule("pass_yd")
    points += value("passing_tds") * rule("pass_td")
    points += value("interceptions") * rule("pass_int")
    points += value("rushing_yards") * rule("rush_yd")
    points += value("rushing_tds") * rule("rush_td")
    points += value("receptions") * rule("rec")
    points += value("receiving_yards") * rule("rec_yd")
    points += value("receiving_tds") * rule("rec_td")
    points += value("fumbles_lost") * rule("fum_lost")

    # Two-point conversions are stored as one count; leagues almost always pay
    # the same for all three kinds, so use the passing rate as the rate.
    two_pt_rate = rule("pass_2pt") or rule("rush_2pt") or rule("rec_2pt")
    points += value("two_pt") * two_pt_rate

    # Tight end premium: an extra amount per reception, tight ends only.
    if (position or "").upper() == "TE":
        points += value("receptions") * rule("bonus_rec_te")

    return round(points, 2)


def describe(scoring: dict) -> str:
    """A short human summary of what makes this league unusual."""
    notes = []
    rec = float((scoring or {}).get("rec") or 0)
    if rec >= 1:
        notes.append("full PPR")
    elif rec > 0:
        notes.append(f"{rec} PPR")
    else:
        notes.append("standard")

    pass_td = float((scoring or {}).get("pass_td") or 0)
    if pass_td and pass_td != 4:
        notes.append(f"{pass_td:g}pt pass TD")
    te_bonus = float((scoring or {}).get("bonus_rec_te") or 0)
    if te_bonus:
        notes.append(f"TE premium +{te_bonus:g}/rec")
    return ", ".join(notes)


def is_superflex(roster_positions) -> bool:
    """A superflex slot lets a quarterback start in a flex, which changes what
    a QB is worth more than any scoring setting does."""
    return any(
        str(p).upper() in {"SUPER_FLEX", "SUPERFLEX", "QB/RB/WR/TE"}
        for p in (roster_positions or [])
    )
