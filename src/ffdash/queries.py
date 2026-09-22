"""Read queries for the UI and the CLI.

Everything here joins on internal player_id. Name lookup happens in exactly one
place -- `find_players` -- and only ever at an edge where a human typed a name.

Trailing windows are 4-6 weeks by design, not season-to-date: usage predicts
fantasy output better than production does, and recent usage better than old.
See CLAUDE.md.
"""

from __future__ import annotations

import sqlite3

from .db import table_exists

# Positions that matter for start/sit. Ordered for display.
SKILL_POSITIONS = ("QB", "RB", "WR", "TE", "FB", "K")
POSITION_ORDER = {p: i for i, p in enumerate(SKILL_POSITIONS)}


def teams_lookup(conn: sqlite3.Connection) -> dict[str, dict]:
    """abbr -> {name, nick, colour, has_logo}. Handed to every template.

    Logos are served from our own /static/logos, never the upstream CDN: the
    page has to render when an external service is down. `has_logo` is False
    when no file is cached or when public_mode() suppresses them, and the
    template falls back to a coloured initials badge.
    """
    from .config import REPO_ROOT, public_mode

    logo_dir = REPO_ROOT / "web" / "static" / "logos"
    # Club trademarks: shown on a tool only you use, suppressed once the site is
    # public. See config.public_mode().
    show_logos = not public_mode()
    out: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT team_abbr, full_name, nick, conference, division, "
        "team_color, team_color2 FROM team"
    ):
        abbr = row["team_abbr"]
        cached = logo_dir / f"{abbr}.png"
        out[abbr] = {
            "abbr": abbr,
            "full_name": row["full_name"],
            "nick": row["nick"] or row["full_name"],
            "conference": row["conference"],
            "division": row["division"],
            # A missing colour would make color-mix() drop the whole rule, so
            # fall back to the ink colour rather than emitting an empty string.
            "color": row["team_color"] or "var(--ink)",
            "color2": row["team_color2"] or "var(--muted)",
            # A flag, not a path: the template builds the URL, because the live
            # app and the generated site serve static files from different roots.
            "has_logo": show_logos and cached.exists(),
        }
    return out


def find_players(conn: sqlite3.Connection, name: str) -> list[dict]:
    """Name -> candidate players. An edge convenience, never a join key."""
    like = "%{}%".format(name.strip().lower())
    rows = conn.execute(
        """
        SELECT player_id, display_name, position, current_team
        FROM player
        WHERE LOWER(display_name) LIKE ?
        ORDER BY
          CASE WHEN LOWER(display_name) = ? THEN 0 ELSE 1 END,
          display_name
        """,
        (like, name.strip().lower()),
    ).fetchall()
    return [dict(r) for r in rows]


def trailing_usage(
    conn: sqlite3.Connection, player_id: int, season: int, weeks: int = 6
) -> list[dict]:
    """The last N recorded weeks of usage for one player, oldest first.

    Joins player_week_stat to snap_count on (player, season, week). A player can
    have a stat line with no snap row (and vice versa) so this is a LEFT JOIN
    from stats; a missing snap % renders as a dash, not a zero.
    """
    rows = conn.execute(
        """
        SELECT s.week, s.opponent, s.team_abbr,
               s.targets, s.receptions, s.receiving_yards, s.receiving_tds,
               s.carries, s.rushing_yards, s.rushing_tds,
               s.attempts, s.completions, s.passing_yards, s.passing_tds,
               s.interceptions,
               s.target_share, s.air_yards_share,
               s.rz_touches, s.inside10_touches, s.fantasy_ppr,
               s.corrected_at, s.updated_at,
               n.offense_snaps, n.offense_pct, n.route_pct
        FROM player_week_stat s
        LEFT JOIN snap_count n
               ON n.player_id = s.player_id
              AND n.season    = s.season
              AND n.week      = s.week
        WHERE s.player_id = ? AND s.season = ?
        ORDER BY s.week DESC
        LIMIT ?
        """,
        (player_id, season, weeks),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def game_by_id(conn: sqlite3.Connection, game_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT g.*,
               ht.full_name AS home_name, at.full_name AS away_name,
               st.name AS stadium_name, st.roof, st.latitude, st.longitude,
               st.timezone AS stadium_tz, st.verified_at AS stadium_verified_at
        FROM game g
        LEFT JOIN team ht ON ht.team_abbr = g.home_team
        LEFT JOIN team at ON at.team_abbr = g.away_team
        LEFT JOIN stadium st ON st.stadium_id = g.stadium_id
        WHERE g.game_id = ?
        """,
        (game_id,),
    ).fetchone()
    return dict(row) if row else None


def games_for_week(conn: sqlite3.Connection, season: int, week: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT g.game_id, g.week, g.kickoff_utc, g.home_team, g.away_team,
               g.home_score, g.away_score,
               o.spread_line, o.total_line, o.home_implied, o.away_implied
        FROM game g
        LEFT JOIN (
            SELECT game_id, spread_line, total_line, home_implied, away_implied
            FROM odds_game o1
            WHERE captured_at = (
                SELECT MAX(captured_at) FROM odds_game o2
                WHERE o2.game_id = o1.game_id AND o2.source = o1.source
            )
            AND source = 'nflverse'
        ) o ON o.game_id = g.game_id
        WHERE g.season = ? AND g.week = ?
        ORDER BY g.kickoff_utc, g.game_id
        """,
        (season, week),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_odds(conn: sqlite3.Connection, game_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT spread_line, total_line, home_implied, away_implied,
               captured_at, source, book
        FROM odds_game
        WHERE game_id = ?
        ORDER BY captured_at DESC
        LIMIT 1
        """,
        (game_id,),
    ).fetchone()
    return dict(row) if row else None


def odds_history(conn: sqlite3.Connection, game_id: str, limit: int = 12) -> list[dict]:
    """Line movement. The closing number is not the whole story."""
    rows = conn.execute(
        """
        SELECT spread_line, total_line, captured_at
        FROM odds_game
        WHERE game_id = ?
        ORDER BY captured_at DESC
        LIMIT ?
        """,
        (game_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def latest_weather(conn: sqlite3.Connection, game_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT * FROM weather_forecast
        WHERE game_id = ?
        ORDER BY captured_at DESC
        LIMIT 1
        """,
        (game_id,),
    ).fetchone()
    return dict(row) if row else None


def injuries_for_game(
    conn: sqlite3.Connection, season: int, week: int, teams: tuple[str, str]
) -> list[dict]:
    """Latest injury row per player for the two teams in this game.

    report_date is our observation date, so 'latest' means the most recent
    snapshot we took -- which is what the Friday designation will be sitting in.
    """
    rows = conn.execute(
        """
        SELECT i.player_id, p.display_name, p.position, p.current_team,
               i.practice_status, i.game_status, i.body_part, i.is_inactive,
               i.report_date, i.updated_at
        FROM injury_report i
        JOIN player p ON p.player_id = i.player_id
        WHERE i.season = ? AND i.week = ?
          AND p.current_team IN (?, ?)
          AND i.report_date = (
              SELECT MAX(report_date) FROM injury_report i2
              WHERE i2.player_id = i.player_id
                AND i2.season = i.season AND i2.week = i.week
          )
        ORDER BY p.current_team, p.display_name
        """,
        (season, week, teams[0], teams[1]),
    ).fetchall()
    out = [dict(r) for r in rows]
    out.sort(
        key=lambda r: (
            r["current_team"],
            POSITION_ORDER.get(r["position"], 99),
            r["display_name"],
        )
    )
    return out


# Availability, worst first. A player on IR and a player who was a full
# participant must not look alike in a usage table -- that is the whole point of
# the flag. `severity` drives the colour; "out" is red, "doubt" amber, "note"
# grey.
AVAILABILITY_RANK = [
    # (label, severity, long form)
    ("IR", "out", "On injured reserve"),
    ("INACTIVE", "out", "Ruled inactive for this game"),
    ("PUP", "out", "Physically unable to perform"),
    ("SUSP", "out", "Suspended"),
    ("CUT", "out", "Released"),
    ("RET", "out", "Retired"),
    ("OUT", "out", "Ruled out"),
    ("DOUBT", "doubt", "Doubtful"),
    ("QUES", "doubt", "Questionable"),
    ("DNP", "doubt", "Did not practise"),
    ("LTD", "note", "Limited in practice"),
    ("PS", "note", "Practice squad"),
]
_RANK_INDEX = {label: i for i, (label, _s, _l) in enumerate(AVAILABILITY_RANK)}

ROSTER_STATUS_LABEL = {
    "RES": "IR",
    "INA": "INACTIVE",
    "PUP": "PUP",
    "NFI": "PUP",
    "SUS": "SUSP",
    "CUT": "CUT",
    "RET": "RET",
    "DEV": "PS",
}

GAME_STATUS_LABEL = {"Out": "OUT", "Doubtful": "DOUBT", "Questionable": "QUES"}
PRACTICE_LABEL = {"DNP": "DNP", "Limited": "LTD"}


def availability_flag(row: dict | None) -> dict | None:
    """The single worst thing true about a player's availability, or None.

    Roster status and the injury report disagree often enough that picking one
    is wrong: a player can be on IR with no injury row at all (the feed simply
    drops him), or Questionable while rostered as active. Take the worse.
    """
    if not row:
        return None

    candidates = []
    roster = ROSTER_STATUS_LABEL.get((row.get("status") or "").upper())
    if roster:
        candidates.append(roster)
    game = GAME_STATUS_LABEL.get(row.get("game_status") or "")
    if game:
        candidates.append(game)
    practice = PRACTICE_LABEL.get(row.get("practice_status") or "")
    if practice:
        candidates.append(practice)

    if not candidates:
        return None

    label = min(candidates, key=lambda c: _RANK_INDEX.get(c, 99))
    index = _RANK_INDEX.get(label, len(AVAILABILITY_RANK) - 1)
    _l, severity, long_form = AVAILABILITY_RANK[index]

    detail = long_form
    body = row.get("body_part")
    if body:
        detail = "{} — {}".format(long_form, body)
    return {
        "label": label,
        "severity": severity,
        "detail": detail,
        "body_part": body,
        "long": long_form,
    }


def availability(
    conn: sqlite3.Connection, season: int, week: int, teams: tuple[str, ...]
) -> dict[int, dict]:
    """player_id -> availability flag, for every flagged player on these teams.

    Joins roster status to the most recent injury snapshot. Players with nothing
    worth saying about them are left out entirely, so a caller can treat a
    missing key as "fine".
    """
    if not teams:
        return {}
    marks = ", ".join("?" for _ in teams)
    rows = conn.execute(
        f"""
        SELECT p.player_id, p.display_name, p.position,
               r.status, r.status_abbr,
               i.game_status, i.practice_status, i.body_part, i.is_inactive
        FROM player p
        LEFT JOIN roster_status r
               ON r.player_id = p.player_id AND r.season = ? AND r.week = ?
        LEFT JOIN injury_report i
               ON i.player_id = p.player_id AND i.season = ? AND i.week = ?
              AND i.report_date = (
                    SELECT MAX(report_date) FROM injury_report i2
                    WHERE i2.player_id = p.player_id
                      AND i2.season = i.season AND i2.week = i.week
                  )
        WHERE COALESCE(r.team_abbr, p.current_team) IN ({marks})
          AND (r.status IS NOT NULL OR i.player_id IS NOT NULL)
        """,
        (season, week, season, week, *teams),
    ).fetchall()

    out: dict[int, dict] = {}
    for row in rows:
        flag = availability_flag(dict(row))
        if flag:
            out[row["player_id"]] = flag
    return out


def unavailable_not_on_report(
    conn: sqlite3.Connection,
    season: int,
    week: int,
    teams: tuple[str, ...],
    already: set[int],
) -> dict[str, list[dict]]:
    """Skill players who cannot play but appear nowhere on the injury report.

    This is the gap that started the whole thing: a player placed on injured
    reserve stops appearing in load_injuries() altogether, so the injury panel
    showed nothing and he read as healthy. Grouped by team for display.
    """
    if not teams:
        return {}
    marks = ", ".join("?" for _ in teams)
    rows = conn.execute(
        f"""
        SELECT p.player_id, p.display_name, p.position,
               COALESCE(r.team_abbr, p.current_team) AS team_abbr,
               r.status, r.status_abbr
        FROM roster_status r
        JOIN player p ON p.player_id = r.player_id
        WHERE r.season = ? AND r.week = ?
          AND COALESCE(r.team_abbr, p.current_team) IN ({marks})
          AND r.status IN ('RES','INA','PUP','NFI','SUS')
          AND p.position IN ('QB','RB','WR','TE','FB','K')
        ORDER BY p.display_name
        """,
        (season, week, *teams),
    ).fetchall()

    out: dict[str, list[dict]] = {}
    for row in rows:
        if row["player_id"] in already:
            continue
        item = dict(row)
        item["avail"] = availability_flag(item)
        out.setdefault(row["team_abbr"], []).append(item)

    for team in out:
        out[team].sort(key=lambda r: (POSITION_ORDER.get(r["position"], 99),
                                      r["display_name"]))
    return out


def latest_injury_week(
    conn: sqlite3.Connection, season: int, upto_week: int
) -> int | None:
    """The most recent week at or before `upto_week` that has injury rows.

    The nflverse injury feed lags the schedule: midweek, the upcoming week's
    practice reports simply do not exist yet. Querying the game's own week then
    returns nothing and the panel reads "no injury report rows", which is
    indistinguishable from "everyone is healthy" -- and throws away the fact
    that a starter was ruled out seven days ago.
    """
    row = conn.execute(
        "SELECT MAX(week) FROM injury_report WHERE season = ? AND week <= ?",
        (season, upto_week),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def previous_ingame_exits(
    conn: sqlite3.Connection, season: int, week: int, teams: tuple[str, ...]
) -> dict[int, dict]:
    """Players who left the most recent game injured and did not return.

    Distinct from not playing at all. A starter who walks off in the second
    quarter still has a full-looking stat line and a normal roster status, and
    no other feed records it -- the weekly report is published before kickoff,
    and IR may not follow until Wednesday. Parsed from play-by-play by
    scripts/ingest_ingame_injuries.py.
    """
    if not teams or week <= 1:
        return {}
    if not table_exists(conn, "ingame_injury"):
        return {}
    marks = ", ".join("?" for _ in teams)
    rows = conn.execute(
        f"""
        SELECT g.player_id, g.week, g.returned, g.detail
        FROM ingame_injury g
        JOIN player p ON p.player_id = g.player_id
        LEFT JOIN roster_status r
               ON r.player_id = g.player_id AND r.season = g.season AND r.week = g.week
        WHERE g.season = ? AND g.week = ? AND g.left_game = 1
          AND COALESCE(r.team_abbr, p.current_team) IN ({marks})
        """,
        (season, week - 1, *teams),
    ).fetchall()
    return {
        r["player_id"]: {
            "week": r["week"],
            "returned": bool(r["returned"]),
            "detail": r["detail"],
        }
        for r in rows
    }


def previous_absences(
    conn: sqlite3.Connection, season: int, week: int, teams: tuple[str, ...]
) -> dict[int, dict]:
    """Who did not play the most recent week, and why.

    A quarterback who was inactive last Sunday is decision-critical this Sunday
    even once his roster status flips back to active, and nothing else on the
    page carries that. Keyed by player_id.
    """
    if not teams or week <= 1:
        return {}
    marks = ", ".join("?" for _ in teams)
    rows = conn.execute(
        f"""
        SELECT r.player_id, r.week, r.status,
               i.game_status, i.body_part, i.practice_status
        FROM roster_status r
        LEFT JOIN injury_report i
               ON i.player_id = r.player_id AND i.season = r.season
              AND i.week = r.week
              AND i.report_date = (
                    SELECT MAX(report_date) FROM injury_report i2
                    WHERE i2.player_id = r.player_id
                      AND i2.season = r.season AND i2.week = r.week
                  )
        WHERE r.season = ? AND r.week = ?
          AND r.team_abbr IN ({marks})
          AND (r.status IN ('INA','RES','PUP','SUS') OR i.game_status = 'Out')
        """,
        (season, week - 1, *teams),
    ).fetchall()

    out: dict[int, dict] = {}
    for row in rows:
        reason = row["body_part"] or None
        out[row["player_id"]] = {
            "week": row["week"],
            "status": row["status"],
            "reason": reason,
            "detail": "Did not play week {}{}".format(
                row["week"], " — {}".format(reason) if reason else ""
            ),
        }
    return out


def practice_progression(
    conn: sqlite3.Connection, player_id: int, season: int, week: int
) -> list[dict]:
    """Every snapshot we hold for this player-week, so W/Th/F reads as a trend."""
    rows = conn.execute(
        """
        SELECT report_date, practice_status, game_status
        FROM injury_report
        WHERE player_id = ? AND season = ? AND week = ?
        ORDER BY report_date
        """,
        (player_id, season, week),
    ).fetchall()
    return [dict(r) for r in rows]


def skill_players_for_team(
    conn: sqlite3.Connection, team: str, season: int, week: int, weeks: int = 6
) -> list[dict]:
    """Skill players worth showing for this team, with trailing usage attached.

    'Worth showing' means they recorded a snap or a touch in the trailing
    window -- a roster list would bury the four names that matter under fifty
    that do not.
    """
    low = max(1, week - weeks)
    rows = conn.execute(
        """
        SELECT p.player_id, p.display_name, p.position,
               AVG(n.offense_pct)      AS avg_snap_pct,
               AVG(s.fantasy_ppr)      AS avg_ppr,
               SUM(s.fantasy_ppr)      AS tot_ppr,
               COUNT(s.week)           AS games,
               MAX(s.updated_at)       AS updated_at,
               -- passing
               SUM(s.attempts)         AS tot_attempts,
               SUM(s.completions)      AS tot_completions,
               SUM(s.passing_yards)    AS tot_passing_yards,
               SUM(s.passing_tds)      AS tot_passing_tds,
               SUM(s.interceptions)    AS tot_interceptions,
               -- rushing
               SUM(s.carries)          AS tot_carries,
               SUM(s.rushing_yards)    AS tot_rushing_yards,
               SUM(s.rushing_tds)      AS tot_rushing_tds,
               -- receiving
               AVG(s.target_share)     AS avg_target_share,
               AVG(s.air_yards_share)  AS avg_air_yards_share,
               SUM(s.targets)          AS tot_targets,
               SUM(s.receptions)       AS tot_receptions,
               SUM(s.receiving_yards)  AS tot_receiving_yards,
               SUM(s.receiving_tds)    AS tot_receiving_tds,
               -- scoring position
               SUM(s.rz_touches)       AS tot_rz,
               SUM(s.inside10_touches) AS tot_in10
        FROM player_week_stat s
        JOIN player p ON p.player_id = s.player_id
        LEFT JOIN snap_count n
               ON n.player_id = s.player_id AND n.season = s.season AND n.week = s.week
        WHERE s.season = ? AND s.week >= ? AND s.week < ?
          AND s.team_abbr = ?
          AND p.position IN ('QB','RB','WR','TE','FB')
        GROUP BY p.player_id, p.display_name, p.position
        HAVING COALESCE(SUM(s.targets),0) + COALESCE(SUM(s.carries),0)
               + COALESCE(SUM(s.attempts),0) > 0
        ORDER BY
          CASE p.position WHEN 'QB' THEN 0 WHEN 'RB' THEN 1
                          WHEN 'WR' THEN 2 WHEN 'TE' THEN 3 ELSE 4 END,
          avg_ppr DESC
        """,
        (season, low, week, team),
    ).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        row["group"] = position_group(row["position"])
        attempts = row["tot_attempts"] or 0
        row["completion_pct"] = (
            (row["tot_completions"] or 0) / attempts if attempts else None
        )
        out.append(row)
    return out


# Which stat set a position is judged on. A quarterback does not run routes, and
# a receiver does not throw; showing them the same columns makes both harder to
# read and puts a meaningless 0% target share next to every QB.
POSITION_GROUPS = {
    "QB": "passing",
    "RB": "rushing",
    "FB": "rushing",
    "WR": "receiving",
    "TE": "receiving",
}

GROUP_LABELS = {
    "passing": "Quarterbacks",
    "rushing": "Backfield",
    "receiving": "Receivers",
}

GROUP_ORDER = ("passing", "rushing", "receiving")


def position_group(position: str | None) -> str:
    return POSITION_GROUPS.get((position or "").upper(), "receiving")


def group_players(players: list[dict]) -> list[tuple[str, str, list[dict]]]:
    """[(group key, label, players)] in display order, empty groups dropped."""
    out = []
    for key in GROUP_ORDER:
        members = [p for p in players if p.get("group") == key]
        if members:
            out.append((key, GROUP_LABELS[key], members))
    return out


def weekly_series(
    conn: sqlite3.Connection, player_id: int, season: int, week: int, weeks: int = 6
) -> list[dict]:
    """Per-week points for the sparklines in the usage block.

    Carries the passing columns too, so a quarterback's trend line can track
    attempts and yards rather than a target share he will never have.
    """
    low = max(1, week - weeks)
    rows = conn.execute(
        """
        SELECT s.week, s.target_share, s.targets, s.carries, s.receptions,
               s.attempts, s.passing_yards, s.rushing_yards, s.receiving_yards,
               s.rz_touches, s.fantasy_ppr, n.offense_pct
        FROM player_week_stat s
        LEFT JOIN snap_count n
               ON n.player_id = s.player_id AND n.season = s.season AND n.week = s.week
        WHERE s.player_id = ? AND s.season = ? AND s.week >= ? AND s.week < ?
        ORDER BY s.week
        """,
        (player_id, season, low, week),
    ).fetchall()
    return [dict(r) for r in rows]


def defense_allowed(
    conn: sqlite3.Connection, opponent: str, season: int, week: int, weeks: int = 6
) -> list[dict]:
    """What this defence has allowed by position over the trailing window.

    Trailing 4-6 weeks by position, not full season -- same reasoning as usage.
    Ranked against the league over the identical window so the number has a
    reference point; rank 1 = most generous to that position.
    """
    low = max(1, week - weeks)
    rows = conn.execute(
        """
        WITH allowed AS (
            SELECT s.opponent AS defense, p.position AS pos,
                   SUM(s.fantasy_ppr) AS ppr_total,
                   COUNT(DISTINCT s.week) AS games
            FROM player_week_stat s
            JOIN player p ON p.player_id = s.player_id
            WHERE s.season = ? AND s.week >= ? AND s.week < ?
              AND p.position IN ('QB','RB','WR','TE')
              AND s.opponent IS NOT NULL
            GROUP BY s.opponent, p.position
        ),
        per_game AS (
            SELECT defense, pos, games,
                   CASE WHEN games > 0 THEN ppr_total / games END AS ppr_allowed_pg
            FROM allowed
        )
        SELECT pos, games, ppr_allowed_pg,
               (SELECT COUNT(*) + 1 FROM per_game b
                 WHERE b.pos = a.pos AND b.ppr_allowed_pg > a.ppr_allowed_pg) AS rank_generous,
               (SELECT COUNT(*) FROM per_game c WHERE c.pos = a.pos) AS n_defenses
        FROM per_game a
        WHERE defense = ?
        ORDER BY CASE pos WHEN 'QB' THEN 0 WHEN 'RB' THEN 1
                          WHEN 'WR' THEN 2 ELSE 3 END
        """,
        (season, low, week, opponent),
    ).fetchall()
    return [dict(r) for r in rows]


def head_to_head(
    conn: sqlite3.Connection, player_id: int, opponent: str, limit: int = 5
) -> list[dict]:
    """Career games against this opponent. Below the fold, deliberately.

    Rosters and schemes turn over; this is close to noise next to usage. It
    exists because it is occasionally interesting, not because it predicts.
    """
    rows = conn.execute(
        """
        SELECT season, week, targets, receptions, receiving_yards,
               carries, rushing_yards, fantasy_ppr
        FROM player_week_stat
        WHERE player_id = ? AND opponent = ?
        ORDER BY season DESC, week DESC
        LIMIT ?
        """,
        (player_id, opponent, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def freshness(conn: sqlite3.Connection) -> dict[str, dict]:
    """Last successful run per ingest source. Every panel stamps itself with this."""
    rows = conn.execute(
        """
        SELECT source, MAX(finished_at) AS finished_at
        FROM ingest_log
        WHERE ok = 1 AND finished_at IS NOT NULL
        GROUP BY source
        """
    ).fetchall()
    return {r["source"]: dict(r) for r in rows}
