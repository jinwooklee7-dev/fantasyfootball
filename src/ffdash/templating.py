"""Jinja filters and globals, shared by the live app and the static renderer.

Both build the same pages; only the URL builder differs.
"""

from __future__ import annotations

from typing import Callable

from .timeutil import humanise_age, render_eastern
from .weather import compass


def num(value, digits: int = 1) -> str:
    """Format a number for display, or a dash.

    Tolerates a missing key as well as a NULL: a column dropped from a query
    should leave one cell blank, not return a 500 for the whole page.
    """
    if value is None:
        return "—"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "—"
    if f == int(f):
        return str(int(f))
    return "{:.{}f}".format(f, digits)


def pct(value) -> str:
    if value is None:
        return "—"
    try:
        return "{:.0f}%".format(float(value) * 100)
    except (TypeError, ValueError):
        return "—"


def signed(value) -> str:
    if value is None:
        return "—"
    try:
        return "{:+.1f}".format(float(value))
    except (TypeError, ValueError):
        return "—"


def configure(env, url: Callable[..., str]) -> None:
    """Install filters and the URL builder on a Jinja environment."""
    env.filters["age"] = humanise_age
    env.filters["eastern"] = render_eastern
    env.filters["compass"] = compass
    env.filters["num"] = num
    env.filters["pct"] = pct
    env.filters["signed"] = signed
    env.globals["url"] = url
