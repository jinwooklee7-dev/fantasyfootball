"""Time handling. Store UTC, render America/New_York. No exceptions."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_utc_iso(dt: datetime) -> str:
    """Normalise a datetime to a UTC ISO8601 string. Naive input is assumed UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def render_eastern(value: str | None, fmt: str = "%a %-I:%M %p ET") -> str:
    """Format a stored UTC string for display. Render-time only."""
    dt = parse_utc(value)
    if dt is None:
        return "—"
    # %-I is not portable to Windows; fall back to manual stripping.
    try:
        return dt.astimezone(EASTERN).strftime(fmt)
    except ValueError:
        return dt.astimezone(EASTERN).strftime(fmt.replace("%-I", "%I")).replace(" 0", " ")


def humanise_age(value: str | None) -> str:
    """'3m ago' / '2h ago' / '4d ago' for last-updated stamps."""
    dt = parse_utc(value)
    if dt is None:
        return "never"
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    if secs < 90:
        return f"{secs}s ago"
    if secs < 5400:
        return f"{secs // 60}m ago"
    if secs < 172800:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"
