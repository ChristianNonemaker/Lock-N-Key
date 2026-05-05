from __future__ import annotations

from datetime import date

from dk_ncaab.collectors.mlb_daily_workflow import build_mlb_daily_research_steps


def test_mlb_daily_research_workflow_keeps_event_odds_explicit():
    steps = build_mlb_daily_research_steps(slate_date=date(2026, 5, 2))

    names = [step.name for step in steps]
    assert names[:5] == [
        "Load MLB slate",
        "Settle recent games",
        "Backfill MLB logs",
        "Backfill Statcast daily",
        "Collect MLB environment",
    ]
    event_step = next(step for step in steps if step.name == "Event-specific odds skipped")
    assert event_step.quota == "odds_api_skipped"
    assert "--markets team_totals,pitcher_strikeouts,batter_total_bases" in event_step.command


def test_mlb_daily_research_workflow_can_include_bounded_event_odds():
    steps = build_mlb_daily_research_steps(
        slate_date=date(2026, 5, 2),
        event_odds_max_events=3,
        include_event_odds=True,
    )

    event_step = next(step for step in steps if step.name == "Collect event-specific odds")
    assert event_step.quota == "odds_api"
    assert "--max-events 3" in event_step.command
