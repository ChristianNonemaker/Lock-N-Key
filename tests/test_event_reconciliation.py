from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dk_ncaab.db.event_reconciliation import (
    apply_mlb_event_reconciliation,
    plan_mlb_event_reconciliation,
)
from dk_ncaab.db.models import (
    Base,
    Event,
    EventProviderKey,
    EventResult,
    League,
    MlbEnvironmentSnapshot,
    MlbEventVenue,
    MlbPlayerGameLog,
    MlbProbableStarter,
    MlbStatsRawPayload,
    MlbTeamGameLog,
    MlbVenue,
    OddsQuote,
    Player,
    SplitsQuote,
    Team,
)


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return Session()


def test_apply_mlb_event_reconciliation_merges_child_rows_and_deletes_duplicate():
    session = _session()
    try:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()

        home = Team(league_id=league.id, name="Cubs", normalized_name="cubs")
        away = Team(league_id=league.id, name="Cardinals", normalized_name="cardinals")
        session.add_all([home, away])
        session.flush()

        venue = MlbVenue(
            provider="mlb_stats_api",
            provider_venue_key="17",
            name="Wrigley Field",
            source="test",
        )
        session.add(venue)
        session.flush()

        home_starter = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="home-sp",
            full_name="Home Starter",
        )
        away_starter = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="away-sp",
            full_name="Away Starter",
        )
        session.add_all([home_starter, away_starter])
        session.flush()

        start = datetime(2026, 4, 24, 18, 20, tzinfo=timezone.utc)
        canonical = Event(
            league_id=league.id,
            external_event_key="espn:baseball_mlb:1001",
            start_time_utc=start,
            home_team_id=home.id,
            away_team_id=away.id,
            status="final",
            first_seen_at_utc=start - timedelta(hours=6),
        )
        duplicate = Event(
            league_id=league.id,
            external_event_key="odds-1001",
            start_time_utc=start + timedelta(minutes=10),
            home_team_id=home.id,
            away_team_id=away.id,
            status="upcoming",
            first_seen_at_utc=start - timedelta(hours=5),
        )
        session.add_all([canonical, duplicate])
        session.flush()

        session.add_all(
            [
                EventProviderKey(
                    event_id=canonical.id,
                    sport_key="baseball_mlb",
                    provider="espn",
                    provider_event_key="espn:baseball_mlb:1001",
                ),
                EventProviderKey(
                    event_id=canonical.id,
                    sport_key="baseball_mlb",
                    provider="mlb_stats_api",
                    provider_event_key="provider-1001",
                ),
                EventProviderKey(
                    event_id=duplicate.id,
                    sport_key="baseball_mlb",
                    provider="odds_api",
                    provider_event_key="odds-1001",
                ),
                EventResult(
                    event_id=canonical.id,
                    home_score=6,
                    away_score=3,
                    status="final",
                    completed_at_utc=start + timedelta(hours=3),
                ),
                OddsQuote(
                    event_id=canonical.id,
                    book="draftkings",
                    market="moneyline",
                    side="home",
                    line=None,
                    price_american=-120,
                    implied_probability=0.545,
                    collected_at_utc=start - timedelta(hours=2),
                    source="the_odds_api",
                ),
                OddsQuote(
                    event_id=duplicate.id,
                    book="draftkings",
                    market="moneyline",
                    side="home",
                    line=None,
                    price_american=-120,
                    implied_probability=0.545,
                    collected_at_utc=start - timedelta(hours=2),
                    source="the_odds_api",
                ),
                OddsQuote(
                    event_id=duplicate.id,
                    book="draftkings",
                    market="spread",
                    side="home",
                    line=-1.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=start - timedelta(hours=1),
                    source="the_odds_api",
                ),
                SplitsQuote(
                    event_id=duplicate.id,
                    market="moneyline",
                    side="home",
                    bets_pct=62.0,
                    handle_pct=71.0,
                    collected_at_utc=start - timedelta(minutes=50),
                    source="action",
                ),
                MlbStatsRawPayload(
                    event_id=duplicate.id,
                    collected_at_utc=start - timedelta(hours=4),
                    endpoint="/game/1001/boxscore",
                    provider_event_key="provider-1001",
                    payload_json={"ok": True},
                ),
                MlbEventVenue(
                    event_id=duplicate.id,
                    venue_id=venue.id,
                    provider="mlb_stats_api",
                    collected_at_utc=start - timedelta(hours=3),
                ),
                MlbEnvironmentSnapshot(
                    event_id=duplicate.id,
                    venue_id=venue.id,
                    provider="nws_api",
                    collected_at_utc=start - timedelta(hours=2),
                    forecast_for_utc=start,
                    temperature_f=72.0,
                    wind_mph=12.0,
                    wind_direction="NW",
                ),
                MlbTeamGameLog(
                    event_id=duplicate.id,
                    team_id=home.id,
                    game_date_utc=start,
                    is_home=True,
                    opponent_team_id=away.id,
                    runs_for=6,
                    runs_against=3,
                    source="mlb_stats_api",
                ),
                MlbTeamGameLog(
                    event_id=duplicate.id,
                    team_id=away.id,
                    game_date_utc=start,
                    is_home=False,
                    opponent_team_id=home.id,
                    runs_for=3,
                    runs_against=6,
                    source="mlb_stats_api",
                ),
                MlbProbableStarter(
                    event_id=duplicate.id,
                    team_id=home.id,
                    player_id=home_starter.id,
                    is_home=True,
                    source="schedule",
                    collected_at_utc=start - timedelta(hours=6),
                ),
                MlbProbableStarter(
                    event_id=duplicate.id,
                    team_id=away.id,
                    player_id=away_starter.id,
                    is_home=False,
                    source="schedule",
                    collected_at_utc=start - timedelta(hours=6),
                ),
                MlbPlayerGameLog(
                    event_id=duplicate.id,
                    player_id=home_starter.id,
                    team_id=home.id,
                    game_date_utc=start,
                    is_home=True,
                    pitching_started=True,
                    innings_pitched_outs=18,
                    earned_runs=2,
                    source="mlb_stats_api",
                ),
            ]
        )
        session.commit()

        plans = plan_mlb_event_reconciliation(session)
        assert len(plans) == 1
        assert plans[0].mergeable is True
        assert plans[0].canonical_event_id == canonical.id
        assert plans[0].duplicate_event_ids == [duplicate.id]

        summary = apply_mlb_event_reconciliation(session, plans)
        session.commit()

        assert summary.merged_groups == 1
        assert summary.deleted_events == 1
        assert session.get(Event, duplicate.id) is None
        merged = session.get(Event, canonical.id)
        assert merged is not None
        assert session.query(Event).count() == 1
        assert session.query(EventProviderKey).filter_by(event_id=canonical.id).count() == 3
        assert session.query(OddsQuote).filter_by(event_id=canonical.id).count() == 2
        assert session.query(SplitsQuote).filter_by(event_id=canonical.id).count() == 1
        assert session.query(MlbStatsRawPayload).filter_by(event_id=canonical.id).count() == 1
        assert session.query(MlbEventVenue).filter_by(event_id=canonical.id).count() == 1
        assert session.query(MlbEnvironmentSnapshot).filter_by(event_id=canonical.id).count() == 1
        assert session.query(MlbTeamGameLog).filter_by(event_id=canonical.id).count() == 2
        assert session.query(MlbProbableStarter).filter_by(event_id=canonical.id).count() == 2
        assert session.query(MlbPlayerGameLog).filter_by(event_id=canonical.id).count() == 1
        assert summary.rows_deduped["odds_quotes"] == 1
    finally:
        session.close()


def test_plan_mlb_event_reconciliation_skips_conflicting_same_provider_keys():
    session = _session()
    try:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()

        home = Team(league_id=league.id, name="Dodgers", normalized_name="dodgers")
        away = Team(league_id=league.id, name="Giants", normalized_name="giants")
        session.add_all([home, away])
        session.flush()

        start = datetime(2026, 4, 24, 2, 0, tzinfo=timezone.utc)
        ev1 = Event(
            league_id=league.id,
            external_event_key="espn:baseball_mlb:2001",
            start_time_utc=start,
            home_team_id=home.id,
            away_team_id=away.id,
            status="upcoming",
        )
        ev2 = Event(
            league_id=league.id,
            external_event_key="espn:baseball_mlb:2002",
            start_time_utc=start + timedelta(minutes=20),
            home_team_id=home.id,
            away_team_id=away.id,
            status="upcoming",
        )
        session.add_all([ev1, ev2])
        session.flush()
        session.add_all(
            [
                EventProviderKey(
                    event_id=ev1.id,
                    sport_key="baseball_mlb",
                    provider="espn",
                    provider_event_key="espn:baseball_mlb:2001",
                ),
                EventProviderKey(
                    event_id=ev2.id,
                    sport_key="baseball_mlb",
                    provider="espn",
                    provider_event_key="espn:baseball_mlb:2002",
                ),
            ]
        )
        session.commit()

        plans = plan_mlb_event_reconciliation(session)
        assert len(plans) == 1
        assert plans[0].mergeable is False
        assert "conflicting_espn_keys" in plans[0].reasons
    finally:
        session.close()
