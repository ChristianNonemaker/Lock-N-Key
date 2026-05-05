"""Line thesis composition for focused sportsbook research views."""

from __future__ import annotations

from api.schemas import (
    LineEvidenceStatusRow,
    LineThesisRow,
    MarketContextRow,
    PlayerPropInsightRow,
    TeamLineEvidenceRow,
    WhyThisLineFactor,
)
from api.services.line_evidence import focus_key_for_line


_EVENT_MARKET_LABELS = {
    "moneyline": "Moneyline",
    "spread": "Run Line",
    "total": "Game Total",
    "team_totals": "Team Total",
    "pitcher_strikeouts": "Pitcher Strikeouts",
    "batter_hits": "Batter Hits",
    "batter_total_bases": "Batter Total Bases",
}


def _price(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:+d}"


def _line_value(*, market: str, side: str, line: float | None, price: int | None) -> str:
    if market == "moneyline":
        return _price(price)
    if line is None:
        return _price(price)
    if market in {"total", "team_totals", "pitcher_strikeouts", "batter_hits", "batter_total_bases"}:
        prefix = "O" if side == "over" else "U"
        return f"{prefix} {line:g} {_price(price)}"
    prefix = "+" if market == "spread" and line > 0 else ""
    return f"{prefix}{line:g} {_price(price)}"


def _move_value(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+g}"


def _score_label(score: int) -> str:
    if score >= 75:
        return "strong"
    if score >= 50:
        return "usable"
    if score >= 30:
        return "thin"
    return "weak"


def _evidence_action(evidence_tier: str) -> str:
    if evidence_tier == "validated_sample":
        return "validated sample"
    if evidence_tier == "thin_validated":
        return "thin validated"
    return "research only"


def _market_label(market: str) -> str:
    return _EVENT_MARKET_LABELS.get(market, market.replace("_", " ").title())


def _factor_focus_for_market(market: str) -> set[str]:
    if market == "total":
        return {"total", "game"}
    if market in {"moneyline", "spread"}:
        return {"side", "game"}
    return {"total", "side", "game"}


def _factor_points(factors: list[WhyThisLineFactor], market: str, limit: int = 2) -> list[str]:
    allowed = _factor_focus_for_market(market)
    points: list[str] = []
    for factor in factors:
        if factor.market_focus not in allowed:
            continue
        headline = factor.headline.strip()
        if headline and headline not in points:
            points.append(headline)
        if len(points) >= limit:
            break
    return points


def _evidence_by_key(rows: list[LineEvidenceStatusRow]) -> dict[str, LineEvidenceStatusRow]:
    return {row.focus_key: row for row in rows}


def _quality_scores(
    *,
    current_line: float | None,
    current_price: int | None,
    number_move: float | None,
    price_move: int | None,
    posted_sample: int,
    evidence: LineEvidenceStatusRow | None,
) -> tuple[int, int]:
    has_current = current_line is not None or current_price is not None
    line_score = 0
    if has_current:
        line_score += 35
    if evidence and evidence.line_lifecycle_status == "current":
        line_score += 20
    elif evidence and evidence.line_lifecycle_status in {"stale", "live"}:
        line_score += 10
    if posted_sample >= 5:
        line_score += 20
    elif posted_sample >= 2:
        line_score += 12
    elif posted_sample >= 1:
        line_score += 6
    if number_move is not None or price_move is not None:
        line_score += 15
    if current_price is not None:
        line_score += 10
    line_score = min(line_score, 100)

    if not evidence:
        return line_score, 0
    tier_base = {
        "validated_sample": 60,
        "thin_validated": 35,
        "research_only": 10,
    }.get(evidence.evidence_tier, 10)
    evidence_score = tier_base
    evidence_score += min(evidence.oof_predicted_rows, 75) // 5
    evidence_score += min(evidence.settled_sample_size, 100) // 10
    evidence_score += min(evidence.posted_line_sample_size, 10)
    evidence_score -= min(len(evidence.gaps) * 6, 24)
    return line_score, max(0, min(int(evidence_score), 100))


def _history_summary(
    *,
    record_vs_current: str | None,
    record_vs_market: str | None,
    recent_record: str | None,
    posted_sample: int,
) -> str:
    if record_vs_current and record_vs_market:
        return f"{record_vs_current} vs today's line; {record_vs_market} vs posted lines"
    if record_vs_current:
        return f"{record_vs_current} vs today's line"
    if record_vs_market:
        return f"{record_vs_market} vs posted lines"
    if recent_record:
        return f"{recent_record} at recent market prices"
    if posted_sample:
        return f"{posted_sample} posted-line samples available"
    return "No line-backed recent history yet"


def _movement_summary(
    *,
    number_move: float | None,
    price_move: int | None,
    best_entry_anchor: str | None,
) -> str:
    parts: list[str] = []
    if number_move is not None:
        parts.append(f"number {_move_value(number_move)}")
    if price_move is not None:
        parts.append(f"price {price_move:+d}")
    if best_entry_anchor:
        parts.append(f"best entry {best_entry_anchor}")
    return ", ".join(parts) if parts else "No stored movement from open"


def _row_thesis(
    *,
    focus_key: str,
    market: str,
    side: str,
    participant_name: str | None,
    selection: str,
    current_line: float | None,
    current_price: int | None,
    number_move: float | None,
    price_move: int | None,
    best_entry_anchor: str | None,
    record_vs_current: str | None,
    record_vs_market: str | None,
    recent_record: str | None,
    posted_sample: int,
    signal_notes: list[str],
    evidence: LineEvidenceStatusRow | None,
    factors: list[WhyThisLineFactor],
) -> LineThesisRow:
    line_score, evidence_score = _quality_scores(
        current_line=current_line,
        current_price=current_price,
        number_move=number_move,
        price_move=price_move,
        posted_sample=posted_sample,
        evidence=evidence,
    )
    evidence_tier = evidence.evidence_tier if evidence else "research_only"
    action_status = _evidence_action(evidence_tier)
    current_summary = _line_value(
        market=market,
        side=side,
        line=current_line,
        price=current_price,
    )
    movement_summary = _movement_summary(
        number_move=number_move,
        price_move=price_move,
        best_entry_anchor=best_entry_anchor,
    )
    history_summary = _history_summary(
        record_vs_current=record_vs_current,
        record_vs_market=record_vs_market,
        recent_record=recent_record,
        posted_sample=posted_sample,
    )
    evidence_summary = (
        "Evidence "
        f"{_score_label(evidence_score)}: {evidence.oof_predicted_rows} OOF rows, "
        f"{evidence.settled_sample_size} settled rows"
        if evidence
        else "Evidence weak: line evidence status pending"
    )
    gaps = evidence.gaps if evidence else []
    risk_bits: list[str] = []
    if evidence_tier != "validated_sample":
        risk_bits.append(action_status)
    if posted_sample < 3:
        risk_bits.append("thin posted-line history")
    if gaps:
        risk_bits.append(", ".join(gaps[:3]))
    risk_summary = "; ".join(risk_bits) if risk_bits else "No major local evidence gaps"

    support_points = []
    if current_line is not None or current_price is not None:
        support_points.append("Current DraftKings line is stored locally")
    if record_vs_current:
        support_points.append(f"Recent current-line record: {record_vs_current}")
    if record_vs_market:
        support_points.append(f"Recent posted-line record: {record_vs_market}")
    support_points.extend(_factor_points(factors, market))
    support_points = list(dict.fromkeys(support_points))[:4]

    caution_points = []
    if evidence_tier != "validated_sample":
        caution_points.append("Treat as sample-sensitive until settled market history grows")
    if posted_sample < 3:
        caution_points.append("Posted-line sample is still thin")
    if signal_notes:
        caution_points.extend(signal_notes[:2])
    if gaps:
        caution_points.extend(gaps[:2])
    caution_points = list(dict.fromkeys(caution_points))[:4]

    next_step = None
    if not evidence or evidence_tier == "research_only":
        next_step = "Collect current lines and rerun strict OOF evidence."
    elif evidence_tier == "thin_validated":
        next_step = "Grow settled priced sample before promotion."
    elif posted_sample < 5:
        next_step = "Keep collecting posted-line history for this market."

    headline = (
        f"{selection}: {action_status.title()} readout for "
        f"{_market_label(market).lower()} at {current_summary}"
    )
    return LineThesisRow(
        focus_key=focus_key,
        market=market,
        side=side,
        participant_name=participant_name,
        headline=headline,
        action_status=action_status,
        line_quality_score=line_score,
        evidence_quality_score=evidence_score,
        current_summary=current_summary,
        movement_summary=movement_summary,
        history_summary=history_summary,
        evidence_summary=evidence_summary,
        risk_summary=risk_summary,
        support_points=support_points,
        caution_points=caution_points,
        next_step=next_step,
    )


def build_line_thesis_rows(
    *,
    market_context: list[MarketContextRow],
    team_line_evidence: list[TeamLineEvidenceRow],
    player_prop_insights: list[PlayerPropInsightRow],
    line_evidence_status: list[LineEvidenceStatusRow],
    why_this_line: list[WhyThisLineFactor],
) -> list[LineThesisRow]:
    """Build one local-only thesis row for every focusable market line."""
    evidence_by_focus_key = _evidence_by_key(line_evidence_status)
    rows: list[LineThesisRow] = []

    for row in market_context:
        focus_key = focus_key_for_line(
            market=row.market,
            side=row.side,
            participant_name=row.selection,
        )
        rows.append(
            _row_thesis(
                focus_key=focus_key,
                market=row.market,
                side=row.side,
                participant_name=row.selection,
                selection=row.selection,
                current_line=row.current_line,
                current_price=row.current_price_american,
                number_move=row.number_move_from_open,
                price_move=row.price_move_american_from_open,
                best_entry_anchor=row.best_entry_anchor,
                record_vs_current=row.record_vs_current_line_last_n,
                record_vs_market=row.record_vs_market_line_last_n,
                recent_record=row.recent_record_last_n,
                posted_sample=(
                    len(row.recent_results_vs_market_prices)
                    if row.market == "moneyline"
                    else len(row.recent_results_vs_market_lines)
                ),
                signal_notes=row.signal_notes,
                evidence=evidence_by_focus_key.get(focus_key),
                factors=why_this_line,
            )
        )

    for row in team_line_evidence:
        for side, price, open_move in (
            ("over", row.current_over_price_american, row.over_price_move_american_from_open),
            ("under", row.current_under_price_american, row.under_price_move_american_from_open),
        ):
            focus_key = focus_key_for_line(
                market="team_totals",
                side=side,
                participant_name=row.team_name,
            )
            selection = f"{row.team_name} team total {side}"
            rows.append(
                _row_thesis(
                    focus_key=focus_key,
                    market="team_totals",
                    side=side,
                    participant_name=row.team_name,
                    selection=selection,
                    current_line=row.current_team_total,
                    current_price=price,
                    number_move=row.number_move_from_open,
                    price_move=open_move,
                    best_entry_anchor=row.best_entry_anchor,
                    record_vs_current=row.record_vs_current_line_last_n,
                    record_vs_market=row.record_vs_market_line_last_n,
                    recent_record=None,
                    posted_sample=row.posted_line_games_sampled,
                    signal_notes=[],
                    evidence=evidence_by_focus_key.get(focus_key),
                    factors=why_this_line,
                )
            )

    for row in player_prop_insights:
        for side, price, open_move in (
            ("over", row.over_price_american, row.over_price_move_american_from_open),
            ("under", row.under_price_american, row.under_price_move_american_from_open),
        ):
            focus_key = focus_key_for_line(
                market=row.market_key,
                side=side,
                participant_name=row.player_name,
            )
            selection = f"{row.player_name} {row.market_label} {side}"
            rows.append(
                _row_thesis(
                    focus_key=focus_key,
                    market=row.market_key,
                    side=side,
                    participant_name=row.player_name,
                    selection=selection,
                    current_line=row.current_line,
                    current_price=price,
                    number_move=row.number_move_from_open,
                    price_move=open_move,
                    best_entry_anchor=row.best_entry_anchor,
                    record_vs_current=row.record_vs_current_line_last_n,
                    record_vs_market=row.record_vs_market_line_last_n,
                    recent_record=None,
                    posted_sample=row.posted_line_games_sampled,
                    signal_notes=[note for note in (row.context_note, row.note) if note],
                    evidence=evidence_by_focus_key.get(focus_key),
                    factors=why_this_line,
                )
            )

    return rows
