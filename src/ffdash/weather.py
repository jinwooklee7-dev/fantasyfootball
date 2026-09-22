"""Weather, from Open-Meteo. Free, no API key.

Wind is the variable that matters. Sustained wind above ~15 mph measurably
suppresses passing and kicking; cold and light rain matter less than people
assume. The UI leads with wind speed and direction, and the whole panel is
suppressed for domes and closed roofs rather than showing a meaningless
forecast. See CLAUDE.md.
"""

from __future__ import annotations

import httpx

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo is asked for imperial units directly rather than converting here --
# one less place for a unit bug to hide.
HOURLY_FIELDS = (
    "temperature_2m",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "precipitation_probability",
    "precipitation",
    "weather_code",
)

# Roof states where the forecast is meaningless.
SEALED_ROOFS = {"dome", "closed"}

# Roof states that are genuinely unknown until gameday. Show the outdoor
# forecast, but say so.
UNKNOWN_ROOFS = {"retractable"}

# https://open-meteo.com/en/docs -- WMO weather interpretation codes, collapsed
# to the handful of descriptions worth showing.
WEATHER_CODES = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Freezing fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Violent showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}

COMPASS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


def roof_applicable(roof: str | None, roof_state: str | None) -> tuple[bool, str | None]:
    """Should we fetch a forecast for this venue? Returns (applicable, caveat).

    `roof` is the venue's permanent structure, from data/stadiums.csv.
    `roof_state` is nflverse's per-game roof field.

    Structure wins over the per-game field, because the per-game field is
    coarse. nflverse reports SoFi as "closed", but SoFi's roof is fixed and the
    SIDES ARE OPEN -- wind reaches the field there, and suppressing the panel
    would hide the one variable that matters. Only a genuinely sealed bowl gets
    suppressed.

    For retractable venues the per-game field is real information (the league
    announced the roof state), so there it does decide.
    """
    base = (roof or "").strip().lower()
    state = (roof_state or "").strip().lower()

    # Permanently sealed: no forecast, ever.
    if base in SEALED_ROOFS:
        return False, None

    # Roof overhead, sides open. Wind matters; precipitation largely does not.
    if base == "fixed_open_sides":
        return True, "roof_overhead_sides_open"

    # Retractable: gameday announcement decides, and is unknown until it comes.
    if base in UNKNOWN_ROOFS:
        if state in SEALED_ROOFS:
            return False, None
        if state in {"open", "outdoors"}:
            return True, None
        return True, "retractable_unknown"

    # Open-air, or a venue we have no structure for. Trust a sealed per-game
    # field if one is present, otherwise fetch.
    if not base and state in SEALED_ROOFS:
        return False, None
    return True, None


def fetch_hourly(
    latitude: float,
    longitude: float,
    kickoff_iso_hour: str,
    client: httpx.Client | None = None,
) -> dict | None:
    """Fetch the forecast for the kickoff hour. Returns None if unavailable.

    Open-Meteo returns hourly arrays; we pick the index matching the kickoff
    hour rather than taking a daily summary, because a 1pm and an 8pm kickoff at
    the same stadium are different games.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        response = client.get(
            FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": ",".join(HOURLY_FIELDS),
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
                "timezone": "UTC",
                "forecast_days": 16,
            },
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            client.close()

    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return None

    try:
        index = times.index(kickoff_iso_hour)
    except ValueError:
        return None

    def at(field: str):
        series = hourly.get(field) or []
        return series[index] if index < len(series) else None

    code = at("weather_code")
    return {
        "temp_f": at("temperature_2m"),
        "wind_mph": at("wind_speed_10m"),
        "wind_gust_mph": at("wind_gusts_10m"),
        "wind_dir_deg": at("wind_direction_10m"),
        "precip_prob": at("precipitation_probability"),
        "precip_in": at("precipitation"),
        "conditions": WEATHER_CODES.get(int(code)) if code is not None else None,
    }


def compass(degrees: float | None) -> str:
    """Wind direction as a compass point. Meteorological: the direction it comes FROM."""
    if degrees is None:
        return "—"
    return COMPASS[int((float(degrees) % 360) / 22.5 + 0.5) % 16]


def wind_severity(wind_mph: float | None, gust_mph: float | None) -> str:
    """calm | breezy | significant — drives the colour of the wind readout.

    ~15 mph sustained is the threshold where passing and kicking measurably
    suffer, so that is where 'significant' starts.
    """
    if wind_mph is None:
        return "unknown"
    wind = float(wind_mph)
    gust = float(gust_mph) if gust_mph is not None else wind
    if wind >= 15 or gust >= 25:
        return "significant"
    if wind >= 10 or gust >= 18:
        return "breezy"
    return "calm"


def summarise(row: dict | None) -> str:
    """One line, wind first. 'Roof closed — no weather impact' for sealed venues."""
    if row is None:
        return "No forecast yet"
    if not row.get("applicable"):
        return "Roof closed — no weather impact"
    wind = row.get("wind_mph")
    if wind is None:
        return "Forecast unavailable"
    direction = compass(row.get("wind_dir_deg"))
    parts = ["{:.0f} mph {} wind".format(float(wind), direction)]
    gust = row.get("wind_gust_mph")
    if gust is not None and float(gust) >= float(wind) + 5:
        parts.append("gusting {:.0f}".format(float(gust)))
    temp = row.get("temp_f")
    if temp is not None:
        parts.append("{:.0f}°F".format(float(temp)))
    conditions = row.get("conditions")
    if conditions:
        parts.append(conditions.lower())
    return ", ".join(parts)
