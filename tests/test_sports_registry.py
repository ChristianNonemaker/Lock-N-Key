import pytest
from fastapi import HTTPException

from api.main import _league_key_for_request_sport
from dk_ncaab.collectors.load_games import _active_sports as active_schedule_sports
from dk_ncaab.config.props import provider_prop_market_keys_for_sport
from dk_ncaab.config.settings import OddsApiCfg, ScheduleCfg
from dk_ncaab.config.sports import (
    default_odds_sport_keys,
    default_schedule_sport_keys,
    espn_scoreboard_params_for,
    espn_scoreboard_url_for,
    feature_enrichers_for,
    get_sport,
    league_key_for_sport,
    odds_api_sport_for,
    odds_collectable_sport_keys,
    sport_for_league_key,
    ui_sport_choices,
    ui_sport_keys,
    validate_odds_sports,
    validate_schedule_sports,
)


ACTIVE_SPORTS = [
    "basketball_ncaab",
    "americanfootball_ncaaf",
    "americanfootball_nfl",
    "baseball_mlb",
]

ACTIVE_CHOICES = [
    ("basketball_ncaab", "NCAAB"),
    ("americanfootball_ncaaf", "NCAAF"),
    ("americanfootball_nfl", "NFL"),
    ("baseball_mlb", "MLB"),
]


def test_registry_defaults_are_quota_safe():
    assert default_schedule_sport_keys() == ACTIVE_SPORTS
    assert ScheduleCfg().active_sports() == ACTIVE_SPORTS
    assert active_schedule_sports(None) == ACTIVE_SPORTS

    assert default_odds_sport_keys() == ["baseball_mlb"]
    assert OddsApiCfg().active_sports() == ["baseball_mlb"]
    assert set(odds_collectable_sport_keys()) == set(ACTIVE_SPORTS)


def test_registry_strips_and_dedupes_overrides():
    assert validate_schedule_sports([
        " basketball_ncaab ",
        "basketball_ncaab",
        "",
        "baseball_mlb",
    ]) == ["basketball_ncaab", "baseball_mlb"]
    assert OddsApiCfg(sports=[" baseball_mlb ", "baseball_mlb"]).active_sports() == [
        "baseball_mlb"
    ]


def test_empty_odds_sports_falls_back_to_legacy_sport():
    cfg = OddsApiCfg(sport="basketball_ncaab", sports=[])
    assert cfg.active_sports() == ["basketball_ncaab"]


def test_rejects_unknown_or_disabled_collection():
    with pytest.raises(ValueError):
        validate_schedule_sports(["Basketball_NCAAB"])
    with pytest.raises(ValueError):
        validate_schedule_sports(["baseball_npb"])
    with pytest.raises(ValueError):
        validate_odds_sports(["baseball_npb"])
    with pytest.raises(ValueError):
        active_schedule_sports("baseball_npb")


def test_active_sports_have_espn_and_odds_mappings():
    expected_leagues = {
        "basketball_ncaab": "ncaab",
        "americanfootball_ncaaf": "ncaaf",
        "americanfootball_nfl": "nfl",
        "baseball_mlb": "mlb",
    }
    for sport, league_key in expected_leagues.items():
        assert league_key_for_sport(sport) == league_key
        assert sport_for_league_key(league_key) == sport
        assert odds_api_sport_for(sport) == sport
        assert espn_scoreboard_url_for(sport).startswith("https://site.api.espn.com/")
        assert espn_scoreboard_params_for(sport, "20260420")["dates"] == "20260420"

    mlb = get_sport("baseball_mlb")
    assert mlb.props_source == "the_odds_api_event_odds"
    assert provider_prop_market_keys_for_sport("baseball_mlb") == [
        "team_totals",
        "pitcher_strikeouts",
        "batter_hits",
        "batter_total_bases",
    ]


def test_planned_nba_and_soccer_are_disabled_placeholders():
    nba = get_sport("basketball_nba")
    soccer = get_sport("soccer_epl")

    assert nba.status == "planned"
    assert soccer.status == "planned"
    assert not nba.schedule_enabled_by_default
    assert not nba.odds_enabled_by_default
    assert not nba.odds_collectable
    assert not nba.ui_enabled
    assert not soccer.schedule_enabled_by_default
    assert not soccer.odds_enabled_by_default
    assert not soccer.odds_collectable
    assert not soccer.ui_enabled

    assert "basketball_nba" not in default_schedule_sport_keys()
    assert "soccer_epl" not in default_schedule_sport_keys()
    assert "basketball_nba" not in default_odds_sport_keys()
    assert "soccer_epl" not in default_odds_sport_keys()
    assert "basketball_nba" not in ui_sport_keys()
    assert "soccer_epl" not in ui_sport_keys()
    with pytest.raises(ValueError):
        odds_api_sport_for("basketball_nba")
    with pytest.raises(ValueError):
        espn_scoreboard_url_for("soccer_epl")


def test_ui_eligibility_comes_from_registry():
    assert ui_sport_choices() == ACTIVE_CHOICES
    assert ui_sport_keys() == ACTIVE_SPORTS
    assert _league_key_for_request_sport("basketball_ncaab") == "ncaab"

    with pytest.raises(HTTPException):
        _league_key_for_request_sport("basketball_nba")


def test_feature_enrichers_are_sport_aware():
    assert set(feature_enrichers_for("basketball_ncaab")) == {
        "odds_snapshots",
        "action_network_splits",
        "kenpom",
        "ap_rankings",
    }
    for sport in ("americanfootball_ncaaf", "americanfootball_nfl"):
        enrichers = set(feature_enrichers_for(sport))
        assert enrichers == {"odds_snapshots"}
        assert "kenpom" not in enrichers
        assert "ap_rankings" not in enrichers
    assert set(feature_enrichers_for("baseball_mlb")) == {
        "odds_snapshots",
        "mlb_stats",
    }
