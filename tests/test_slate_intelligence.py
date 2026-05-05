from __future__ import annotations

from datetime import datetime, timedelta, timezone

from api.schemas import BoardLineOption, BoardSplitSummary
from api.services.slate_intelligence import build_slate_intelligence


def test_slate_intelligence_scores_moved_fresh_split_game():
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    line = BoardLineOption(
        market="total",
        side="over",
        label="Over",
        line=8.5,
        price_american=-120,
        collected_at_utc=now - timedelta(minutes=5),
        is_stale=False,
        open_line=7.5,
        open_price_american=-110,
        best_entry_anchor="OPEN",
        best_entry_line=7.5,
        best_entry_price_american=-110,
        number_move_from_open=1.0,
        price_move_american_from_open=-10,
    )
    split = BoardSplitSummary(
        market="total",
        side="over",
        bets_pct=45.0,
        handle_pct=62.0,
        collected_at_utc=now - timedelta(minutes=5),
    )

    summary = build_slate_intelligence(
        start_time_utc=now + timedelta(hours=2),
        status="upcoming",
        odds_age_min=5,
        odds_stale=False,
        lines=[line],
        split_summary=[split],
        flags=[],
        oof_rows_by_market={"total": 120},
        now=now,
    )

    assert summary.tier == "high_interest"
    assert summary.headline == "Open first"
    assert summary.primary_action == "open_research"
    assert "market moved" in summary.reasons
    assert "split divergence" in summary.reasons
    assert summary.evidence_label == "OOF: total"
    assert summary.split_gap == 0.17


def test_slate_intelligence_needs_data_without_lines():
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)

    summary = build_slate_intelligence(
        start_time_utc=now + timedelta(hours=5),
        status="upcoming",
        odds_age_min=None,
        odds_stale=True,
        lines=[],
        split_summary=[],
        flags=["No odds yet", "No public splits"],
        now=now,
    )

    assert summary.tier == "needs_data"
    assert summary.primary_action == "collect_data"
    assert summary.next_action_label == "Collect current odds"
    assert "current DK lines missing" in summary.gaps
    assert summary.evidence_label == "No current lines"
