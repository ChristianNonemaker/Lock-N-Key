from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.analysis.mlb_market_readiness import build_mlb_market_readiness
from dk_ncaab.db.models import (
    Base,
    Event,
    EventOddsQuote,
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


def test_build_mlb_market_readiness_reports_market_contracts(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 5, 2, 18, tzinfo=timezone.utc)
    final_start = now - timedelta(days=2)
    upcoming_start = now + timedelta(days=1)

    monkeypatch.setattr(
        "dk_ncaab.analysis.mlb_market_readiness.read_latest_entry_ev",
        lambda: {
            "sport": "baseball_mlb",
            "anchor": "T60",
            "generated_at_utc": now.isoformat(),
            "predictions_path": "artifacts/entry_ev/oof/test/predictions.parquet",
            "rows_predicted_by_market": {"moneyline": 24, "team_totals": 6},
            "recommended_by_market": {"moneyline": 4, "team_totals": 1},
        },
    )

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()
        home = Team(league_id=league.id, name="Home", normalized_name="home")
        away = Team(league_id=league.id, name="Away", normalized_name="away")
        session.add_all([home, away])
        session.flush()
        player = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="123",
            full_name="Hitter One",
        )
        session.add(player)
        session.flush()

        final_event = Event(
            league_id=league.id,
            external_event_key="final",
            start_time_utc=final_start,
            home_team_id=home.id,
            away_team_id=away.id,
            status="final",
        )
        upcoming_event = Event(
            league_id=league.id,
            external_event_key="upcoming",
            start_time_utc=upcoming_start,
            home_team_id=home.id,
            away_team_id=away.id,
            status="upcoming",
        )
        session.add_all([final_event, upcoming_event])
        session.flush()
        session.add(EventResult(event_id=final_event.id, home_score=5, away_score=4, status="final"))
        for event, start in ((final_event, final_start), (upcoming_event, upcoming_start)):
            session.add_all(
                [
                    OddsQuote(
                        event_id=event.id,
                        book="draftkings",
                        market="moneyline",
                        side="home",
                        price_american=-120,
                        implied_probability=0.545,
                        collected_at_utc=start - timedelta(hours=1),
                        source="test",
                    ),
                    EventOddsQuote(
                        event_id=event.id,
                        book="draftkings",
                        market_key="team_totals",
                        provider_market_key="team_totals",
                        entity_type="team",
                        team_id=home.id,
                        participant_name="Home",
                        side="over",
                        line=4.5,
                        price_american=-110,
                        implied_probability=0.524,
                        collected_at_utc=start - timedelta(hours=1),
                        source="test",
                    ),
                    EventOddsQuote(
                        event_id=event.id,
                        book="draftkings",
                        market_key="batter_hits",
                        provider_market_key="batter_hits",
                        entity_type="player",
                        player_id=player.id,
                        participant_name="Hitter One",
                        side="over",
                        line=0.5,
                        price_american=-130,
                        implied_probability=0.565,
                        collected_at_utc=start - timedelta(hours=1),
                        source="test",
                    ),
                ]
            )
        session.add_all(
            [
                MlbTeamGameLog(
                    event_id=final_event.id,
                    team_id=home.id,
                    opponent_team_id=away.id,
                    game_date_utc=final_start,
                    is_home=True,
                    runs_for=5,
                    runs_against=4,
                    source="test",
                ),
                MlbPlayerGameLog(
                    event_id=final_event.id,
                    player_id=player.id,
                    team_id=home.id,
                    game_date_utc=final_start,
                    is_home=True,
                    hits=1,
                    source="test",
                ),
                MlbStatcastDaily(
                    player_id=player.id,
                    key_mlbam="123",
                    game_date_utc=final_start,
                    player_type="batter",
                    source="test",
                    imported_at_utc=now,
                    hard_hit_rate=0.4,
                ),
                MlbPlayerIdCrosswalk(
                    player_id=player.id,
                    key_mlbam="123",
                    source="test",
                    source_row_key="123",
                    imported_at_utc=now,
                ),
            ]
        )
        session.commit()

        result = build_mlb_market_readiness(
            session,
            days_back=10,
            days_forward=3,
            now=now,
        )

    by_market = {row.market: row for row in result.markets}
    assert result.summary.markets_ready == 1
    assert by_market["moneyline"].verdict == "ready"
    assert by_market["moneyline"].current_quoted_rows == 1
    assert by_market["moneyline"].settled_quoted_rows == 1
    assert by_market["moneyline"].oof_predicted_rows == 24
    assert by_market["moneyline"].next_action == "ready_for_review"
    assert by_market["team_totals"].verdict == "thin"
    assert by_market["team_totals"].participant_link_rate == 1.0
    assert by_market["team_totals"].next_action == "grow_settled_event_market_sample"
    assert "collect-event-odds" in (by_market["team_totals"].next_action_command or "")
    assert by_market["batter_hits"].verdict == "collect_more"
    assert by_market["batter_hits"].stat_context_label == "batter Statcast days"
    assert by_market["batter_hits"].next_action == "rerun_oof_entry_ev"
    assert "oof-entry-ev" in (by_market["batter_hits"].next_action_command or "")
