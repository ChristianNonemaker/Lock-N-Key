"""Local game-level intelligence for the compact sportsbook board."""

from __future__ import annotations

from datetime import datetime, timezone

from api.schemas import (
    BoardLineOption,
    BoardSplitSummary,
    SlateIntelligenceSignal,
    SlateIntelligenceSummary,
)


def build_slate_intelligence(
    *,
    start_time_utc: datetime,
    status: str,
    odds_age_min: int | None,
    odds_stale: bool,
    lines: list[BoardLineOption],
    split_summary: list[BoardSplitSummary],
    flags: list[str],
    oof_rows_by_market: dict[str, int] | None = None,
    now: datetime | None = None,
) -> SlateIntelligenceSummary:
    now = _ensure_utc(now or datetime.now(timezone.utc))
    start = _ensure_utc(start_time_utc)
    oof_rows_by_market = oof_rows_by_market or {}
    reasons: list[str] = []
    gaps: list[str] = []
    signals: list[SlateIntelligenceSignal] = []
    score = 0

    if lines:
        score += 20
        reasons.append("current DK lines")
    else:
        gaps.append("current DK lines missing")

    freshness_value, freshness_detail, freshness_score, freshness_gap = _freshness_signal(
        odds_age_min=odds_age_min,
        odds_stale=odds_stale,
    )
    score += freshness_score
    if freshness_score >= 16:
        reasons.append("fresh odds")
    if freshness_gap:
        gaps.append(freshness_gap)
    signals.append(SlateIntelligenceSignal(label="Freshness", value=freshness_value, detail=freshness_detail))

    best_move = _best_line_move(lines)
    move_score = 0
    if best_move:
        number_move = _number_move(best_move)
        price_move = _price_move(best_move)
        number_abs = abs(number_move or 0.0)
        price_abs = abs(price_move or 0.0)
        if number_abs >= 1.0:
            move_score = max(move_score, 22)
        elif number_abs >= 0.5:
            move_score = max(move_score, 12)
        if price_abs >= 20:
            move_score = max(move_score, 18)
        elif price_abs >= 10:
            move_score = max(move_score, 10)
        if move_score:
            reasons.append("market moved")
        score += move_score
        signals.append(
            SlateIntelligenceSignal(
                label="Strongest Move",
                value=best_move.label,
                detail=_move_detail(number_move=number_move, price_move=price_move),
            )
        )
    else:
        signals.append(
            SlateIntelligenceSignal(
                label="Strongest Move",
                value="No move",
                detail="Open/current movement not stored yet",
            )
        )

    best_split = _largest_split_gap(split_summary)
    split_gap = None
    if best_split:
        split_gap = _split_gap(best_split)
        if split_gap >= 0.10:
            score += 18
            reasons.append("split divergence")
        elif split_gap >= 0.05:
            score += 9
            reasons.append("split lean")
        signals.append(
            SlateIntelligenceSignal(
                label="Split Pressure",
                value=f"{split_gap:.0%} gap",
                detail=f"{best_split.market} {best_split.side}",
            )
        )
    elif "No public splits" in flags:
        gaps.append("public splits missing")
        signals.append(
            SlateIntelligenceSignal(
                label="Split Pressure",
                value="No splits",
                detail="Public split feed missing",
            )
        )
    else:
        signals.append(
            SlateIntelligenceSignal(
                label="Split Pressure",
                value="Quiet",
                detail="No notable split divergence",
            )
        )

    game_markets = {line.market for line in lines}
    oof_overlap = sorted(market for market in game_markets if int(oof_rows_by_market.get(market) or 0) > 0)
    if oof_overlap:
        score += 12
        reasons.append("strict OOF market")
        evidence_label = "OOF: " + ", ".join(oof_overlap[:3])
    elif lines:
        evidence_label = "Research only"
        gaps.append("strict OOF evidence missing")
    else:
        evidence_label = "No current lines"
    signals.append(
        SlateIntelligenceSignal(
            label="Evidence",
            value=evidence_label,
            detail="Validated shelf exists" if oof_overlap else "Open research before acting",
        )
    )

    hours_to_start = (start - now).total_seconds() / 3600
    if status == "live":
        score += 8
        reasons.append("live")
    elif 0 <= hours_to_start <= 8:
        score += 10
        reasons.append("starts soon")
    elif 8 < hours_to_start <= 24:
        score += 5
        reasons.append("today/tomorrow")

    if any(line.best_entry_anchor is None for line in lines):
        gaps.append("best-entry anchor missing")
    for flag in flags:
        if flag == "No odds yet":
            gaps.append("current DK lines missing")
        elif flag == "Odds stale":
            gaps.append("odds stale")
        elif flag == "No final result" and status not in {"upcoming", "live"}:
            gaps.append("settlement missing")

    tier = _tier(score=score, gaps=gaps, has_lines=bool(lines))
    headline = _headline(tier)
    primary_action = "collect_data" if tier == "needs_data" else "open_research" if score >= 45 else "monitor"
    next_action_label = _next_action_label(gaps=gaps, score=score, has_oof=bool(oof_overlap))

    return SlateIntelligenceSummary(
        score=min(score, 100),
        tier=tier,
        headline=headline,
        primary_action=primary_action,
        next_action_label=next_action_label,
        reasons=_dedupe(reasons),
        gaps=_dedupe(gaps),
        strongest_move_label=best_move.label if best_move else None,
        strongest_number_move=_number_move(best_move) if best_move else None,
        strongest_price_move_american=_price_move(best_move) if best_move else None,
        split_pressure_label=f"{best_split.market} {best_split.side}" if best_split else None,
        split_gap=round(split_gap, 3) if split_gap is not None else None,
        evidence_label=evidence_label,
        signals=signals,
    )


def _freshness_signal(
    *,
    odds_age_min: int | None,
    odds_stale: bool,
) -> tuple[str, str, int, str | None]:
    if odds_age_min is None:
        return "No odds", "Current DK snapshot missing", 0, "current DK lines missing"
    if odds_stale:
        return f"{odds_age_min}m stale", "Refresh before trusting movement", 4, "odds stale"
    if odds_age_min <= 15:
        return f"{odds_age_min}m fresh", "Current DK snapshot available", 20, None
    return f"{odds_age_min}m fresh", "Usable current DK snapshot", 16, None


def _best_line_move(lines: list[BoardLineOption]) -> BoardLineOption | None:
    best: BoardLineOption | None = None
    best_score = -1.0
    for line in lines:
        score = max(abs(_number_move(line) or 0.0) * 10.0, abs(_price_move(line) or 0.0) / 10.0)
        if score > best_score:
            best = line
            best_score = score
    return best if best_score > 0 else None


def _largest_split_gap(splits: list[BoardSplitSummary]) -> BoardSplitSummary | None:
    best: BoardSplitSummary | None = None
    best_gap = -1.0
    for split in splits:
        gap = _split_gap(split)
        if gap > best_gap:
            best = split
            best_gap = gap
    return best if best_gap >= 0 else None


def _split_gap(split: BoardSplitSummary) -> float:
    bets = _pct_fraction(split.bets_pct)
    handle = _pct_fraction(split.handle_pct)
    if bets is None or handle is None:
        return 0.0
    return abs(handle - bets)


def _pct_fraction(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 100.0 if abs(value) > 1.0 else value


def _number_move(line: BoardLineOption | None) -> float | None:
    if not line:
        return None
    return line.number_move_from_open


def _price_move(line: BoardLineOption | None) -> float | None:
    if not line or line.price_move_american_from_open is None:
        return None
    return float(line.price_move_american_from_open)


def _move_detail(*, number_move: float | None, price_move: float | None) -> str:
    parts: list[str] = []
    if number_move is not None and abs(number_move) > 0:
        parts.append(f"number {number_move:+g}")
    if price_move is not None and abs(price_move) > 0:
        parts.append(f"price {price_move:+g}")
    return ", ".join(parts) or "No meaningful move"


def _tier(*, score: int, gaps: list[str], has_lines: bool) -> str:
    if not has_lines:
        return "needs_data"
    if score >= 70:
        return "high_interest"
    if score >= 45:
        return "review"
    if score >= 25:
        return "monitor"
    if gaps:
        return "needs_data"
    return "low_signal"


def _headline(tier: str) -> str:
    labels = {
        "high_interest": "Open first",
        "review": "Worth review",
        "monitor": "Monitor",
        "needs_data": "Needs data",
        "low_signal": "Low signal",
    }
    return labels.get(tier, "Monitor")


def _next_action_label(*, gaps: list[str], score: int, has_oof: bool) -> str:
    gap_text = " | ".join(gaps)
    if "current DK lines missing" in gap_text:
        return "Collect current odds"
    if "odds stale" in gap_text:
        return "Refresh odds"
    if "public splits missing" in gap_text:
        return "Collect splits"
    if "best-entry anchor missing" in gap_text:
        return "Build entry snapshots"
    if not has_oof:
        return "Grow strict OOF evidence"
    if score >= 45:
        return "Open line research"
    return "Monitor"


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
