"""Template context for each page.

Extracted from the FastAPI routes so the static renderer builds pages from the
identical logic. Two implementations of "what goes on a game page" would drift
within a week, and the generated site is the one nobody is watching.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import queries as q
from . import scoring
from .odds import describe_spread
from .timeutil import parse_utc
from .weather import summarise, wind_severity

# How old each ingest may get before the footer flags it red. Roughly double the
# cron interval, so one missed run is not an alarm but a stopped scheduler is
# obvious. Nothing runs itself -- see deploy/crontab.example.
STALE_AFTER_SECONDS = {
    "schedules": 2 * 3600,
    "weather": 12 * 3600,
    "snaps": 18 * 3600,
    "player_stats": 30 * 3600,
    "injuries": 30 * 3600,
    "depth_charts": 30 * 3600,
    "nextgen": 30 * 3600,
    "players": 9 * 86400,
}
DEFAULT_STALE_AFTER = 30 * 3600


def mark_stale(fresh: dict[str, dict]) -> dict[str, dict]:
    now = datetime.now(timezone.utc)
    for source, row in fresh.items():
        stamp = parse_utc(row.get("finished_at"))
        limit = STALE_AFTER_SECONDS.get(source, DEFAULT_STALE_AFTER)
        row["stale"] = True if stamp is None else (now - stamp).total_seconds() > limit
    return fresh


def current_week(conn: sqlite3.Connection, season: int) -> int:
    """The week to show by default: the earliest with an unplayed game."""
    row = conn.execute(
        "SELECT MIN(week) FROM game WHERE season = ? AND home_score IS NULL",
        (season,),
    ).fetchone()
    if row and row[0]:
        return int(row[0])
    row = conn.execute("SELECT MAX(week) FROM game WHERE season = ?", (season,)).fetchone()
    return int(row[0]) if row and row[0] else 1


def shell(conn: sqlite3.Connection, fmt: str = scoring.DEFAULT_FORMAT) -> dict:
    """Context every page needs: team identity, freshness and scoring format."""
    return {
        "teams": q.teams_lookup(conn),
        "fresh": mark_stale(q.freshness(conn)),
        "scoring": scoring.normalise(fmt),
        "scoring_label": scoring.label(fmt),
        "scoring_long": scoring.long_label(fmt),
        "scoring_formats": scoring.FORMATS,
    }


def apply_scoring(players: list[dict], fmt: str) -> None:
    """Attach points in EVERY scoring format, plus the currently selected one.

    All three are emitted into the page so the format switch can work client
    side. A query parameter cannot drive it on the generated site -- there is no
    server to read one -- and rendering three copies of 900 pages to work around
    that would be absurd. The numbers are already computed; carrying all of them
    costs a few bytes a row.

    Full PPR is what is stored; the others are that minus the reception bonus.
    See ffdash.scoring for why this is a subtraction, not a reimplementation.
    """
    for p in players:
        games = p.get("games") or 0
        p["points_by_format"] = {}
        for key in scoring.FORMATS:
            total = scoring.adjust(p.get("tot_ppr"), p.get("tot_receptions"), key)
            p["points_by_format"][key] = (
                (total / games) if (total is not None and games) else None
            )
        p["avg_points"] = p["points_by_format"][scoring.normalise(fmt)]

        for week in p.get("series", []):
            week["points_by_format"] = {
                key: scoring.adjust(
                    week.get("fantasy_ppr"), week.get("receptions"), key
                )
                for key in scoring.FORMATS
            }
            week["points"] = week["points_by_format"][scoring.normalise(fmt)]


_shell = shell


def week_context(
    conn: sqlite3.Connection, season: int, week: int, fmt: str = scoring.DEFAULT_FORMAT
) -> dict:
    games = q.games_for_week(conn, season, week)
    for g in games:
        g["spread_text"] = describe_spread(
            g["spread_line"], g["home_team"], g["away_team"]
        )
    weeks = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT week FROM game WHERE season = ? ORDER BY week", (season,)
        )
    ]
    return {
        "games": games,
        "season": season,
        "week": week,
        "weeks": weeks,
        **shell(conn, fmt),
    }


def game_context(
    conn: sqlite3.Connection,
    game_id: str,
    weeks: int = 6,
    fmt: str = scoring.DEFAULT_FORMAT,
) -> dict | None:
    game = q.game_by_id(conn, game_id)
    if game is None:
        return None

    season, week = game["season"], game["week"]
    home, away = game["home_team"], game["away_team"]

    odds = q.latest_odds(conn, game_id)
    weather = q.latest_weather(conn, game_id)

    # The injury feed lags the schedule. Midweek there are no rows for the
    # upcoming week at all, and querying it would render "no injury report
    # rows" -- which reads as "everyone is healthy". Fall back to the most
    # recent week that has data and say plainly which week is on screen.
    injury_week = q.latest_injury_week(conn, season, week) or week
    injuries = q.injuries_for_game(conn, season, injury_week, (home, away))
    for row in injuries:
        row["progression"] = q.practice_progression(
            conn, row["player_id"], season, injury_week
        )

    # Who did not play last week. A starter who was inactive seven days ago
    # matters even once his roster status flips back to active.
    missed = q.previous_absences(conn, season, week, (home, away))

    # Left last week's game hurt and did not come back. Stronger and more
    # specific than "did not play": no other feed records it at all.
    exits = q.previous_ingame_exits(conn, season, week, (home, away))

    # Availability: IR, inactives, and the practice designations, keyed by
    # player. Attached to the usage rows so a player who cannot play does not
    # sit in the table looking like a viable start.
    avail = q.availability(conn, season, week, (home, away))

    # Usage is grouped by position: a quarterback's row has nothing in common
    # with a receiver's, so they get separate tables with their own columns.
    usage = {}
    for team, opponent in ((away, home), (home, away)):
        players = q.skill_players_for_team(conn, team, season, week, weeks)
        for p in players:
            p["series"] = q.weekly_series(conn, p["player_id"], season, week, weeks)
            p["h2h"] = q.head_to_head(conn, p["player_id"], opponent)
            p["avail"] = avail.get(p["player_id"])
            p["missed"] = missed.get(p["player_id"])
            p["exit"] = exits.get(p["player_id"])
        apply_scoring(players, fmt)
        usage[team] = q.group_players(players)

    for row in injuries:
        row["avail"] = avail.get(row["player_id"])
        row["missed"] = missed.get(row["player_id"])
        row["exit"] = exits.get(row["player_id"])

    # Anyone unavailable who is NOT on the injury report -- almost always an IR
    # player, whom the injury feed drops entirely. Without this they vanish.
    flagged_ids = {i["player_id"] for i in injuries}
    missing_from_report = q.unavailable_not_on_report(
        conn, season, week, (home, away), flagged_ids
    )

    slate = q.games_for_week(conn, season, week)
    ids = [g["game_id"] for g in slate]
    here = ids.index(game_id) if game_id in ids else -1

    return {
        "game": game,
        "odds": odds,
        "spread_text": describe_spread(odds["spread_line"] if odds else None, home, away),
        "odds_history": q.odds_history(conn, game_id),
        "weather": weather,
        "weather_summary": summarise(weather),
        "wind_class": wind_severity(
            weather.get("wind_mph") if weather else None,
            weather.get("wind_gust_mph") if weather else None,
        ),
        "injuries_by_team": {
            away: [i for i in injuries if i["current_team"] == away],
            home: [i for i in injuries if i["current_team"] == home],
        },
        "unavailable_by_team": missing_from_report,
        "injury_week": injury_week,
        "injury_week_is_stale": injury_week != week,
        "usage": usage,
        "defense": {
            away: q.defense_allowed(conn, away, season, week, weeks),
            home: q.defense_allowed(conn, home, season, week, weeks),
        },
        "home": home,
        "away": away,
        "weeks": weeks,
        "prev_game": slate[here - 1] if here > 0 else None,
        "next_game": slate[here + 1] if 0 <= here < len(slate) - 1 else None,
        **shell(conn, fmt),
    }


def player_context(
    conn: sqlite3.Connection,
    player_id: int,
    season: int,
    weeks: int = 8,
    fmt: str = scoring.DEFAULT_FORMAT,
) -> dict | None:
    row = conn.execute(
        "SELECT player_id, display_name, position, current_team FROM player "
        "WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    if row is None:
        return None
    rows = q.trailing_usage(conn, player_id, season, weeks)
    for r in rows:
        r["points_by_format"] = {
            key: scoring.adjust(r.get("fantasy_ppr"), r.get("receptions"), key)
            for key in scoring.FORMATS
        }
        r["points"] = r["points_by_format"][scoring.normalise(fmt)]
    return {
        "player": dict(row),
        "rows": rows,
        "group": q.position_group(row["position"]),
        "season": season,
        **shell(conn, fmt),
    }


def players_on_game_page(context: dict) -> set[int]:
    """Every player_id a game page links to.

    The renderer uses this to build exactly the player pages that are reachable,
    rather than all 24,000 in the database or -- worse -- guessing and leaving
    dead links.
    """
    found: set[int] = set()
    for groups in context.get("usage", {}).values():
        for _key, _label, members in groups:
            for p in members:
                found.add(p["player_id"])
    for rows in context.get("injuries_by_team", {}).values():
        for r in rows:
            found.add(r["player_id"])
    # The IR block links out too. Missing these left dead links on every game
    # page until the static link check caught it.
    for rows in context.get("unavailable_by_team", {}).values():
        for r in rows:
            found.add(r["player_id"])
    return found
