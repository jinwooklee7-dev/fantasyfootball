"""Implied team totals.

The single best free start/sit signal, and it costs nothing but arithmetic.
From a game line:  implied = total/2 +/- spread/2.

nflverse states spread_line from the HOME team's perspective, POSITIVE when the
home team is favoured. This was verified against historical results rather than
taken from documentation -- getting the sign backwards silently inverts the most
important number on the page.
"""

from __future__ import annotations


def implied_totals(
    spread_line: float | None, total_line: float | None
) -> tuple[float | None, float | None]:
    """Return (home_implied, away_implied).

    >>> implied_totals(9.5, 53.5)      # home favoured by 9.5
    (31.5, 22.0)
    >>> implied_totals(-3.0, 44.0)     # home is a 3-point underdog
    (20.5, 23.5)
    """
    if spread_line is None or total_line is None:
        return None, None
    home = total_line / 2 + spread_line / 2
    away = total_line / 2 - spread_line / 2
    return round(home, 2), round(away, 2)


def describe_spread(spread_line: float | None, home: str, away: str) -> str:
    """'KC -9.5' / 'Pick'em' -- for display."""
    if spread_line is None:
        return "—"
    if abs(spread_line) < 0.01:
        return "Pick'em"
    if spread_line > 0:
        return f"{home} -{_trim(spread_line)}"
    return f"{away} -{_trim(abs(spread_line))}"


def _trim(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") if value % 1 else f"{int(value)}"
