"""Integrity checks against the live database.

These are not unit tests -- they assert that what the ingests actually wrote
hangs together. They skip cleanly when the database has not been built yet.

    uv run scripts/init_db.py && uv run pytest
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from ffdash.config import db_path  # noqa: E402
from ffdash.db import connect, scalar  # noqa: E402
from ffdash.odds import implied_totals  # noqa: E402


@pytest.fixture(scope="module")
def conn():
    if not db_path().exists():
        pytest.skip("database not built; run scripts/init_db.py first")
    c = connect()
    yield c
    c.close()


def _count(conn, table: str) -> int:
    return scalar(conn, "SELECT COUNT(*) FROM {}".format(table)) or 0


def test_every_game_resolves_to_a_stadium(conn):
    """The nflverse stadium_id is the only key that joins. If this breaks,
    every weather panel goes dark at once."""
    if _count(conn, "game") == 0:
        pytest.skip("no games ingested")
    orphans = conn.execute(
        """
        SELECT g.game_id, g.stadium_id FROM game g
        LEFT JOIN stadium s ON s.stadium_id = g.stadium_id
        WHERE s.stadium_id IS NULL
        LIMIT 5
        """
    ).fetchall()
    assert not orphans, "games with no stadium: {}".format([dict(r) for r in orphans])


def test_stored_implied_totals_match_the_formula(conn):
    """Guards against the stored value drifting from the arithmetic."""
    rows = conn.execute(
        """
        SELECT spread_line, total_line, home_implied, away_implied
        FROM odds_game
        WHERE spread_line IS NOT NULL AND total_line IS NOT NULL
        LIMIT 200
        """
    ).fetchall()
    if not rows:
        pytest.skip("no odds ingested")
    for r in rows:
        home, away = implied_totals(r["spread_line"], r["total_line"])
        assert r["home_implied"] == pytest.approx(home)
        assert r["away_implied"] == pytest.approx(away)


def test_implied_totals_favour_the_right_team(conn):
    """The sign check, against real stored rows rather than a fixture."""
    rows = conn.execute(
        """
        SELECT spread_line, home_implied, away_implied FROM odds_game
        WHERE spread_line IS NOT NULL AND ABS(spread_line) >= 3
        LIMIT 100
        """
    ).fetchall()
    if not rows:
        pytest.skip("no odds ingested")
    for r in rows:
        if r["spread_line"] > 0:
            assert r["home_implied"] > r["away_implied"], "home favoured but implied lower"
        else:
            assert r["away_implied"] > r["home_implied"], "away favoured but implied lower"


def test_no_orphan_player_references(conn):
    """Every stat row must resolve to a real internal player."""
    for table in ("player_week_stat", "snap_count", "injury_report"):
        if _count(conn, table) == 0:
            continue
        orphans = scalar(
            conn,
            """
            SELECT COUNT(*) FROM {} t
            LEFT JOIN player p ON p.player_id = t.player_id
            WHERE p.player_id IS NULL
            """.format(table),
        )
        assert orphans == 0, "{}: {} rows point at a missing player".format(table, orphans)


def test_crosswalk_maps_each_external_id_once(conn):
    """(source, source_id) is the primary key; two internal players sharing one
    external id would mean we merged two people."""
    if _count(conn, "player_xref") == 0:
        pytest.skip("crosswalk not built")
    dupes = conn.execute(
        """
        SELECT source, source_id, COUNT(DISTINCT player_id) n
        FROM player_xref GROUP BY source, source_id HAVING n > 1 LIMIT 5
        """
    ).fetchall()
    assert not dupes, "external ids mapped to several players: {}".format(
        [dict(r) for r in dupes]
    )


def test_snap_percentages_are_fractions(conn):
    """nflverse gives offense_pct as 0-1. If a source ever switches to 0-100
    the usage column silently reads '8100%'."""
    if _count(conn, "snap_count") == 0:
        pytest.skip("no snaps ingested")
    worst = scalar(conn, "SELECT MAX(offense_pct) FROM snap_count")
    assert worst is None or worst <= 1.0, "offense_pct looks like a percentage, not a fraction"


def test_timestamps_are_stored_as_utc(conn):
    """Everything in the DB is UTC ISO8601; conversion happens at render."""
    if _count(conn, "game") == 0:
        pytest.skip("no games ingested")
    bad = conn.execute(
        """
        SELECT game_id, kickoff_utc FROM game
        WHERE kickoff_utc IS NOT NULL AND kickoff_utc NOT LIKE '____-__-__T__:__:__Z'
        LIMIT 5
        """
    ).fetchall()
    assert not bad, "non-UTC timestamps: {}".format([dict(r) for r in bad])


def test_every_ingest_wrote_a_log_row(conn):
    """The UI reads freshness from ingest_log; an ingest that does not log is
    invisible when it starts failing."""
    if _count(conn, "ingest_log") == 0:
        pytest.skip("nothing ingested yet")
    unfinished = scalar(
        conn, "SELECT COUNT(*) FROM ingest_log WHERE finished_at IS NULL"
    )
    assert unfinished == 0, "{} ingest runs never recorded a finish".format(unfinished)


def test_injury_panel_falls_back_to_the_latest_published_week(conn):
    """The feed lags the schedule. Querying a week it has not published yet
    returns nothing, which renders as "everyone is healthy" -- and hides that a
    starter was ruled out seven days ago."""
    from ffdash import queries as q

    row = conn.execute(
        "SELECT MAX(season), MAX(week) FROM injury_report"
    ).fetchone()
    if not row or row[0] is None:
        pytest.skip("no injury rows ingested")
    season, latest = int(row[0]), int(row[1])

    # Asking for a week beyond what is published must resolve backwards.
    assert q.latest_injury_week(conn, season, latest + 3) == latest
    assert q.latest_injury_week(conn, season, latest) == latest


def test_a_player_who_missed_last_week_is_surfaced(conn):
    """Sam Darnold, 2026: Out and inactive in Week 2, roster status back to ACT
    in Week 3, and no Week 3 injury row anywhere. He read as fully healthy."""
    from ffdash import queries as q

    if not _count(conn, "roster_status"):
        pytest.skip("roster status not ingested")

    played = conn.execute(
        "SELECT MAX(week) FROM roster_status WHERE status = 'INA'"
    ).fetchone()
    if not played or played[0] is None:
        pytest.skip("no inactives recorded yet")
    week = int(played[0]) + 1

    season = conn.execute("SELECT MAX(season) FROM roster_status").fetchone()[0]
    teams = tuple(
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT team_abbr FROM roster_status "
            "WHERE season = ? AND status = 'INA' AND team_abbr IS NOT NULL LIMIT 4",
            (season,),
        )
    )
    if not teams:
        pytest.skip("no teams with inactives")

    missed = q.previous_absences(conn, int(season), week, teams)
    assert missed, "nobody flagged as having missed the previous week"
    sample = next(iter(missed.values()))
    assert sample["week"] == week - 1
    assert "Did not play week" in sample["detail"]


def test_red_zone_is_null_not_zero_when_untrusted(conn):
    """A team-week whose play-by-play has no red zone plays must store NULL, so
    the page can say 'no data' instead of a confident 0."""
    rows = scalar(
        conn,
        """
        SELECT COUNT(*) FROM player_week_stat
        WHERE rz_touches IS NULL AND inside10_touches IS NOT NULL
        """,
    )
    assert rows == 0, "rz and inside-10 trust flags disagree"
