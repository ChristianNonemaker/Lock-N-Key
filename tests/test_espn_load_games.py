from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.collectors import load_games
from dk_ncaab.collectors.load_games import (
    _ensure_league,
    _espn_scoreboard_params,
    _process_espn_event,
    _sport_from_event,
)
from dk_ncaab.db.models import Base, Event, EventResult, League, Team
from dk_ncaab.db.models import TeamAlias
from dk_ncaab.etl.normalize import normalize_team_name


ACTIVE_SPORTS = [
    ("basketball_ncaab", "ncaab", "espn:1001", {"groups": "50"}),
    ("americanfootball_ncaaf", "ncaaf", "espn:americanfootball_ncaaf:1001", {"groups": "80"}),
    ("americanfootball_nfl", "nfl", "espn:americanfootball_nfl:1001", {}),
    ("baseball_mlb", "mlb", "espn:baseball_mlb:1001", {}),
]


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(session_factory):
    sess = session_factory()
    yield sess
    sess.close()


def _espn_event(
    *,
    event_id: str = "1001",
    state: str = "pre",
    home: str = "Home Team",
    away: str = "Away Team",
    home_score: int | None = None,
    away_score: int | None = None,
) -> dict:
    home_competitor = {
        "homeAway": "home",
        "team": {"displayName": home},
    }
    away_competitor = {
        "homeAway": "away",
        "team": {"displayName": away},
    }
    if home_score is not None:
        home_competitor["score"] = str(home_score)
    if away_score is not None:
        away_competitor["score"] = str(away_score)

    return {
        "id": event_id,
        "date": "2026-04-20T23:30Z",
        "status": {"type": {"state": state}},
        "competitions": [
            {
                "competitors": [
                    home_competitor,
                    away_competitor,
                ]
            }
        ],
    }


@pytest.mark.parametrize("sport,league_key,external_key,extra_params", ACTIVE_SPORTS)
def test_espn_params_and_event_create_for_active_sports(
    session,
    sport,
    league_key,
    external_key,
    extra_params,
):
    params = _espn_scoreboard_params("20260420", sport)
    assert params["dates"] == "20260420"
    assert params["limit"] == "200"
    for key, value in extra_params.items():
        assert params[key] == value
    if "groups" not in extra_params:
        assert "groups" not in params

    league = _ensure_league(session, sport)
    created, score_added, status_updated = _process_espn_event(
        session,
        _espn_event(state="pre"),
        league,
        sport,
    )
    session.commit()

    assert created == 1
    assert score_added == 0
    assert status_updated == 0

    event = session.query(Event).filter_by(external_event_key=external_key).one()
    assert event.status == "upcoming"
    assert event.league.key == league_key
    assert _sport_from_event(event) == sport
    assert session.query(Team).count() == 2


@pytest.mark.parametrize("sport,league_key,external_key,_extra_params", ACTIVE_SPORTS)
def test_espn_final_update_adds_result_once(
    session,
    sport,
    league_key,
    external_key,
    _extra_params,
):
    league = _ensure_league(session, sport)
    assert _process_espn_event(session, _espn_event(state="pre"), league, sport) == (1, 0, 0)
    session.commit()

    final = _espn_event(state="post", home_score=78, away_score=74)
    created, score_added, status_updated = _process_espn_event(session, final, league, sport)
    session.commit()

    assert created == 0
    assert score_added == 1
    assert status_updated == 1

    event = session.query(Event).filter_by(external_event_key=external_key).one()
    assert event.league.key == league_key
    assert event.status == "final"
    result = session.query(EventResult).filter_by(event_id=event.id).one()
    assert result.home_score == 78
    assert result.away_score == 74

    assert _process_espn_event(session, final, league, sport) == (0, 0, 0)
    session.commit()
    assert session.query(EventResult).filter_by(event_id=event.id).count() == 1


def test_espn_malformed_event_is_ignored(session):
    league = _ensure_league(session, "basketball_ncaab")
    assert _process_espn_event(session, {"id": "bad", "competitions": []}, league, "basketball_ncaab") == (0, 0, 0)
    assert session.query(Event).count() == 0


def test_sport_from_event_uses_league_when_external_key_is_not_espn(session):
    league = League(key="nfl", name="NFL")
    session.add(league)
    session.flush()
    home = Team(league_id=league.id, name="Home", normalized_name="home")
    away = Team(league_id=league.id, name="Away", normalized_name="away")
    session.add_all([home, away])
    session.flush()
    event = Event(
        league_id=league.id,
        external_event_key="odds-api-event",
        start_time_utc=datetime(2026, 4, 20, 23, 30, tzinfo=timezone.utc),
        home_team_id=home.id,
        away_team_id=away.id,
    )
    session.add(event)
    session.commit()

    assert _sport_from_event(event) == "americanfootball_nfl"


def test_load_games_for_date_fans_out_active_sports_no_network(monkeypatch, session_factory):
    calls: list[tuple[str, str]] = []

    def fake_fetch(date_str: str, sport: str) -> list[dict]:
        calls.append((date_str, sport))
        return [
            _espn_event(
                event_id=f"{len(calls)}001",
                home=f"{sport} Home",
                away=f"{sport} Away",
            )
        ]

    monkeypatch.setattr(load_games, "SessionLocal", session_factory)
    monkeypatch.setattr(load_games, "_fetch_espn_scoreboard", fake_fetch)

    created = load_games.load_games_for_date(
        datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    )

    assert created == 4
    assert calls == [( "20260420", sport) for sport, *_ in ACTIVE_SPORTS]
    with session_factory() as sess:
        assert sess.query(Event).count() == 4
        assert sess.query(League).count() == 4


def test_load_games_for_date_specific_sport_is_idempotent_no_network(
    monkeypatch,
    session_factory,
):
    calls: list[tuple[str, str]] = []

    def fake_fetch(date_str: str, sport: str) -> list[dict]:
        calls.append((date_str, sport))
        return [_espn_event(event_id="3001", home="Mets", away="Braves")]

    monkeypatch.setattr(load_games, "SessionLocal", session_factory)
    monkeypatch.setattr(load_games, "_fetch_espn_scoreboard", fake_fetch)
    target = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)

    assert load_games.load_games_for_date(target, sport="baseball_mlb") == 1
    assert load_games.load_games_for_date(target, sport="baseball_mlb") == 0
    assert calls == [("20260420", "baseball_mlb"), ("20260420", "baseball_mlb")]

    with session_factory() as sess:
        assert sess.query(Event).count() == 1
        assert sess.query(Team).count() == 2


def test_update_scores_espn_batches_by_sport_and_date(monkeypatch, session_factory):
    with session_factory() as sess:
        league = _ensure_league(sess, "americanfootball_nfl")
        for event_id, home, away in [
            ("4001", "Lions", "Packers"),
            ("4002", "Bears", "Vikings"),
        ]:
            _process_espn_event(
                sess,
                _espn_event(event_id=event_id, state="pre", home=home, away=away),
                league,
                "americanfootball_nfl",
            )
        sess.commit()

    calls: list[tuple[str, str]] = []

    def fake_fetch(date_str: str, sport: str) -> list[dict]:
        calls.append((date_str, sport))
        return [
            _espn_event(
                event_id="4001",
                state="post",
                home="Lions",
                away="Packers",
                home_score=27,
                away_score=24,
            ),
            _espn_event(
                event_id="4002",
                state="post",
                home="Bears",
                away="Vikings",
                home_score=20,
                away_score=17,
            ),
        ]

    monkeypatch.setattr(load_games, "SessionLocal", session_factory)
    monkeypatch.setattr(load_games, "_fetch_espn_scoreboard", fake_fetch)

    assert load_games.update_scores_espn("americanfootball_nfl") == 2
    assert calls == [("20260420", "americanfootball_nfl")]

    with session_factory() as sess:
        assert sess.query(EventResult).count() == 2
        assert sess.query(Event).filter_by(status="final").count() == 2


def test_espn_uses_seed_aliases_without_creating_duplicate_teams(session):
    league = _ensure_league(session, "basketball_ncaab")
    duke = Team(league_id=league.id, name="Duke", normalized_name="duke")
    unc = Team(league_id=league.id, name="North Carolina", normalized_name="north carolina")
    session.add_all([duke, unc])
    session.flush()
    session.add_all(
        [
            TeamAlias(
                team_id=duke.id,
                alias=normalize_team_name("Duke Blue Devils"),
                source="seed",
            ),
            TeamAlias(
                team_id=unc.id,
                alias=normalize_team_name("North Carolina Tar Heels"),
                source="seed",
            ),
        ]
    )
    session.flush()

    created, score_added, status_updated = _process_espn_event(
        session,
        _espn_event(
            event_id="5001",
            home="Duke Blue Devils",
            away="North Carolina Tar Heels",
        ),
        league,
        "basketball_ncaab",
    )
    session.commit()

    assert (created, score_added, status_updated) == (1, 0, 0)
    assert session.query(Team).count() == 2
    event = session.query(Event).filter_by(external_event_key="espn:5001").one()
    assert event.home_team_id == duke.id
    assert event.away_team_id == unc.id


def test_alias_resolution_is_league_scoped_for_espn(session):
    ncaab = League(key="ncaab", name="NCAA Men's Basketball")
    nfl = League(key="nfl", name="NFL")
    session.add_all([ncaab, nfl])
    session.flush()
    louisville = Team(
        league_id=ncaab.id,
        name="Louisville Cardinals",
        normalized_name="louisville cardinals",
    )
    arizona = Team(
        league_id=nfl.id,
        name="Arizona Cardinals",
        normalized_name="arizona cardinals",
    )
    session.add_all([louisville, arizona])
    session.flush()
    session.add_all(
        [
            TeamAlias(team_id=louisville.id, alias="cardinals", source="espn"),
            TeamAlias(team_id=arizona.id, alias="cardinals", source="espn"),
        ]
    )
    session.flush()

    created, _, _ = _process_espn_event(
        session,
        _espn_event(event_id="6001", home="Cardinals", away="Bears"),
        nfl,
        "americanfootball_nfl",
    )
    session.commit()

    assert created == 1
    event = session.query(Event).filter_by(external_event_key="espn:americanfootball_nfl:6001").one()
    assert event.home_team_id == arizona.id


def test_non_away_competitor_does_not_silently_become_away(session):
    league = _ensure_league(session, "basketball_ncaab")
    payload = _espn_event()
    payload["competitions"][0]["competitors"] = [
        {"homeAway": "home", "team": {"displayName": "Home"}, "score": "10"},
        {"homeAway": "neutral", "team": {"displayName": "Neutral"}, "score": "8"},
    ]

    assert _process_espn_event(session, payload, league, "basketball_ncaab") == (0, 0, 0)
    assert session.query(Event).count() == 0


def test_final_status_does_not_regress_to_live(session):
    league = _ensure_league(session, "basketball_ncaab")
    final = _espn_event(state="post", home_score=70, away_score=65)
    assert _process_espn_event(session, final, league, "basketball_ncaab") == (1, 1, 0)
    session.commit()

    live_replay = _espn_event(state="in")
    assert _process_espn_event(session, live_replay, league, "basketball_ncaab") == (0, 0, 0)
    session.commit()

    event = session.query(Event).filter_by(external_event_key="espn:1001").one()
    assert event.status == "final"


def test_update_scores_revisits_final_events_missing_results(monkeypatch, session_factory):
    with session_factory() as sess:
        league = _ensure_league(sess, "baseball_mlb")
        _process_espn_event(sess, _espn_event(event_id="7001", state="post"), league, "baseball_mlb")
        sess.commit()
        assert sess.query(Event).filter_by(status="final").count() == 1
        assert sess.query(EventResult).count() == 0

    calls: list[tuple[str, str]] = []

    def fake_fetch(date_str: str, sport: str) -> list[dict]:
        calls.append((date_str, sport))
        return [
            _espn_event(
                event_id="7001",
                state="post",
                home_score=5,
                away_score=3,
            )
        ]

    monkeypatch.setattr(load_games, "SessionLocal", session_factory)
    monkeypatch.setattr(load_games, "_fetch_espn_scoreboard", fake_fetch)

    assert load_games.update_scores_espn("baseball_mlb") == 1
    assert calls == [("20260420", "baseball_mlb")]
    with session_factory() as sess:
        assert sess.query(EventResult).count() == 1
