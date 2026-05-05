from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.analysis.mlb_inventory import build_mlb_data_inventory
from dk_ncaab.collectors.mlb_identity import import_chadwick_player_ids_csv
from dk_ncaab.collectors.mlb_statcast import backfill_statcast_daily, import_statcast_daily_csv
from dk_ncaab.db.models import (
    Base,
    Event,
    EventOddsQuote,
    EventProviderKey,
    EventResult,
    League,
    MlbPlayerGameLog,
    MlbPlayerIdCrosswalk,
    MlbStatcastDaily,
    MlbTeamGameLog,
    OddsQuote,
    Player,
    Team,
)
from dk_ncaab.etl.features import build_features


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _test_input_path(name: str) -> Path:
    path = Path("artifacts/test_inputs")
    path.mkdir(parents=True, exist_ok=True)
    return path / name


def test_import_chadwick_player_ids_links_mlbam_players():
    Session = _session()
    csv_path = _test_input_path("chadwick_player_ids.csv")
    csv_path.write_text(
        "name_first,name_last,key_mlbam,key_retro,key_bbref,key_fangraphs,mlb_played_first\n"
        "Ada,Starter,12345,starta01,starta01,9876,2024\n",
        encoding="utf-8",
    )

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()
        player = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="12345",
            full_name="Ada Starter",
        )
        session.add(player)
        session.commit()

        result = import_chadwick_player_ids_csv(csv_path, session=session)

        assert result.rows_seen == 1
        assert result.rows_upserted == 1
        assert result.linked_to_local_players == 1
        row = session.query(MlbPlayerIdCrosswalk).one()
        assert row.player_id == player.id
        assert row.key_retro == "starta01"
        assert row.key_fangraphs == "9876"


def test_import_chadwick_player_ids_accepts_split_register_directory():
    Session = _session()
    csv_dir = _test_input_path("chadwick_split_register")
    csv_dir.mkdir(parents=True, exist_ok=True)
    (csv_dir / "people-0.csv").write_text(
        "name_first,name_last,key_mlbam,key_retro,key_bbref,key_fangraphs\n"
        "Ada,Starter,12345,starta01,starta01,9876\n",
        encoding="utf-8",
    )
    (csv_dir / "people-a.csv").write_text(
        "name_first,name_last,key_mlbam,key_retro,key_bbref,key_fangraphs\n"
        "Grace,Reliever,67890,relieg01,relieg01,1234\n",
        encoding="utf-8",
    )

    with Session() as session:
        result = import_chadwick_player_ids_csv(csv_dir, session=session)

        assert result.files_imported == 2
        assert result.rows_seen == 2
        assert result.rows_upserted == 2
        assert session.query(MlbPlayerIdCrosswalk).count() == 2


def test_import_statcast_daily_csv_aggregates_and_links():
    Session = _session()
    csv_path = _test_input_path("statcast_daily.csv")
    csv_path.write_text(
        "game_date,batter,pitcher,batter_name,player_name,events,description,launch_speed,"
        "launch_speed_angle,estimated_ba_using_speedangle,estimated_slg_using_speedangle,"
        "estimated_woba_using_speedangle\n"
        "2026-04-20,111,222,Bat One,Pitch One,,called_strike,,,,\n"
        "2026-04-20,111,222,Bat One,Pitch One,single,hit_into_play,101,6,0.8,1.5,0.7\n"
        "2026-04-20,111,222,Bat One,Pitch One,strikeout,swinging_strike,,,,\n",
        encoding="utf-8",
    )

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()
        batter = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="111",
            full_name="Bat One",
        )
        pitcher = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="222",
            full_name="Pitch One",
        )
        session.add_all([batter, pitcher])
        session.commit()

        result = import_statcast_daily_csv(csv_path, session=session)

        assert result.rows_seen == 3
        assert result.daily_rows_upserted == 2
        assert result.linked_to_local_players == 2
        batter_row = session.query(MlbStatcastDaily).filter_by(player_type="batter").one()
        pitcher_row = session.query(MlbStatcastDaily).filter_by(player_type="pitcher").one()
        assert batter_row.plate_appearances == 2
        assert batter_row.hits == 1
        assert batter_row.total_bases == 1
        assert batter_row.hard_hit_rate == 1.0
        assert pitcher_row.strikeouts == 1
        assert pitcher_row.csw_rate == 2 / 3


def test_backfill_statcast_daily_dry_run_uses_bounded_windows():
    result = backfill_statcast_daily(
        start_date=datetime(2026, 4, 8).date(),
        end_date=datetime(2026, 4, 10).date(),
        window_days=2,
        out_dir="artifacts/test_inputs/statcast",
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.totals["downloads"] == 0
    assert [window.csv_path for window in result.windows] == [
        str(Path("artifacts/test_inputs/statcast/statcast_2026-04-08_2026-04-09.csv")),
        str(Path("artifacts/test_inputs/statcast/statcast_2026-04-10_2026-04-10.csv")),
    ]
    assert "game_date_gt=2026-04-08" in result.windows[0].source_url
    assert "game_date_lt=2026-04-09" in result.windows[0].source_url


def test_mlb_inventory_reports_line_history_and_missing_stats():
    Session = _session()
    start = datetime(2026, 4, 20, 23, tzinfo=timezone.utc)

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()
        away = Team(league_id=league.id, name="Away", normalized_name="away")
        home = Team(league_id=league.id, name="Home", normalized_name="home")
        session.add_all([away, home])
        session.flush()
        event = Event(
            league_id=league.id,
            external_event_key="event-1",
            start_time_utc=start,
            home_team_id=home.id,
            away_team_id=away.id,
            status="final",
        )
        session.add(event)
        session.flush()
        session.add_all(
            [
                EventProviderKey(
                    event_id=event.id,
                    sport_key="baseball_mlb",
                    provider="odds_api",
                    provider_event_key="odds-1",
                ),
                EventResult(event_id=event.id, home_score=5, away_score=4, status="final"),
                OddsQuote(
                    event_id=event.id,
                    book="draftkings",
                    market="total",
                    side="over",
                    line=8.5,
                    price_american=-110,
                    implied_probability=0.52,
                    collected_at_utc=start - timedelta(hours=2),
                    source="the_odds_api",
                ),
                EventOddsQuote(
                    event_id=event.id,
                    book="draftkings",
                    market_key="batter_hits",
                    provider_market_key="batter_hits",
                    entity_type="player",
                    participant_name="Unlinked Batter",
                    side="over",
                    line=0.5,
                    price_american=-120,
                    collected_at_utc=start - timedelta(hours=1),
                    source="the_odds_api_event_odds",
                ),
            ]
        )
        session.commit()

        result = build_mlb_data_inventory(session=session)

        assert result.summary["events"]["final"] == 1
        assert result.summary["line_history"]["draftkings_pregame_events"] == 1
        assert result.summary["line_history"]["settled_draftkings_pregame_events"] == 1
        assert result.summary["line_history"]["unlinked_event_specific_player_quotes"] == 1
        assert result.summary["missing_joins"]["final_events_without_team_logs"] == 1


def test_build_features_uses_statcast_for_player_props_and_starters():
    Session = _session()
    start = datetime(2026, 4, 20, 23, tzinfo=timezone.utc)

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()
        away = Team(league_id=league.id, name="Away", normalized_name="away")
        home = Team(league_id=league.id, name="Home", normalized_name="home")
        session.add_all([away, home])
        session.flush()
        batter = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="111",
            full_name="Bat One",
        )
        starter = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="222",
            full_name="Starter One",
        )
        session.add_all([batter, starter])
        session.flush()
        event = Event(
            league_id=league.id,
            external_event_key="event-1",
            start_time_utc=start,
            home_team_id=home.id,
            away_team_id=away.id,
            status="upcoming",
        )
        session.add(event)
        session.flush()
        session.add_all(
            [
                MlbTeamGameLog(
                    event_id=event.id,
                    team_id=home.id,
                    opponent_team_id=away.id,
                    game_date_utc=start - timedelta(days=2),
                    is_home=True,
                    runs_for=4,
                    runs_against=3,
                ),
                MlbPlayerGameLog(
                    event_id=event.id,
                    player_id=starter.id,
                    team_id=home.id,
                    game_date_utc=start - timedelta(days=3),
                    is_home=True,
                    pitching_started=True,
                    innings_pitched_outs=18,
                    earned_runs=2,
                    pitching_hits=5,
                    pitching_base_on_balls=1,
                    pitching_strike_outs=7,
                ),
            ]
        )
        from dk_ncaab.db.models import MlbProbableStarter

        session.add(MlbProbableStarter(
            event_id=event.id,
            team_id=home.id,
            player_id=starter.id,
            is_home=True,
            source="schedule",
            collected_at_utc=start - timedelta(hours=3),
        ))
        for offset in range(1, 4):
            session.add(MlbStatcastDaily(
                player_id=starter.id,
                key_mlbam="222",
                game_date_utc=start - timedelta(days=offset),
                player_type="pitcher",
                source="baseball_savant_csv",
                pitches=90,
                whiff_rate=0.3,
                csw_rate=0.32,
                hard_hit_rate=0.4,
                imported_at_utc=start,
            ))
        for offset in range(1, 6):
            session.add(MlbStatcastDaily(
                player_id=batter.id,
                key_mlbam="111",
                game_date_utc=start - timedelta(days=offset),
                player_type="batter",
                source="baseball_savant_csv",
                xba_mean=0.31,
                xslg_mean=0.52,
                hard_hit_rate=0.45,
                barrel_rate=0.12,
                imported_at_utc=start,
            ))
        session.commit()

        row = build_features(
            session,
            event.id,
            "batter_hits",
            "over",
            participant_name="Bat One",
            participant_entity_type="player",
            participant_player_id=batter.id,
        )

        assert row.home_mlb_starter_statcast_whiff_rate_l3 == 0.3
        assert row.home_mlb_starter_statcast_csw_rate_l3 == 0.32
        assert row.participant_statcast_xba_l5 == 0.31
        assert row.participant_statcast_barrel_rate_l5 == 0.12
