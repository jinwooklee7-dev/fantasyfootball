"""Tests for the logic that would otherwise fail silently.

These cover the things that produce a plausible-looking wrong number rather than
a crash: the implied-total sign convention, roof suppression, and the wind
threshold. A backwards implied total looks completely normal on the page.

    uv run pytest
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from ffdash.odds import describe_spread, implied_totals  # noqa: E402
from ffdash.links import live_url, normalise_base, static_url  # noqa: E402
from ffdash import scoring  # noqa: E402
from ffdash.queries import availability_flag, group_players, position_group  # noqa: E402
from ffdash.timeutil import humanise_age, parse_utc, to_utc_iso  # noqa: E402
from ffdash.weather import compass, roof_applicable, summarise, wind_severity  # noqa: E402


# --------------------------------------------------------------- implied totals

def test_implied_totals_home_favoured():
    """nflverse spread_line is POSITIVE when the home team is favoured.

    Verified against 2020_01_HOU_KC: KC favoured by 9.5 at home, total 53.5,
    and KC won by 14. If this assertion ever flips, every implied total on the
    site is backwards.
    """
    home, away = implied_totals(9.5, 53.5)
    assert (home, away) == (31.5, 22.0)
    assert home - away == pytest.approx(9.5)


def test_implied_totals_home_underdog():
    home, away = implied_totals(-3.0, 44.0)
    assert (home, away) == (20.5, 23.5)
    assert away - home == pytest.approx(3.0)


def test_implied_totals_sum_to_the_total():
    for spread, total in [(9.5, 53.5), (-3.0, 44.0), (0.0, 41.0), (-11.5, 45.5)]:
        home, away = implied_totals(spread, total)
        assert home + away == pytest.approx(total)


def test_implied_totals_missing_inputs():
    assert implied_totals(None, 44.0) == (None, None)
    assert implied_totals(-3.0, None) == (None, None)
    assert implied_totals(None, None) == (None, None)


def test_describe_spread_names_the_favourite():
    assert describe_spread(9.5, "KC", "HOU") == "KC -9.5"
    assert describe_spread(-11.5, "MIA", "KC") == "KC -11.5"
    assert describe_spread(0.0, "NYG", "DAL") == "Pick'em"
    assert describe_spread(None, "NYG", "DAL") == "—"


# --------------------------------------------------------------- roofs

def test_dome_suppresses_weather():
    assert roof_applicable("dome", None)[0] is False
    assert roof_applicable("dome", "dome")[0] is False


def test_fixed_open_sides_always_gets_weather():
    """SoFi. nflverse reports it as 'closed', but the sides are open and wind
    reaches the field -- suppressing the panel would hide the one variable that
    matters."""
    applicable, caveat = roof_applicable("fixed_open_sides", "closed")
    assert applicable is True
    assert caveat == "roof_overhead_sides_open"


def test_retractable_defers_to_gameday_state():
    assert roof_applicable("retractable", "closed")[0] is False
    assert roof_applicable("retractable", "outdoors") == (True, None)
    assert roof_applicable("retractable", None) == (True, "retractable_unknown")


def test_open_air_gets_weather():
    assert roof_applicable("open", "outdoors") == (True, None)
    assert roof_applicable("open", None) == (True, None)


def test_unknown_venue_trusts_the_per_game_field():
    assert roof_applicable(None, "dome")[0] is False
    assert roof_applicable(None, None) == (True, None)


# --------------------------------------------------------------- wind

def test_wind_severity_threshold_is_fifteen():
    """~15 mph sustained is where passing and kicking measurably suffer."""
    assert wind_severity(14.9, 14.9) == "breezy"
    assert wind_severity(15.0, 15.0) == "significant"
    assert wind_severity(4.0, 5.0) == "calm"
    assert wind_severity(None, None) == "unknown"


def test_wind_severity_promotes_on_gusts_alone():
    assert wind_severity(8.0, 26.0) == "significant"


def test_compass_directions():
    assert compass(0) == "N"
    assert compass(90) == "E"
    assert compass(180) == "S"
    assert compass(270) == "W"
    assert compass(None) == "—"


def test_summarise_leads_with_wind():
    row = {
        "applicable": 1, "wind_mph": 13.0, "wind_dir_deg": 70.0,
        "wind_gust_mph": 14.0, "temp_f": 86.0, "conditions": "Partly cloudy",
    }
    text = summarise(row)
    assert text.startswith("13 mph ENE wind")
    # Gust is only 1 mph over sustained; not worth the words.
    assert "gusting" not in text


def test_summarise_sealed_roof():
    assert summarise({"applicable": 0}) == "Roof closed — no weather impact"
    assert summarise(None) == "No forecast yet"


# --------------------------------------------------------------- position groups

def test_quarterbacks_are_judged_on_passing():
    """A QB does not run routes. He must never land in a table with a target
    share column, where he would read as 0%."""
    assert position_group("QB") == "passing"


def test_backs_and_receivers_split():
    assert position_group("RB") == "rushing"
    assert position_group("FB") == "rushing"
    assert position_group("WR") == "receiving"
    assert position_group("TE") == "receiving"


def test_position_group_is_case_insensitive_and_defaults():
    assert position_group("qb") == "passing"
    # An unexpected or missing position must not crash the page.
    assert position_group(None) == "receiving"
    assert position_group("LS") == "receiving"


def test_group_players_orders_and_drops_empties():
    players = [
        {"display_name": "A", "group": position_group("WR")},
        {"display_name": "B", "group": position_group("QB")},
        {"display_name": "C", "group": position_group("TE")},
    ]
    grouped = group_players(players)
    keys = [key for key, _, _ in grouped]
    # Passers first, then backfield, then receivers -- backfield absent here.
    assert keys == ["passing", "receiving"]
    labels = {key: label for key, label, _ in grouped}
    assert labels["passing"] == "Quarterbacks"
    members = {key: [p["display_name"] for p in group] for key, _, group in grouped}
    assert members["passing"] == ["B"]
    assert members["receiving"] == ["A", "C"]


def test_group_players_handles_no_players():
    assert group_players([]) == []


# --------------------------------------------------------------- availability

def test_injured_reserve_beats_a_practice_designation():
    """The whole point: a player on IR must not read as merely Questionable."""
    flag = availability_flag({"status": "RES", "game_status": "Questionable"})
    assert flag["label"] == "IR"
    assert flag["severity"] == "out"


def test_ir_is_flagged_with_no_injury_row_at_all():
    """A player placed on IR vanishes from load_injuries() entirely. That is
    exactly the case that used to render as healthy."""
    flag = availability_flag({"status": "RES"})
    assert flag["label"] == "IR"


def test_inactive_is_flagged():
    assert availability_flag({"status": "INA"})["label"] == "INACTIVE"


def test_designations_without_roster_status():
    assert availability_flag({"game_status": "Out"})["label"] == "OUT"
    assert availability_flag({"game_status": "Questionable"})["severity"] == "doubt"
    assert availability_flag({"practice_status": "DNP"})["label"] == "DNP"
    assert availability_flag({"practice_status": "Limited"})["severity"] == "note"


def test_active_and_healthy_is_not_flagged():
    assert availability_flag({"status": "ACT"}) is None
    assert availability_flag({"status": "ACT", "practice_status": "Full"}) is None
    assert availability_flag({}) is None
    assert availability_flag(None) is None


def test_body_part_is_carried_into_the_detail():
    flag = availability_flag({"game_status": "Questionable", "body_part": "Knee"})
    assert flag["body_part"] == "Knee"
    assert "Knee" in flag["detail"]


# --------------------------------------------------------------- scoring

def test_ppr_is_what_is_stored():
    assert scoring.adjust(17.6, 12, "ppr") == pytest.approx(17.6)


def test_half_and_standard_subtract_the_reception_bonus():
    """Kelce: 17.6 PPR with 12 receptions -> 11.6 half, 5.6 standard."""
    assert scoring.adjust(17.6, 12, "half") == pytest.approx(11.6)
    assert scoring.adjust(17.6, 12, "std") == pytest.approx(5.6)


def test_a_player_with_no_receptions_scores_the_same_everywhere():
    """Quarterbacks. If these ever diverge, the conversion is wrong."""
    for fmt in ("ppr", "half", "std"):
        assert scoring.adjust(25.3, 0, fmt) == pytest.approx(25.3)


def test_missing_reception_count_is_not_silently_full_ppr():
    """Returning the PPR figure unchanged would overstate every other format."""
    assert scoring.adjust(12.0, None, "ppr") == pytest.approx(12.0)
    assert scoring.adjust(12.0, None, "half") is None
    assert scoring.adjust(12.0, None, "std") is None


def test_missing_points_stay_missing():
    for fmt in ("ppr", "half", "std"):
        assert scoring.adjust(None, 5, fmt) is None


def test_format_names_are_forgiving_and_default_safely():
    assert scoring.normalise("PPR") == "ppr"
    assert scoring.normalise("half_ppr") == "half"
    assert scoring.normalise("standard") == "std"
    assert scoring.normalise("nonsense") == "ppr"
    assert scoring.normalise(None) == "ppr"


# --------------------------------------------------------------- links

def test_live_and_static_urls_point_at_the_same_pages():
    live, static = live_url(), static_url("/")
    assert live("game", game_id="X") == "/game/X"
    assert static("game", game_id="X") == "/game/X.html"
    assert live("player", player_id=7) == "/player/7"
    assert static("player", player_id=7) == "/player/7.html"


def test_static_base_prefixes_every_link():
    """A GitHub Pages project site lives at /<repo>/. Miss the prefix and every
    link on all 900 pages is broken."""
    url = static_url("/ffdash/")
    assert url("index") == "/ffdash/"
    assert url("game", game_id="X") == "/ffdash/game/X.html"
    assert url("static", path="app.css") == "/ffdash/static/app.css"


def test_normalise_base_accepts_the_usual_spellings():
    for given in ("/", "", "   "):
        assert normalise_base(given) == "/"
    for given in ("ffdash", "/ffdash", "ffdash/", "/ffdash/"):
        assert normalise_base(given) == "/ffdash/"


def test_normalise_base_rejects_a_url():
    with pytest.raises(ValueError):
        normalise_base("https://example.com/ffdash/")


def test_normalise_base_rejects_a_mangled_windows_path():
    """Git Bash rewrites a leading-slash argument into a Windows path, which
    would otherwise produce a whole site of broken links without complaint."""
    with pytest.raises(ValueError):
        normalise_base("C:/Program Files/Git/ffdash/")


def test_unknown_link_kind_is_an_error():
    for url in (live_url(), static_url("/")):
        with pytest.raises(ValueError):
            url("nonsense")


# --------------------------------------------------------------- time

def test_timestamps_round_trip_as_utc():
    iso = "2026-09-21T17:00:00Z"
    dt = parse_utc(iso)
    assert dt is not None
    assert to_utc_iso(dt) == iso


def test_naive_datetimes_are_assumed_utc():
    from datetime import datetime

    assert to_utc_iso(datetime(2026, 9, 21, 17, 0)) == "2026-09-21T17:00:00Z"


def test_humanise_age_handles_missing():
    assert humanise_age(None) == "never"
    assert humanise_age("not a timestamp") == "never"
