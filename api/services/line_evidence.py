"""Line-level evidence status composition for research payloads."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict

from sqlalchemy.orm import Session

from api.schemas import (
    BoardGame,
    LineEvidenceStatusRow,
    MarketContextRow,
    PlayerPropInsightRow,
    TeamLineEvidenceRow,
)

log = logging.getLogger(__name__)

MIN_OOF_ROWS = 100
MIN_SETTLED_EVENTS = 30
MIN_POSTED_LINE_SAMPLES = 10


def focus_key_for_line(
    *,
    market: str,
    side: str,
    participant_name: str | None = None,
) -> str:
    """Return a stable UI identity for core and participant-specific lines."""
    participant = _focus_key_part(participant_name)
    return f"{market}:{side}:{participant}"


def _focus_key_part(value: str | None) -> str:
    if not value:
        return "market"
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "market"


def line_lifecycle_status(
    *,
    has_current: bool,
    is_live: bool = False,
    is_stale: bool = False,
    best_entry_anchor: str | None = None,
) -> str:
    if not has_current:
        return "no_current_line"
    if is_live:
        return "live"
    if is_stale:
        return "stale"
    if best_entry_anchor is None:
        return "anchor_missing"
    return "current"


def line_evidence_tier(
    *,
    has_current: bool,
    oof_rows: int,
    settled_events: int,
    posted_line_sample_size: int,
    gaps: list[str],
) -> str:
    if not has_current or oof_rows <= 0:
        return "research_only"
    blocking_gaps = {"no_current_lines", "missing_stat_context", "participant_linkage_gap"}
    if (
        oof_rows >= MIN_OOF_ROWS
        and settled_events >= MIN_SETTLED_EVENTS
        and posted_line_sample_size >= MIN_POSTED_LINE_SAMPLES
        and not blocking_gaps.intersection(gaps)
    ):
        return "validated_sample"
    return "thin_validated"


def promotion_status(
    *,
    has_current: bool,
    oof_rows: int,
    settled_events: int,
    posted_line_sample_size: int,
    gaps: list[str],
) -> tuple[str, list[str]]:
    promotion_gaps: list[str] = []
    if not has_current:
        promotion_gaps.append("no_current_line")
    if oof_rows < MIN_OOF_ROWS:
        promotion_gaps.append("oof_sample_below_gate")
    if settled_events < MIN_SETTLED_EVENTS:
        promotion_gaps.append("settled_events_below_gate")
    if posted_line_sample_size < MIN_POSTED_LINE_SAMPLES:
        promotion_gaps.append("posted_line_sample_below_gate")
    blocking_gaps = sorted(
        set(gaps).intersection({"no_current_lines", "missing_stat_context", "participant_linkage_gap"})
    )
    promotion_gaps.extend(blocking_gaps)
    if promotion_gaps:
        if oof_rows > 0 and has_current:
            return "sample_sensitive", sorted(set(promotion_gaps))
        return "research_only", sorted(set(promotion_gaps))
    return "promotable", []


def mlb_market_readiness_by_market(
    session: Session,
    *,
    sport: str,
    league_key: str,
) -> dict[str, dict[str, object]]:
    if sport != "baseball_mlb":
        return {}
    try:
        from dk_ncaab.analysis.mlb_market_readiness import build_mlb_market_readiness

        result = build_mlb_market_readiness(session, sport=sport, league_key=league_key)
        return {row.market: asdict(row) for row in result.markets}
    except Exception:
        log.exception("Failed to build MLB market readiness for line evidence status")
        return {}


def build_line_evidence_status_rows(
    session: Session,
    *,
    board_game: BoardGame,
    market_context: list[MarketContextRow],
    team_line_evidence: list[TeamLineEvidenceRow],
    player_prop_insights: list[PlayerPropInsightRow],
) -> list[LineEvidenceStatusRow]:
    readiness_by_market = mlb_market_readiness_by_market(
        session,
        sport=board_game.sport,
        league_key=board_game.league_key,
    )

    def readiness(market: str) -> dict[str, object]:
        return readiness_by_market.get(market, {})

    def row_for(
        *,
        market: str,
        side: str,
        participant_name: str | None,
        current_line: float | None,
        current_price_american: int | None,
        posted_line_sample_size: int,
        is_live: bool = False,
        is_stale: bool = False,
        best_entry_anchor: str | None = None,
        extra_gaps: list[str] | None = None,
    ) -> LineEvidenceStatusRow:
        market_ready = readiness(market)
        readiness_gaps = [str(gap) for gap in (market_ready.get("gaps") or [])]
        gaps = sorted(set(readiness_gaps + list(extra_gaps or [])))
        has_current = current_line is not None or current_price_american is not None
        settled_rows = int(market_ready.get("settled_quoted_rows") or 0)
        settled_events = int(market_ready.get("settled_quoted_events") or 0)
        oof_rows = int(market_ready.get("oof_predicted_rows") or 0)
        status, promotion_gaps = promotion_status(
            has_current=has_current,
            oof_rows=oof_rows,
            settled_events=settled_events,
            posted_line_sample_size=posted_line_sample_size,
            gaps=gaps,
        )
        return LineEvidenceStatusRow(
            focus_key=focus_key_for_line(
                market=market,
                side=side,
                participant_name=participant_name,
            ),
            market=market,
            side=side,
            participant_name=participant_name,
            current_line=current_line,
            current_price_american=current_price_american,
            line_lifecycle_status=line_lifecycle_status(
                has_current=has_current,
                is_live=is_live,
                is_stale=is_stale,
                best_entry_anchor=best_entry_anchor,
            ),
            market_readiness_verdict=market_ready.get("verdict"),
            settled_sample_size=settled_rows,
            posted_line_sample_size=posted_line_sample_size,
            oof_predicted_rows=oof_rows,
            oof_recommended_rows=int(market_ready.get("oof_recommended_rows") or 0),
            evidence_tier=line_evidence_tier(
                has_current=has_current,
                oof_rows=oof_rows,
                settled_events=settled_events,
                posted_line_sample_size=posted_line_sample_size,
                gaps=gaps,
            ),
            promotion_status=status,
            promotion_gaps=promotion_gaps,
            min_oof_rows=MIN_OOF_ROWS,
            min_settled_events=MIN_SETTLED_EVENTS,
            min_posted_line_samples=MIN_POSTED_LINE_SAMPLES,
            gaps=gaps,
        )

    rows: list[LineEvidenceStatusRow] = []
    for market_row in market_context:
        posted_samples = len(market_row.recent_results_vs_market_lines)
        if market_row.market == "moneyline":
            posted_samples = len(market_row.recent_results_vs_market_prices)
        rows.append(
            row_for(
                market=market_row.market,
                side=market_row.side,
                participant_name=market_row.selection,
                current_line=market_row.current_line,
                current_price_american=market_row.current_price_american,
                posted_line_sample_size=posted_samples,
                is_live=market_row.is_live,
                is_stale=market_row.is_stale,
                best_entry_anchor=market_row.best_entry_anchor,
                extra_gaps=market_row.signal_notes,
            )
        )

    for team_row in team_line_evidence:
        for side, price in (
            ("over", team_row.current_over_price_american),
            ("under", team_row.current_under_price_american),
        ):
            rows.append(
                row_for(
                    market="team_totals",
                    side=side,
                    participant_name=team_row.team_name,
                    current_line=team_row.current_team_total,
                    current_price_american=price,
                    posted_line_sample_size=team_row.posted_line_games_sampled,
                    best_entry_anchor=team_row.best_entry_anchor,
                    extra_gaps=[] if team_row.current_team_total is not None else ["no_current_lines"],
                )
            )

    for prop_row in player_prop_insights:
        for side, price in (
            ("over", prop_row.over_price_american),
            ("under", prop_row.under_price_american),
        ):
            rows.append(
                row_for(
                    market=prop_row.market_key,
                    side=side,
                    participant_name=prop_row.player_name,
                    current_line=prop_row.current_line,
                    current_price_american=price,
                    posted_line_sample_size=prop_row.posted_line_games_sampled,
                    best_entry_anchor=prop_row.best_entry_anchor,
                    extra_gaps=[] if prop_row.current_line is not None else ["no_current_lines"],
                )
            )
    return rows
