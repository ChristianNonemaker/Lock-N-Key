from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dk_ncaab.analysis.mlb_market_history import (
    build_mlb_market_history_frame,
    generate_mlb_market_history_artifact,
)
from dk_ncaab.db.models import (
    Base,
    Event,
    EventOddsQuote,
    EventResult,
    League,
    MlbPlayerGameLog,
    MlbTeamGameLog,
    Player,
    Team,
)

_TEST_OUT = Path("artifacts/test_outputs/mlb_market_history")


def _case_dir(name: str) -> Path:
    path = _TEST_OUT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _seed_final_mlb_market_history_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    start = now - timedelta(days=1)

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()

        home = Team(league_id=league.id, name="Home Bats", normalized_name="home bats")
        away = Team(league_id=league.id, name="Away Arms", normalized_name="away arms")
        session.add_all([home, away])
        session.flush()

        event = Event(
            league_id=league.id,
            external_event_key="mlb-final-1",
            start_time_utc=start,
            home_team_id=home.id,
            away_team_id=away.id,
            status="final",
        )
        session.add(event)
        session.flush()
        session.add(
            EventResult(
                event_id=event.id,
                home_score=6,
                away_score=2,
                status="final",
                completed_at_utc=start + timedelta(hours=3),
            )
        )

        starter = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="starter-1",
            full_name="Home Starter",
            primary_position="P",
        )
        session.add(starter)
        session.flush()

        session.add(
            MlbTeamGameLog(
                event_id=event.id,
                team_id=home.id,
                game_date_utc=start,
                is_home=True,
                opponent_team_id=away.id,
                runs_for=6,
                runs_against=2,
                source="mlb_stats_api",
            )
        )
        session.add(
            MlbPlayerGameLog(
                event_id=event.id,
                player_id=starter.id,
                team_id=home.id,
                game_date_utc=start,
                is_home=True,
                pitching_started=True,
                pitching_strike_outs=7,
                source="mlb_stats_api",
            )
        )

        timestamps = {
            "open": start - timedelta(hours=2),
            "t60": start - timedelta(minutes=70),
            "t30": start - timedelta(minutes=35),
            "close": start - timedelta(minutes=10),
        }

        def add_market_snapshots(
            *,
            market_key: str,
            participant_name: str,
            team_id: int | None,
            player_id: int | None,
            open_line: float,
            t60_line: float,
            t30_line: float,
            close_line: float,
        ) -> None:
            for stamp_key, line, over_price, under_price in [
                ("open", open_line, -115, -105),
                ("t60", t60_line, -120, 100),
                ("t30", t30_line, -125, 105),
                ("close", close_line, -130, 110),
            ]:
                collected = timestamps[stamp_key]
                session.add_all(
                    [
                        EventOddsQuote(
                            event_id=event.id,
                            book="draftkings",
                            market_key=market_key,
                            provider_market_key=market_key,
                            entity_type="team" if team_id is not None else "player",
                            team_id=team_id,
                            player_id=player_id,
                            participant_name=participant_name,
                            side="over",
                            line=line,
                            price_american=over_price,
                            implied_probability=None,
                            provider_updated_at_utc=collected,
                            collected_at_utc=collected,
                            source="the_odds_api_event_odds",
                        ),
                        EventOddsQuote(
                            event_id=event.id,
                            book="draftkings",
                            market_key=market_key,
                            provider_market_key=market_key,
                            entity_type="team" if team_id is not None else "player",
                            team_id=team_id,
                            player_id=player_id,
                            participant_name=participant_name,
                            side="under",
                            line=line,
                            price_american=under_price,
                            implied_probability=None,
                            provider_updated_at_utc=collected,
                            collected_at_utc=collected,
                            source="the_odds_api_event_odds",
                        ),
                    ]
                )

        add_market_snapshots(
            market_key="team_totals",
            participant_name=home.name,
            team_id=home.id,
            player_id=None,
            open_line=4.5,
            t60_line=5.0,
            t30_line=5.5,
            close_line=5.5,
        )
        add_market_snapshots(
            market_key="pitcher_strikeouts",
            participant_name=starter.full_name,
            team_id=None,
            player_id=starter.id,
            open_line=5.5,
            t60_line=6.0,
            t30_line=6.0,
            close_line=6.5,
        )
        session.commit()

    return Session


def test_build_mlb_market_history_frame_exports_team_totals_and_props():
    Session = _seed_final_mlb_market_history_session()

    with Session() as session:
        frame, summary = build_mlb_market_history_frame(session)

    assert len(frame) == 2
    assert summary["rows_exported"] == 2
    assert summary["events_exported"] == 1
    assert summary["rows_by_market"]["team_totals"] == 1
    assert summary["rows_by_market"]["pitcher_strikeouts"] == 1

    team_row = frame.loc[frame["market_key"] == "team_totals"].iloc[0]
    assert team_row["participant_name"] == "Home Bats"
    assert team_row["actual_value"] == pytest.approx(6.0)
    assert team_row["settled_result"] == "O"
    assert team_row["margin_vs_line_CLOSE"] == pytest.approx(0.5)
    assert team_row["line_OPEN"] == pytest.approx(4.5)
    assert team_row["line_T60"] == pytest.approx(5.0)
    assert team_row["line_T30"] == pytest.approx(5.5)
    assert team_row["line_CLOSE"] == pytest.approx(5.5)
    assert team_row["best_entry_anchor"] == "T30"
    assert team_row["line_best_entry"] == pytest.approx(5.5)

    prop_row = frame.loc[frame["market_key"] == "pitcher_strikeouts"].iloc[0]
    assert prop_row["participant_name"] == "Home Starter"
    assert prop_row["actual_value"] == pytest.approx(7.0)
    assert prop_row["settled_result"] == "O"
    assert prop_row["line_CLOSE"] == pytest.approx(6.5)
    assert prop_row["over_price_american_CLOSE"] == -130


def test_generate_mlb_market_history_artifact_writes_bundle_and_filters_markets():
    Session = _seed_final_mlb_market_history_session()
    case_dir = _case_dir("artifact_bundle")

    with Session() as session:
        result = generate_mlb_market_history_artifact(
            session=session,
            market_keys=["team_totals"],
            out_dir=case_dir,
        )

    assert result.parquet_path.exists()
    assert result.manifest_path.exists()
    assert result.summary_path.exists()
    assert result.latest_path.exists()
    assert result.summary["rows_exported"] == 1
    assert result.summary["rows_by_market"]["team_totals"] == 1

    exported = pd.read_parquet(result.parquet_path)
    assert exported["market_key"].tolist() == ["team_totals"]
    assert exported.loc[0, "participant_team_name"] == "Home Bats"
    assert exported.loc[0, "opponent_team_name"] == "Away Arms"
