#!/usr/bin/env python3
"""Kickoff-hour weather for upcoming games. Open-Meteo, free, no key.

Domes and closed roofs are not fetched at all -- they store a row with
applicable=0 so the UI can say "Roof closed" rather than render a meaningless
forecast. Retractable roofs are unknown until gameday: the outdoor forecast is
stored with a retractable_unknown caveat.

    uv run scripts/ingest_weather.py              # next 7 days
    uv run scripts/ingest_weather.py --hours 24   # inside 24h of kickoff
    uv run scripts/ingest_weather.py --week 3

Cron: daily, then hourly inside 24 hours of kickoff.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse
from datetime import datetime, timedelta, timezone

import httpx

from ffdash.config import current_season
from ffdash.db import session, upsert
from ffdash.ingestlog import logged
from ffdash.timeutil import parse_utc, utc_now_iso
from ffdash.weather import fetch_hourly, roof_applicable

# Open-Meteo's forecast horizon. Beyond this there is nothing to ask for.
MAX_FORECAST_DAYS = 16


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--hours",
        type=int,
        default=24 * 7,
        help="only games kicking off within this many hours (default 168)",
    )
    ap.add_argument("--week", type=int, default=None, help="a specific week instead")
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()
    season = args.season or current_season()

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=min(args.hours, MAX_FORECAST_DAYS * 24))

    with session() as conn:
        with logged(conn, "weather") as run:
            if args.week is not None:
                games = conn.execute(
                    """
                    SELECT g.game_id, g.kickoff_utc, g.roof_state,
                           s.latitude, s.longitude, s.roof, s.name AS stadium_name
                    FROM game g LEFT JOIN stadium s ON s.stadium_id = g.stadium_id
                    WHERE g.season = ? AND g.week = ?
                    ORDER BY g.kickoff_utc
                    """,
                    (season, args.week),
                ).fetchall()
            else:
                games = conn.execute(
                    """
                    SELECT g.game_id, g.kickoff_utc, g.roof_state,
                           s.latitude, s.longitude, s.roof, s.name AS stadium_name
                    FROM game g LEFT JOIN stadium s ON s.stadium_id = g.stadium_id
                    WHERE g.season = ?
                      AND g.kickoff_utc IS NOT NULL
                      AND g.kickoff_utc >= ?
                      AND g.kickoff_utc <= ?
                    ORDER BY g.kickoff_utc
                    """,
                    (season, _iso(now), _iso(horizon)),
                ).fetchall()

            captured = utc_now_iso()
            rows = []
            sealed = fetched = skipped = failed = 0

            with httpx.Client(timeout=30) as client:
                for g in games:
                    kickoff = parse_utc(g["kickoff_utc"])
                    if kickoff is None:
                        skipped += 1
                        continue

                    applicable, caveat = roof_applicable(g["roof"], g["roof_state"])

                    if not applicable:
                        sealed += 1
                        rows.append(_row(g, captured, kickoff, applicable=0))
                        continue

                    if g["latitude"] is None or g["longitude"] is None:
                        # No coordinates seeded for this venue -- record the gap
                        # rather than silently omitting the game.
                        skipped += 1
                        rows.append(
                            _row(g, captured, kickoff, applicable=1,
                                 conditions="no stadium coordinates")
                        )
                        continue

                    # Open-Meteo indexes hourly data on the exact hour.
                    hour_key = kickoff.strftime("%Y-%m-%dT%H:00")
                    try:
                        forecast = fetch_hourly(
                            float(g["latitude"]),
                            float(g["longitude"]),
                            hour_key,
                            client=client,
                        )
                    except Exception as exc:
                        failed += 1
                        print("  {}: {}".format(g["game_id"], exc))
                        continue

                    if forecast is None:
                        skipped += 1
                        continue

                    fetched += 1
                    row = _row(g, captured, kickoff, applicable=1)
                    row.update(forecast)
                    if caveat and row.get("conditions"):
                        row["conditions"] = "{} ({})".format(row["conditions"], caveat)
                    elif caveat:
                        row["conditions"] = caveat
                    rows.append(row)

            run.rows = upsert(
                conn, "weather_forecast", rows, key=["game_id", "captured_at"]
            )
            run.note = "{} fetched, {} sealed roofs, {} skipped, {} failed".format(
                fetched, sealed, skipped, failed
            )


def _row(g, captured: str, kickoff, applicable: int) -> dict:
    return {
        "game_id": g["game_id"],
        "captured_at": captured,
        "valid_for_utc": kickoff.strftime("%Y-%m-%dT%H:00:00Z"),
        "applicable": applicable,
        "temp_f": None,
        "wind_mph": None,
        "wind_gust_mph": None,
        "wind_dir_deg": None,
        "precip_prob": None,
        "precip_in": None,
        "conditions": None,
    }


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    main()
