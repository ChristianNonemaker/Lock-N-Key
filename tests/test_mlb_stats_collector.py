from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.collectors.mlb_stats import collect_mlb_stats
from dk_ncaab.db.models import (
    Base,
    Event,
    EventProviderKey,
    EventResult,
    League,
    MlbEventVenue,
    MlbPlayerGameLog,
    MlbProbableStarter,
    MlbStatsRawPayload,
    MlbTeamGameLog,
    MlbVenue,
    Player,
    Team,
)


class FakeMlbClient:
    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, params=None, timeout=30):
        self.calls.append(url)
        request = httpx.Request("GET", url)
        if url.endswith("/schedule"):
            return httpx.Response(200, json=_schedule_payload(), request=request)
        if url.endswith("/game/777/boxscore"):
            return httpx.Response(200, json=_boxscore_payload(), request=request)
        raise AssertionError(f"Unexpected URL: {url}")

    def close(self):
        return None


def _schedule_payload() -> dict:
    return {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": 777,
                        "gameDate": "2026-04-20T23:10:00Z",
                        "venue": {"id": 17, "name": "Wrigley Field"},
                        "status": {"abstractGameState": "Final", "detailedState": "Final"},
                        "teams": {
                            "away": {
                                "score": 3,
                                "team": {"id": 111, "name": "Away Bats"},
                                "probablePitcher": {"id": 9001, "fullName": "Away Starter"},
                            },
                            "home": {
                                "score": 5,
                                "team": {"id": 222, "name": "Home Bats"},
                                "probablePitcher": {"id": 9002, "fullName": "Home Starter"},
                            },
                        },
                    }
                ]
            }
        ]
    }


def _player(person_id: int, name: str, batting: dict | None = None, pitching: dict | None = None):
    return {
        "person": {"id": person_id, "fullName": name},
        "position": {"abbreviation": "P" if pitching else "1B"},
        "battingOrder": "100" if batting else None,
        "stats": {"batting": batting or {}, "pitching": pitching or {}},
    }


def _boxscore_payload() -> dict:
    home_pitching = {
        "inningsPitched": "6.0",
        "hits": 4,
        "runs": 2,
        "earnedRuns": 2,
        "baseOnBalls": 1,
        "strikeOuts": 7,
        "homeRuns": 1,
        "pitchesThrown": 91,
    }
    away_pitching = {
        "inningsPitched": "5.2",
        "hits": 7,
        "runs": 4,
        "earnedRuns": 4,
        "baseOnBalls": 2,
        "strikeOuts": 5,
        "homeRuns": 2,
        "pitchesThrown": 88,
    }
    return {
        "teams": {
            "home": {
                "team": {"id": 222, "name": "Home Bats"},
                "teamStats": {
                    "batting": {"runs": 5, "hits": 9, "homeRuns": 2, "baseOnBalls": 3, "strikeOuts": 8},
                    "pitching": {"inningsPitched": "9.0", "runs": 3},
                },
                "batters": [9002],
                "pitchers": [9002, 9010],
                "players": {
                    "ID9002": _player(9002, "Home Starter", {"atBats": 3, "hits": 1}, home_pitching),
                    "ID9010": _player(9010, "Home Reliever", pitching={"inningsPitched": "3.0"}),
                },
            },
            "away": {
                "team": {"id": 111, "name": "Away Bats"},
                "teamStats": {
                    "batting": {"runs": 3, "hits": 6, "homeRuns": 1, "baseOnBalls": 2, "strikeOuts": 9},
                    "pitching": {"inningsPitched": "8.0", "runs": 5},
                },
                "batters": [9001],
                "pitchers": [9001, 9011],
                "players": {
                    "ID9001": _player(9001, "Away Starter", {"atBats": 2, "hits": 0}, away_pitching),
                    "ID9011": _player(9011, "Away Reliever", pitching={"inningsPitched": "2.1"}),
                },
            },
        }
    }


def test_collect_mlb_stats_upserts_provider_backed_logs_idempotently():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    client = FakeMlbClient()

    with Session() as session:
        result = collect_mlb_stats(
            start_date=date(2026, 4, 20),
            end_date=date(2026, 4, 20),
            client=client,
            session=session,
        )
        assert result.schedule_games == 1
        assert result.events_created == 1
        assert result.boxscores_fetched == 1
        assert result.team_logs_upserted == 2
        assert result.player_logs_upserted == 4
        assert result.probable_starters_upserted == 2

        assert session.query(Event).count() == 1
        assert session.query(EventProviderKey).count() == 1
        assert session.query(EventResult).count() == 1
        assert session.query(MlbVenue).count() == 1
        assert session.query(MlbEventVenue).count() == 1
        assert session.query(Player).count() == 4
        assert session.query(MlbTeamGameLog).count() == 2
        assert session.query(MlbPlayerGameLog).count() == 4
        assert session.query(MlbProbableStarter).count() == 2
        assert session.query(MlbStatsRawPayload).count() == 2
        venue = session.query(MlbVenue).one()
        assert venue.name == "Wrigley Field"
        assert venue.latitude is not None
        assert venue.roof_type == "open"

        again = collect_mlb_stats(
            start_date=date(2026, 4, 20),
            end_date=date(2026, 4, 20),
            client=client,
            session=session,
        )
        assert again.events_created == 0
        assert again.team_logs_upserted == 0
        assert again.player_logs_upserted == 0
        assert session.query(Event).count() == 1
        assert session.query(MlbTeamGameLog).count() == 2
        assert session.query(MlbPlayerGameLog).count() == 4


def test_collect_mlb_stats_respects_boxscore_cap():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    client = FakeMlbClient()

    with Session() as session:
        result = collect_mlb_stats(
            start_date=date(2026, 4, 20),
            end_date=date(2026, 4, 20),
            client=client,
            session=session,
            max_boxscores=0,
            request_delay_sec=0,
        )

        assert result.schedule_games == 1
        assert result.boxscores_fetched == 0
        assert result.team_logs_upserted == 0
        assert result.player_logs_upserted == 0
        assert session.query(MlbProbableStarter).count() == 2
        assert session.query(MlbTeamGameLog).count() == 0
        assert all("/boxscore" not in call for call in client.calls)
        assert session.query(MlbStatsRawPayload).count() == 1


def test_collect_mlb_stats_reuses_existing_same_game_event_with_lineage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    client = FakeMlbClient()

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()

        away = Team(league_id=league.id, name="Away Bats", normalized_name="away bats")
        home = Team(league_id=league.id, name="Home Bats", normalized_name="home bats")
        session.add_all([away, home])
        session.flush()

        existing = Event(
            league_id=league.id,
            external_event_key="odds-777",
            start_time_utc=datetime(2026, 4, 20, 23, 20, tzinfo=timezone.utc),
            home_team_id=home.id,
            away_team_id=away.id,
            status="upcoming",
        )
        session.add(existing)
        session.flush()
        session.add(
            EventProviderKey(
                event_id=existing.id,
                sport_key="baseball_mlb",
                provider="odds_api",
                provider_event_key="odds-777",
            )
        )
        session.commit()
        session.close()

    with Session() as session:
        result = collect_mlb_stats(
            start_date=date(2026, 4, 20),
            end_date=date(2026, 4, 20),
            client=client,
            session=session,
        )

        assert result.events_created == 0
        assert session.query(Event).count() == 1
        assert session.query(EventProviderKey).filter_by(event_id=existing.id).count() == 2
        assert session.query(EventProviderKey).filter_by(
            provider="mlb_stats_api",
            provider_event_key="777",
        ).one().event_id == existing.id
