from __future__ import annotations

import shutil
from types import SimpleNamespace
from uuid import uuid4
from pathlib import Path

from dk_ncaab.analysis.mlb_evidence_growth import build_mlb_evidence_growth_log


def _readiness(*, team_total_current: int, team_total_oof: int) -> SimpleNamespace:
    rows = [
        SimpleNamespace(
            market="moneyline",
            label="Moneyline",
            verdict="ready",
            current_quoted_rows=2,
            current_quoted_events=1,
            settled_quoted_rows=80,
            settled_quoted_events=40,
            oof_predicted_rows=82,
            oof_recommended_rows=29,
            participant_link_rate=None,
            priority_score=10,
            next_action="ready_for_review",
            next_action_label="Ready for review",
            next_action_command="python -m dk_ncaab oof-entry-ev --sport baseball_mlb --anchor T60",
            next_action_reason="Ready.",
            gaps=[],
        ),
        SimpleNamespace(
            market="team_totals",
            label="Team Totals",
            verdict="thin",
            current_quoted_rows=team_total_current,
            current_quoted_events=1,
            settled_quoted_rows=4,
            settled_quoted_events=1,
            oof_predicted_rows=team_total_oof,
            oof_recommended_rows=1,
            participant_link_rate=1.0,
            priority_score=55,
            next_action="grow_settled_event_market_sample",
            next_action_label="Grow settled prop sample",
            next_action_command=(
                "python -m dk_ncaab collect-event-odds --sport baseball_mlb "
                "--markets team_totals --max-events 3"
            ),
            next_action_reason="Thin sample.",
            gaps=["thin_oof_sample"],
        ),
    ]
    return SimpleNamespace(
        markets=rows,
        summary=SimpleNamespace(
            markets_ready=1,
            markets_thin=1,
            markets_collect_more=0,
            markets_missing_data=0,
            total_oof_predicted_rows=sum(row.oof_predicted_rows for row in rows),
        ),
        warnings=[],
    )


def _inventory() -> SimpleNamespace:
    return SimpleNamespace(
        summary={
            "events": {"total": 20, "final": 10},
            "line_history": {
                "odds_quotes": 100,
                "draftkings_pregame_events": 15,
                "settled_draftkings_pregame_events": 10,
                "event_specific_quotes": 12,
                "event_specific_pregame_events": 2,
                "event_specific_quotes_by_market": {"team_totals": 12},
                "unlinked_event_specific_player_quotes": 2,
                "unlinked_event_specific_team_quotes": 0,
            },
            "mlb_stats": {"team_logs": 20, "player_logs": 200},
            "statcast": {"daily_rows": 300},
            "environment": {"park_factors": 0},
        }
    )


def test_build_mlb_evidence_growth_log_appends_and_computes_deltas(monkeypatch):
    out_dir = Path("artifacts/test/evidence_growth") / uuid4().hex
    monkeypatch.setattr(
        "dk_ncaab.analysis.mlb_evidence_growth.build_mlb_market_readiness",
        lambda session: _readiness(team_total_current=4, team_total_oof=4),
    )
    monkeypatch.setattr(
        "dk_ncaab.analysis.mlb_evidence_growth.build_mlb_data_inventory",
        lambda *, session, out_dir: _inventory(),
    )

    first = build_mlb_evidence_growth_log(
        session=object(),
        out_dir=out_dir,
        label="baseline",
    )
    team_totals = next(row for row in first["markets"] if row["market"] == "team_totals")
    assert team_totals["current_quoted_rows_delta"] == 0
    assert first["summary"]["top_next_action"] == "grow_settled_event_market_sample"
    assert first["summary"]["unlinked_event_specific_player_quotes"] == 2
    assert any("player quotes are not linked" in warning for warning in first["warnings"])

    monkeypatch.setattr(
        "dk_ncaab.analysis.mlb_evidence_growth.build_mlb_market_readiness",
        lambda session: _readiness(team_total_current=8, team_total_oof=6),
    )
    second = build_mlb_evidence_growth_log(
        session=object(),
        out_dir=out_dir,
        label="after-event-odds",
    )
    team_totals = next(row for row in second["markets"] if row["market"] == "team_totals")
    assert team_totals["current_quoted_rows_delta"] == 4
    assert team_totals["oof_predicted_rows_delta"] == 2
    assert second["previous_generated_at_utc"] == first["generated_at_utc"]
    assert (out_dir / "mlb_evidence_growth.jsonl").exists()
    shutil.rmtree(out_dir, ignore_errors=True)
