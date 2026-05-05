"""
Sportsbook-style research board.

This page borrows the sportsbook interaction shape: sport filters, game rows,
clickable line buttons, an expanded research panel, and a right-side slip.
It is a research UI only; it does not place wagers.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import streamlit as st

from dk_ncaab.config.sports import ui_sport_choices

_ET = ZoneInfo("America/New_York")
_LEDGER_FILE = Path("artifacts/state/research_ledger.jsonl")

_MODES = [
    ("live", "Live"),
    ("today", "Today"),
    ("upcoming", "Upcoming"),
]

_LENSES = [
    ("slate", "Slate"),
    ("queue", "Daily Betting Queue"),
]


def _query_value(name: str) -> str | None:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _query_date(name: str) -> date:
    raw = _query_value(name)
    if not raw:
        return date.today()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return date.today()


def _query_event_id() -> int | None:
    raw = _query_value("event_id")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _query_focus_market() -> str | None:
    raw = _query_value("focus_market")
    return raw.strip() if raw else None


def _query_focus_side() -> str | None:
    raw = _query_value("focus_side")
    return raw.strip() if raw else None


def _query_focus_key() -> str | None:
    raw = _query_value("focus_key")
    return raw.strip() if raw else None


def _query_lens() -> str | None:
    raw = _query_value("lens")
    return raw.strip() if raw else None


def _sync_board_query_params(
    sport: str,
    mode: str,
    selected_date: date,
    event_id: int | None = None,
    focus_market: str | None = None,
    focus_side: str | None = None,
    focus_key: str | None = None,
    lens: str = "slate",
) -> None:
    st.query_params["sport"] = sport
    st.query_params["mode"] = mode
    st.query_params["date"] = selected_date.isoformat()
    if lens != "slate":
        st.query_params["lens"] = lens
    elif "lens" in st.query_params:
        del st.query_params["lens"]
    if event_id:
        st.query_params["event_id"] = str(event_id)
    elif "event_id" in st.query_params:
        del st.query_params["event_id"]
    if focus_market and focus_side and event_id:
        st.query_params["focus_market"] = focus_market
        st.query_params["focus_side"] = focus_side
        if focus_key:
            st.query_params["focus_key"] = focus_key
        elif "focus_key" in st.query_params:
            del st.query_params["focus_key"]
    else:
        if "focus_market" in st.query_params:
            del st.query_params["focus_market"]
        if "focus_side" in st.query_params:
            del st.query_params["focus_side"]
        if "focus_key" in st.query_params:
            del st.query_params["focus_key"]


def _utc_to_et(value: str | None, include_date: bool = False) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        et = dt.astimezone(_ET)
        fmt = "%m/%d %#I:%M %p" if include_date else "%#I:%M %p"
        try:
            return et.strftime(fmt)
        except ValueError:
            fallback = et.strftime(fmt.replace("%#I", "%I"))
            return fallback.replace(" 0", " ")
    except Exception:
        return value[:16]


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _price(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:+d}"


def _line_value(line: dict) -> str:
    market = line.get("market")
    raw = line.get("line")
    price = _price(line.get("price_american"))
    if market == "moneyline":
        return price
    if raw is None:
        return price
    prefix = "+" if market == "spread" and raw > 0 else ""
    if market == "total":
        total_prefix = "O" if line.get("side") == "over" else "U"
        return f"{total_prefix} {raw:g} {price}"
    return f"{prefix}{raw:g} {price}"


def _line_button_cue(line: dict) -> str:
    parts: list[str] = []
    number_move = _to_float(line.get("number_move_from_open"))
    if number_move is None:
        number_move = _to_float(line.get("line_move_from_open"))
    price_move = _to_float(line.get("price_move_american_from_open"))
    if number_move is not None and abs(number_move) > 0:
        parts.append(f"num {number_move:+g}")
    if price_move is not None and abs(price_move) > 0:
        parts.append(f"px {price_move:+g}")
    if line.get("best_entry_anchor"):
        parts.append(f"best {line['best_entry_anchor']}")
    if line.get("is_stale"):
        parts.append("stale")
    return " | ".join(parts[:3])


def _move(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "-"
    if pct:
        return f"{value:+.1%}"
    return f"{value:+g}"


def _price_move(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:+d}"


def _line_source_label(value: str | None) -> str:
    mapping = {
        "draftkings_team_total_market": "DK Team Total",
        "derived_implied_team_total_from_spread_total": "Derived from DK spread + total",
        "the_odds_api_event_odds": "DK event market",
    }
    return mapping.get(value or "", value or "-")


def _format_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.0%}"


def _to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _recent_line_record_label(row: dict, line_label: str) -> str | None:
    record = row.get("record_vs_current_line_last_n")
    if not record:
        return None
    avg_margin = _to_float(row.get("avg_margin_vs_current_line_last_n"))
    if avg_margin is None:
        return f"L5 vs {line_label}: {record}"
    return f"L5 vs {line_label}: {record} | avg margin {avg_margin:+g}"


def _market_line_record_label(row: dict, line_label: str) -> str | None:
    record = row.get("record_vs_market_line_last_n")
    if not record:
        return None
    avg_margin = _to_float(row.get("avg_margin_vs_market_line_last_n"))
    if avg_margin is None:
        return f"L5 vs {line_label}: {record}"
    return f"L5 vs {line_label}: {record} | avg margin {avg_margin:+g}"


def _price_record_label(row: dict) -> str | None:
    record = row.get("recent_record_last_n")
    if not record:
        return None
    win_rate = _to_float(row.get("recent_win_rate_last_n"))
    delta = _to_float(row.get("current_implied_delta_vs_avg_last_n"))
    parts = [f"L5 at market prices: {record}"]
    if win_rate is not None:
        parts.append(f"win {win_rate:.0%}")
    if delta is not None:
        parts.append(f"current vs avg {delta:+.1%}")
    return " | ".join(parts)


def _factor_lean_label(value: str | None) -> str:
    if not value:
        return "-"
    mapping = {
        "home": "Home",
        "away": "Away",
        "over": "Over",
        "under": "Under",
        "neutral": "Neutral",
    }
    return mapping.get(value, value)


def _stat_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        if abs(value) >= 10:
            return f"{value:.1f}"
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def _pct_fraction(value: object) -> float | None:
    raw = _to_float(value)
    if raw is None:
        return None
    return raw / 100.0 if abs(raw) > 1.0 else raw


def _best_entry_value(line: dict) -> str:
    anchor = line.get("best_entry_anchor")
    price = _price(line.get("best_entry_price_american"))
    raw = line.get("best_entry_line")
    if not anchor:
        return "-"
    if line.get("market") == "moneyline" or raw is None:
        return f"{anchor} {price}"
    if line.get("market") == "total":
        total_prefix = "O" if line.get("side") == "over" else "U"
        return f"{anchor} {total_prefix} {raw:g} {price}"
    prefix = "+" if raw > 0 else ""
    return f"{anchor} {prefix}{raw:g} {price}"


def _best_line_move(game: dict) -> dict | None:
    best: dict | None = None
    best_score = -1.0
    for line in game.get("lines", []):
        score = 0.0
        if line.get("number_move_from_open") is not None:
            score = max(score, abs(float(line["number_move_from_open"])) * 10.0)
        if line.get("price_move_american_from_open") is not None:
            score = max(score, abs(float(line["price_move_american_from_open"])) / 10.0)
        if score > best_score:
            best = line
            best_score = score
    return best


def _largest_split_gap(game: dict) -> dict | None:
    best: dict | None = None
    best_gap = -1.0
    for split in game.get("split_summary", []):
        bets = _pct_fraction(split.get("bets_pct"))
        handle = _pct_fraction(split.get("handle_pct"))
        if bets is None or handle is None:
            continue
        gap = abs(handle - bets)
        if gap > best_gap:
            best = split
            best_gap = gap
    return best


def _market_readiness_verdicts(readiness: dict | None) -> dict[str, str]:
    return {
        str(row.get("market")): str(row.get("verdict"))
        for row in (readiness or {}).get("markets", [])
        if row.get("market")
    }


def _slate_intelligence(game: dict) -> dict:
    raw = game.get("slate_intelligence")
    return raw if isinstance(raw, dict) else {}


def _game_priority(
    game: dict,
    *,
    ev_summary: dict | None = None,
    market_readiness: dict | None = None,
) -> tuple[int, list[str], dict | None, dict | None]:
    intelligence = _slate_intelligence(game)
    if intelligence:
        return (
            int(intelligence.get("score") or 0),
            [str(reason) for reason in intelligence.get("reasons", [])],
            _best_line_move(game),
            _largest_split_gap(game),
        )

    reasons: list[str] = []
    score = 0
    readiness_by_market = _market_readiness_verdicts(market_readiness)

    if game.get("lines"):
        score += 2
        reasons.append("current DK lines")

    if not game.get("odds_stale") and game.get("odds_age_min") is not None:
        score += 3
        reasons.append("fresh odds")
    elif game.get("odds_age_min") is None:
        reasons.append("no odds")
    else:
        reasons.append("stale odds")

    best_move = _best_line_move(game)
    if best_move:
        line_move = best_move.get("number_move_from_open")
        price_move = best_move.get("price_move_american_from_open")
        move_score = 0
        if line_move is not None:
            move_score = max(move_score, int(abs(float(line_move)) >= 1.0) * 2 + int(abs(float(line_move)) >= 0.5))
        if price_move is not None:
            move_score = max(move_score, int(abs(float(price_move)) >= 20) * 2 + int(abs(float(price_move)) >= 10))
        if move_score:
            score += move_score
            if line_move not in (None, 0):
                reasons.append("number move")
            elif price_move not in (None, 0):
                reasons.append("price pressure")

    if any(line.get("best_entry_anchor") is None for line in game.get("lines", [])):
        reasons.append("anchor missing")

    if ev_summary and ev_summary.get("available"):
        game_markets = {line.get("market") for line in game.get("lines", []) if line.get("market")}
        evidence_markets = set((ev_summary.get("rows_predicted_by_market") or {}).keys())
        if game_markets & evidence_markets:
            score += 1
            reasons.append("strict OOF market")

    if readiness_by_market:
        game_markets = {line.get("market") for line in game.get("lines", []) if line.get("market")}
        verdicts = [readiness_by_market.get(market) for market in game_markets]
        if any(verdict == "ready" for verdict in verdicts):
            score += 2
            reasons.append("ready market")
        elif any(verdict in {"thin", "collect_more"} for verdict in verdicts):
            score += 1
            reasons.append("collectable market")

    start_time = _parse_utc(game.get("start_time_utc"))
    if start_time is not None:
        hours_to_start = (start_time - datetime.now(timezone.utc)).total_seconds() / 3600
        if 0 <= hours_to_start <= 8:
            score += 2
            reasons.append("starts soon")
        elif 8 < hours_to_start <= 24:
            score += 1
            reasons.append("today/tomorrow")

    best_split = _largest_split_gap(game)
    if best_split:
        handle_pct = _pct_fraction(best_split["handle_pct"]) or 0.0
        bets_pct = _pct_fraction(best_split["bets_pct"]) or 0.0
        gap = abs(handle_pct - bets_pct)
        if gap >= 0.10:
            score += 2
            reasons.append("split divergence")
        elif gap >= 0.05:
            score += 1
            reasons.append("split lean")

    if "No public splits" in game.get("flags", []):
        reasons.append("splits missing")

    return score, reasons, best_move, best_split


def _watch_queue_rows(
    games: list[dict],
    *,
    ev_summary: dict | None = None,
    market_readiness: dict | None = None,
) -> list[dict]:
    rows: list[dict] = []
    for game in games:
        score, reasons, best_move, best_split = _game_priority(
            game,
            ev_summary=ev_summary,
            market_readiness=market_readiness,
        )
        split_gap = None
        if best_split:
            split_gap = abs(
                (_pct_fraction(best_split["handle_pct"]) or 0.0)
                - (_pct_fraction(best_split["bets_pct"]) or 0.0)
            )
        rows.append(
            {
                "event_id": game["event_id"],
                "Start": _utc_to_et(game.get("start_time_utc"), include_date=True),
                "Game": f"{game['away_team']['name']} at {game['home_team']['name']}",
                "Queue Score": score,
                "Why Open": _slate_intelligence(game).get("headline")
                or ", ".join(reasons)
                or "schedule context",
                "Next": _slate_intelligence(game).get("next_action_label") or "-",
                "Best Move": (
                    "-"
                    if not best_move
                    else f"{best_move['label']} | {_move(best_move.get('number_move_from_open'))} num | "
                    f"{_price_move(best_move.get('price_move_american_from_open'))} price"
                ),
                "Split Gap": (
                    "-"
                    if not best_split
                    else f"{best_split['market']} {best_split['side']} | "
                    f"{split_gap or 0.0:.0%}"
                ),
                "Odds Age": "No odds"
                if game.get("odds_age_min") is None
                else f"{game['odds_age_min']}m",
            }
        )
    rows.sort(key=lambda row: (-row["Queue Score"], row["Odds Age"], row["Start"], row["Game"]))
    return rows


def _queue_sorted_games(
    games: list[dict],
    *,
    ev_summary: dict | None = None,
    market_readiness: dict | None = None,
) -> list[dict]:
    priority = {
        game["event_id"]: _game_priority(
            game,
            ev_summary=ev_summary,
            market_readiness=market_readiness,
        )[0]
        for game in games
    }
    return sorted(
        games,
        key=lambda game: (-priority.get(game["event_id"], 0), game.get("start_time_utc") or ""),
    )


def _render_slate_summary(games: list[dict]) -> None:
    fresh_games = sum(1 for game in games if game.get("odds_age_min") is not None and not game.get("odds_stale"))
    stale_games = sum(1 for game in games if game.get("odds_age_min") is not None and game.get("odds_stale"))
    no_odds_games = sum(1 for game in games if game.get("odds_age_min") is None)
    moving_games = sum(
        1
        for game in games
        if (
            (move := _best_line_move(game))
            and (
                (
                    move.get("number_move_from_open") is not None
                    and abs(float(move["number_move_from_open"])) >= 0.5
                )
                or (
                    move.get("price_move_american_from_open") is not None
                    and abs(float(move["price_move_american_from_open"])) >= 10
                )
            )
        )
    )
    split_games = sum(
        1
        for game in games
        if (
            (split := _largest_split_gap(game))
            and abs((_pct_fraction(split["handle_pct"]) or 0.0) - (_pct_fraction(split["bets_pct"]) or 0.0)) >= 0.05
        )
    )
    missing_data_games = sum(
        1 for game in games if {"No odds yet", "No public splits"} & set(game.get("flags", []))
    )

    st.caption(
        f"{len(games)} games | {fresh_games} fresh | {moving_games} moving | "
        f"{split_games} split gaps | {stale_games} stale | "
        f"{missing_data_games + no_odds_games} missing"
    )


def _game_market_pulse_items(
    game: dict,
    *,
    ev_summary: dict | None = None,
    market_readiness: dict | None = None,
) -> list[tuple[str, str, str]]:
    intelligence = _slate_intelligence(game)
    if intelligence.get("signals"):
        return [
            (
                str(signal.get("label") or "-"),
                str(signal.get("value") or "-"),
                str(signal.get("detail") or ""),
            )
            for signal in intelligence.get("signals", [])[:4]
            if isinstance(signal, dict)
        ]

    age = game.get("odds_age_min")
    if age is None:
        freshness_value = "No odds"
        freshness_detail = "Current DK lines missing"
    elif game.get("odds_stale"):
        freshness_value = f"{age}m stale"
        freshness_detail = "Refresh before trusting movement"
    else:
        freshness_value = f"{age}m fresh"
        freshness_detail = "Current DK snapshot available"

    best_move = _best_line_move(game)
    if best_move:
        move_value = best_move.get("label") or _event_market_label(best_move.get("market") or "")
        number_move = _move(best_move.get("number_move_from_open"))
        price_move = _price_move(best_move.get("price_move_american_from_open"))
        move_detail = f"{number_move} number | {price_move} price"
    else:
        move_value = "No move"
        move_detail = "Open/current movement not stored yet"

    best_split = _largest_split_gap(game)
    if best_split:
        gap = abs(
            (_pct_fraction(best_split["handle_pct"]) or 0.0)
            - (_pct_fraction(best_split["bets_pct"]) or 0.0)
        )
        split_value = f"{gap:.0%} gap"
        split_detail = f"{best_split.get('market')} {best_split.get('side')}"
    elif any("No public splits" in flag for flag in game.get("flags", [])):
        split_value = "No splits"
        split_detail = "Public split feed missing"
    else:
        split_value = "Quiet"
        split_detail = "No notable split divergence"

    readiness_by_market = _market_readiness_verdicts(market_readiness)
    game_markets = {line.get("market") for line in game.get("lines", []) if line.get("market")}
    ready_count = sum(1 for market in game_markets if readiness_by_market.get(str(market)) == "ready")
    thin_count = sum(
        1 for market in game_markets if readiness_by_market.get(str(market)) in {"thin", "collect_more"}
    )
    evidence_markets = set((ev_summary or {}).get("rows_predicted_by_market") or {})
    oof_overlap = sorted(str(market) for market in game_markets & evidence_markets)
    if ready_count:
        evidence_value = f"{ready_count} ready"
        evidence_detail = "Strict evidence present" if oof_overlap else "Readiness checks pass"
    elif thin_count:
        evidence_value = f"{thin_count} thin"
        evidence_detail = "Collect settled sample"
    elif readiness_by_market:
        evidence_value = "Missing"
        evidence_detail = "Readiness gaps remain"
    elif oof_overlap:
        evidence_value = "OOF seen"
        evidence_detail = ", ".join(oof_overlap[:3])
    else:
        evidence_value = "Research"
        evidence_detail = "Open game for line-level evidence"

    return [
        ("Freshness", freshness_value, freshness_detail),
        ("Strongest Move", move_value, move_detail),
        ("Split Pressure", split_value, split_detail),
        ("Evidence", evidence_value, evidence_detail),
    ]


def _render_slate_intelligence_strip(game: dict) -> None:
    intelligence = _slate_intelligence(game)
    if not intelligence:
        return
    headline = str(intelligence.get("headline") or "Monitor")
    score = int(intelligence.get("score") or 0)
    next_action = str(intelligence.get("next_action_label") or "Monitor")
    reasons = [str(reason) for reason in intelligence.get("reasons", []) if reason]
    gaps = [str(gap) for gap in intelligence.get("gaps", []) if gap]
    reason_text = ", ".join(reasons[:3]) or "schedule context"
    gap_text = ""
    if gaps:
        gap_text = f" | Gaps: {', '.join(gaps[:2])}"
    st.caption(f"{headline} | Score {score} | {reason_text} | Next: {next_action}{gap_text}")


def _render_game_market_pulse(
    game: dict,
    *,
    ev_summary: dict | None = None,
    market_readiness: dict | None = None,
) -> None:
    st.markdown("**Market Pulse**")
    cols = st.columns(4)
    for col, (label, value, detail) in zip(
        cols,
        _game_market_pulse_items(
            game,
            ev_summary=ev_summary,
            market_readiness=market_readiness,
        ),
    ):
        with col:
            st.caption(label)
            st.markdown(f"**{value}**")
            st.caption(detail)


def _render_watch_queue(
    games: list[dict],
    *,
    ev_summary: dict | None = None,
    market_readiness: dict | None = None,
    expanded: bool = False,
) -> None:
    rows = _watch_queue_rows(games, ev_summary=ev_summary, market_readiness=market_readiness)
    st.markdown("**Daily Betting Queue**")
    st.caption(
        "A secondary lens that prioritizes current lines, movement, freshness, start time, "
        "market readiness, and strict OOF coverage."
    )
    if not rows:
        st.info("No queued games yet.")
        return
    df = pd.DataFrame(rows[:12 if expanded else 8])
    st.dataframe(df, use_container_width=True, hide_index=True)


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_board(api_base: str, sport: str, mode: str, selected_date: date) -> dict:
    params = {
        "sport": sport,
        "mode": mode,
        "date": selected_date.strftime("%Y-%m-%d"),
    }
    resp = httpx.get(f"{api_base}/board", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_research(api_base: str, event_id: int) -> dict:
    resp = httpx.get(f"{api_base}/events/{event_id}/research", timeout=45)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_entry_ev_summary(api_base: str) -> dict:
    resp = httpx.get(f"{api_base}/analysis/entry-ev/latest", timeout=15)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_mlb_readiness(api_base: str) -> dict:
    resp = httpx.get(
        f"{api_base}/analysis/mlb/readiness",
        params={"sport": "baseball_mlb", "days_back": 1, "days_forward": 7, "limit": 80},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_mlb_market_readiness(api_base: str) -> dict:
    resp = httpx.get(
        f"{api_base}/analysis/mlb/market-readiness",
        params={"sport": "baseball_mlb", "days_back": 30, "days_forward": 7},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_mlb_evidence_growth(api_base: str) -> dict:
    resp = httpx.get(
        f"{api_base}/analysis/mlb/evidence-growth/latest",
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _research_market_row(research: dict, market: str, side: str) -> dict | None:
    for row in research.get("market_context", []):
        if row.get("market") == market and row.get("side") == side:
            return row
    return None


def _research_team_line_row(research: dict, team_name: str) -> dict | None:
    for row in research.get("team_line_evidence", []):
        if row.get("team_name") == team_name:
            return row
    return None


def _focus_key_part(value: str | None) -> str:
    if not value:
        return "market"
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "market"


def _focus_key_for_line(market: str, side: str, participant_name: str | None = None) -> str:
    return f"{market}:{side}:{_focus_key_part(participant_name)}"


def _line_evidence_by_focus_key(research: dict) -> dict[str, dict]:
    by_key: dict[str, dict] = {}
    for row in research.get("line_evidence_status", []):
        focus_key = row.get("focus_key") or _focus_key_for_line(
            row.get("market") or "",
            row.get("side") or "",
            row.get("participant_name"),
        )
        by_key[focus_key] = row
    return by_key


def _event_market_label(market: str) -> str:
    labels = {
        "moneyline": "Moneyline",
        "spread": "Run Line",
        "total": "Game Total",
        "team_totals": "Team Total",
        "pitcher_strikeouts": "Pitcher Strikeouts",
        "batter_hits": "Batter Hits",
        "batter_total_bases": "Batter Total Bases",
    }
    return labels.get(market, market.replace("_", " ").title())


def _focused_line_selection(event_id: int) -> tuple[str | None, str | None, str | None]:
    if st.session_state.get("board_research_event_id") != event_id:
        return None, None, None
    return (
        st.session_state.get("board_focus_market"),
        st.session_state.get("board_focus_side"),
        st.session_state.get("board_focus_key"),
    )


def _core_focused_rows(research: dict) -> list[dict]:
    evidence_by_key = _line_evidence_by_focus_key(research)
    rows = []
    for row in research.get("market_context", []):
        focus_key = (
            row.get("focus_key")
            or next(
                (
                    key
                    for key, evidence in evidence_by_key.items()
                    if evidence.get("market") == row.get("market")
                    and evidence.get("side") == row.get("side")
                    and evidence.get("participant_name") == row.get("selection")
                ),
                None,
            )
            or _focus_key_for_line(row.get("market") or "", row.get("side") or "", row.get("selection"))
        )
        row_copy = dict(row)
        row_copy["focus_key"] = focus_key
        row_copy["market_label"] = _event_market_label(row.get("market") or "")
        row_copy["participant_name"] = row.get("selection")
        rows.append(row_copy)
    return rows


def _team_total_focused_rows(research: dict) -> list[dict]:
    rows = []
    for row in research.get("team_line_evidence", []):
        team_name = row.get("team_name")
        for side, price_field, open_price_field, best_price_field, move_field in (
            (
                "over",
                "current_over_price_american",
                "open_over_price_american",
                "best_entry_over_price_american",
                "over_price_move_american_from_open",
            ),
            (
                "under",
                "current_under_price_american",
                "open_under_price_american",
                "best_entry_under_price_american",
                "under_price_move_american_from_open",
            ),
        ):
            rows.append(
                {
                    "focus_key": _focus_key_for_line("team_totals", side, team_name),
                    "market": "team_totals",
                    "market_label": "Team Total",
                    "side": side,
                    "selection": f"{team_name} Team Total {side.title()}",
                    "participant_name": team_name,
                    "current_line": row.get("current_team_total"),
                    "current_price_american": row.get(price_field),
                    "open_line": row.get("open_team_total"),
                    "open_price_american": row.get(open_price_field),
                    "best_entry_anchor": row.get("best_entry_anchor"),
                    "best_entry_line": row.get("best_entry_team_total"),
                    "best_entry_price_american": row.get(best_price_field),
                    "number_move_from_open": row.get("number_move_from_open"),
                    "price_move_american_from_open": row.get(move_field),
                    "latest_quote_utc": row.get("latest_quote_utc"),
                    "is_live": False,
                    "is_stale": False,
                    "record_vs_current_line_last_n": row.get("record_vs_current_line_last_n"),
                    "avg_margin_vs_current_line_last_n": row.get("avg_margin_vs_current_line_last_n"),
                    "recent_results_vs_current_line": row.get("recent_results_vs_current_line") or [],
                    "record_vs_market_line_last_n": row.get("record_vs_market_line_last_n"),
                    "avg_margin_vs_market_line_last_n": row.get("avg_margin_vs_market_line_last_n"),
                    "recent_results_vs_market_lines": row.get("recent_results_vs_market_lines") or [],
                    "settled_market_history": row.get("settled_market_history") or [],
                    "history_points": row.get("history_points") or [],
                    "signal_notes": [row["note"]] if row.get("note") else [],
                }
            )
    return rows


def _player_prop_focused_rows(research: dict) -> list[dict]:
    rows = []
    for row in research.get("player_prop_insights", []):
        player_name = row.get("player_name")
        market = row.get("market_key") or ""
        for side, price_field, open_price_field, best_price_field, move_field in (
            (
                "over",
                "over_price_american",
                "open_over_price_american",
                "best_entry_over_price_american",
                "over_price_move_american_from_open",
            ),
            (
                "under",
                "under_price_american",
                "open_under_price_american",
                "best_entry_under_price_american",
                "under_price_move_american_from_open",
            ),
        ):
            rows.append(
                {
                    "focus_key": _focus_key_for_line(market, side, player_name),
                    "market": market,
                    "market_label": row.get("market_label") or _event_market_label(market),
                    "side": side,
                    "selection": f"{player_name} {row.get('market_label') or _event_market_label(market)} {side.title()}",
                    "participant_name": player_name,
                    "team_name": row.get("team_name"),
                    "current_line": row.get("current_line"),
                    "current_price_american": row.get(price_field),
                    "open_line": row.get("open_line"),
                    "open_price_american": row.get(open_price_field),
                    "best_entry_anchor": row.get("best_entry_anchor"),
                    "best_entry_line": row.get("best_entry_line"),
                    "best_entry_price_american": row.get(best_price_field),
                    "number_move_from_open": row.get("number_move_from_open"),
                    "price_move_american_from_open": row.get(move_field),
                    "latest_quote_utc": row.get("latest_quote_utc"),
                    "is_live": False,
                    "is_stale": False,
                    "record_vs_current_line_last_n": row.get("record_vs_current_line_last_n"),
                    "avg_margin_vs_current_line_last_n": row.get("avg_margin_vs_current_line_last_n"),
                    "recent_results_vs_current_line": row.get("recent_results_vs_current_line") or [],
                    "record_vs_market_line_last_n": row.get("record_vs_market_line_last_n"),
                    "avg_margin_vs_market_line_last_n": row.get("avg_margin_vs_market_line_last_n"),
                    "recent_results_vs_market_lines": row.get("recent_results_vs_market_lines") or [],
                    "settled_market_history": row.get("settled_market_history") or [],
                    "history_points": row.get("history_points") or [],
                    "recent_results": row.get("recent_results") or [],
                    "avg_last_n": row.get("avg_last_n"),
                    "hit_rate_over_last_n": row.get("hit_rate_over_last_n"),
                    "hit_rate_under_last_n": row.get("hit_rate_under_last_n"),
                    "games_sampled": row.get("games_sampled"),
                    "context_note": row.get("context_note"),
                    "signal_notes": [
                        note for note in (row.get("context_note"), row.get("note")) if note
                    ],
                }
            )
    return rows


def _research_focused_rows(research: dict) -> list[dict]:
    rows = _core_focused_rows(research) + _team_total_focused_rows(research) + _player_prop_focused_rows(research)
    evidence_by_key = _line_evidence_by_focus_key(research)
    for row in rows:
        evidence = evidence_by_key.get(row.get("focus_key"))
        if evidence:
            row["evidence_tier"] = evidence.get("evidence_tier")
            row["market_readiness_verdict"] = evidence.get("market_readiness_verdict")
            row["line_lifecycle_status"] = evidence.get("line_lifecycle_status")
            row["gaps"] = evidence.get("gaps") or []
    return rows


def _focused_market_row(research: dict, event_id: int) -> dict | None:
    market, side, focus_key = _focused_line_selection(event_id)
    if not market or not side:
        return None
    for row in _research_focused_rows(research):
        if focus_key and row.get("focus_key") == focus_key:
            return row
    for row in _research_focused_rows(research):
        if row.get("market") == market and row.get("side") == side:
            return row
    return None


def _focused_evidence_status(research: dict, market_row: dict) -> dict | None:
    market = market_row.get("market")
    side = market_row.get("side")
    focus_key = market_row.get("focus_key")
    participant = market_row.get("participant_name") or market_row.get("selection")
    for row in research.get("line_evidence_status", []):
        if focus_key and row.get("focus_key") == focus_key:
            return row
        if (
            row.get("market") == market
            and row.get("side") == side
            and row.get("participant_name") == participant
        ):
            return row
    return None


def _focused_line_thesis(research: dict, market_row: dict) -> dict | None:
    focus_key = market_row.get("focus_key")
    market = market_row.get("market")
    side = market_row.get("side")
    participant = market_row.get("participant_name") or market_row.get("selection")
    for row in research.get("line_thesis", []):
        if focus_key and row.get("focus_key") == focus_key:
            return row
        if (
            row.get("market") == market
            and row.get("side") == side
            and row.get("participant_name") == participant
        ):
            return row
    return None


def _score_band(value: object) -> str:
    score = int(value or 0)
    if score >= 75:
        return "Strong"
    if score >= 50:
        return "Usable"
    if score >= 30:
        return "Thin"
    return "Weak"


def _render_line_thesis(research: dict, market_row: dict) -> None:
    thesis = _focused_line_thesis(research, market_row)
    if not thesis:
        return

    st.markdown("**Line Thesis**")
    st.caption(thesis.get("headline") or "Focused market readout.")

    score_cols = st.columns(4)
    line_score = int(thesis.get("line_quality_score") or 0)
    evidence_score = int(thesis.get("evidence_quality_score") or 0)
    score_items = [
        ("Current", thesis.get("current_summary") or "-"),
        ("Line Quality", f"{line_score}/100 - {_score_band(line_score)}"),
        ("Evidence Quality", f"{evidence_score}/100 - {_score_band(evidence_score)}"),
        ("Status", str(thesis.get("action_status") or "-").title()),
    ]
    for col, (label, value) in zip(score_cols, score_items):
        with col:
            st.caption(label)
            st.markdown(f"**{value}**")

    summary_cols = st.columns(4)
    summary_items = [
        ("Movement", thesis.get("movement_summary") or "-"),
        ("History", thesis.get("history_summary") or "-"),
        ("Evidence", thesis.get("evidence_summary") or "-"),
        ("Risk", thesis.get("risk_summary") or "-"),
    ]
    for col, (label, value) in zip(summary_cols, summary_items):
        with col:
            st.caption(label)
            st.write(value)

    support_points = thesis.get("support_points") or []
    caution_points = thesis.get("caution_points") or []
    point_cols = st.columns(2)
    with point_cols[0]:
        st.caption("Supports")
        if support_points:
            for point in support_points:
                st.caption(f"- {point}")
        else:
            st.caption("- No local supporting points yet.")
    with point_cols[1]:
        st.caption("Cautions")
        if caution_points:
            for point in caution_points:
                st.caption(f"- {point}")
        else:
            st.caption("- No major local cautions.")
    if thesis.get("next_step"):
        st.info(thesis["next_step"])


def _evidence_tier_label(value: str | None) -> str:
    mapping = {
        "research_only": "Research Only",
        "thin_validated": "Thin Validated",
        "validated_sample": "Validated Sample",
    }
    return mapping.get(value or "", value or "-")


def _render_focused_evidence_status(research: dict, market_row: dict) -> None:
    status = _focused_evidence_status(research, market_row)
    if not status:
        st.info("Line-level evidence status is pending for this market.")
        return

    st.markdown("**Evidence Status**")
    lifecycle = str(status.get("line_lifecycle_status") or "-").replace("_", " ").title()
    evidence_items = [
        ("Tier", _evidence_tier_label(status.get("evidence_tier"))),
        ("Promotion", str(status.get("promotion_status") or "research_only").replace("_", " ").title()),
        ("Readiness", _verdict_label(status.get("market_readiness_verdict"))),
        ("Lifecycle", lifecycle),
        ("Settled Rows", str(status.get("settled_sample_size", 0))),
        ("OOF Rows", str(status.get("oof_predicted_rows", 0))),
        ("OOF Flagged", str(status.get("oof_recommended_rows", 0))),
        ("Posted Sample", str(status.get("posted_line_sample_size", 0))),
    ]
    for start in range(0, len(evidence_items), 4):
        row_items = evidence_items[start : start + 4]
        cols = st.columns(len(row_items))
        for col, (label, value) in zip(cols, row_items):
            with col:
                st.caption(label)
                st.markdown(f"**{value}**")

    if status.get("evidence_tier") == "research_only":
        st.caption("This line is descriptive research context until strict OOF evidence exists.")
    elif status.get("evidence_tier") == "thin_validated":
        st.caption("Strict OOF evidence exists, but the market is still sample-sensitive.")
    else:
        st.caption("Strict OOF evidence and minimum market samples are present for this market.")

    gaps = status.get("gaps") or []
    if gaps:
        st.caption("Gaps: " + ", ".join(gaps))
    promotion_gaps = status.get("promotion_gaps") or []
    if promotion_gaps:
        st.caption("Promotion gates: " + ", ".join(promotion_gaps))


def _market_row_to_line(row: dict) -> dict:
    return {
        "focus_key": row.get("focus_key"),
        "market": row.get("market"),
        "side": row.get("side"),
        "label": row.get("selection"),
        "participant_name": row.get("participant_name"),
        "line": row.get("current_line"),
        "price_american": row.get("current_price_american"),
        "is_stale": row.get("is_stale"),
    }


def _research_game_stub(research: dict) -> dict:
    return {
        "event_id": research["event_id"],
        "away_team": research["away_team"],
        "home_team": research["home_team"],
        "odds_age_min": research.get("odds_age_min"),
        "odds_stale": research.get("odds_stale"),
    }


def _prop_stronger_side(row: dict) -> str:
    over_rate = _to_float(row.get("hit_rate_over_last_n"))
    under_rate = _to_float(row.get("hit_rate_under_last_n"))
    if over_rate is None and under_rate is None:
        return "-"
    if over_rate is None:
        return "Under"
    if under_rate is None:
        return "Over"
    if over_rate > under_rate:
        return "Over"
    if under_rate > over_rate:
        return "Under"
    return "Even"


def _event_odds_history_chart_df(row: dict) -> pd.DataFrame | None:
    history_points = row.get("history_points") or []
    rows = []
    line_values: list[float] = []
    for point in history_points:
        label = point.get("label") or _utc_to_et(point.get("collected_at_utc"), include_date=True)
        line_value = _to_float(point.get("line"))
        if line_value is not None:
            line_values.append(line_value)
        rows.append(
            {
                "Snapshot": label,
                "Line": line_value,
                "Over Price": point.get("over_price_american"),
                "Under Price": point.get("under_price_american"),
            }
        )
    if not rows:
        return None
    chart_df = pd.DataFrame(rows).set_index("Snapshot")
    unique_lines = {value for value in line_values if value is not None}
    if len(unique_lines) > 1:
        return chart_df[["Line"]]
    price_cols = [col for col in ["Over Price", "Under Price"] if chart_df[col].notna().any()]
    if price_cols:
        return chart_df[price_cols]
    return chart_df[["Line"]] if chart_df["Line"].notna().any() else None


def _team_total_history_chart_df(recent_games: list[dict], current_tt: float | None) -> pd.DataFrame | None:
    rows = []
    for game in sorted(
        recent_games,
        key=lambda row: row.get("start_time_utc") or "",
    ):
        if game.get("team_score") is None:
            continue
        row = {
            "Game": _utc_to_et(game.get("start_time_utc"), include_date=True).split(" ")[0],
            "Actual Runs": game.get("team_score"),
        }
        if game.get("close_implied_team_total") is not None:
            row["Close Implied TT"] = game.get("close_implied_team_total")
        if current_tt is not None:
            row["Current TT"] = current_tt
        rows.append(row)
    if not rows:
        return None
    return pd.DataFrame(rows).set_index("Game")


def _player_prop_history_chart_df(row: dict) -> pd.DataFrame | None:
    recent_results = row.get("recent_results") or []
    current_line = _to_float(row.get("current_line"))
    if not recent_results:
        return None
    chart_rows = []
    for result in sorted(
        recent_results,
        key=lambda item: item.get("game_date_utc") or "",
    ):
        chart_row = {
            "Game": result.get("label") or _utc_to_et(result.get("game_date_utc"), include_date=True).split(" ")[0],
            "Result": result.get("value"),
        }
        if current_line is not None:
            chart_row["Current Line"] = current_line
        chart_rows.append(chart_row)
    if not chart_rows:
        return None
    return pd.DataFrame(chart_rows).set_index("Game")


def _recent_market_results_chart_df(points: list[dict]) -> pd.DataFrame | None:
    if not points:
        return None
    chart_rows = []
    for point in sorted(
        points,
        key=lambda item: item.get("game_date_utc") or "",
    ):
        chart_rows.append(
            {
                "Game": point.get("label") or _utc_to_et(point.get("game_date_utc"), include_date=True).split(" ")[0],
                "Result": point.get("value"),
                "Posted Line": point.get("line"),
            }
        )
    if not chart_rows:
        return None
    return pd.DataFrame(chart_rows).set_index("Game")


def _recent_price_results_chart_df(points: list[dict]) -> pd.DataFrame | None:
    if not points:
        return None
    chart_rows = []
    for point in sorted(
        points,
        key=lambda item: item.get("game_date_utc") or "",
    ):
        chart_rows.append(
            {
                "Game": point.get("label") or _utc_to_et(point.get("game_date_utc"), include_date=True).split(" ")[0],
                "Posted Implied %": point.get("implied_probability"),
            }
        )
    if not chart_rows:
        return None
    return pd.DataFrame(chart_rows).set_index("Game")


def _spread_history_chart_df(recent_games: list[dict]) -> pd.DataFrame | None:
    rows = []
    for game in sorted(
        recent_games,
        key=lambda row: row.get("start_time_utc") or "",
    ):
        team_score = _to_float(game.get("team_score"))
        opp_score = _to_float(game.get("opp_score"))
        close_spread = _to_float(game.get("close_spread"))
        if team_score is None or opp_score is None or close_spread is None:
            continue
        rows.append(
            {
                "Game": _utc_to_et(game.get("start_time_utc"), include_date=True).split(" ")[0],
                "Margin": round(team_score - opp_score, 2),
                "Cover Threshold": round(-close_spread, 2),
            }
        )
    if not rows:
        return None
    return pd.DataFrame(rows).set_index("Game")


def _spread_record_label(recent_games: list[dict]) -> str | None:
    results = [
        game.get("spread_result")
        for game in recent_games
        if game.get("spread_result") in {"W", "L", "P"}
    ]
    if not results:
        return None
    wins = sum(1 for result in results if result == "W")
    losses = sum(1 for result in results if result == "L")
    pushes = sum(1 for result in results if result == "P")
    return f"ATS L5: {wins}-{losses}-{pushes}"


def _focused_factor_rows(research: dict, market: str) -> list[dict]:
    focus = "total" if market == "total" else "side"
    rows = []
    for factor in research.get("why_this_line", []):
        market_focus = factor.get("market_focus")
        if market_focus not in {focus, "game"}:
            continue
        rows.append(
            {
                "Factor": factor.get("factor"),
                "Lean": _factor_lean_label(factor.get("lean")),
                "Headline": factor.get("headline"),
            "Detail": factor.get("detail") or "-",
        }
    )
    return rows


def _market_profile_values(research: dict, market_row: dict) -> tuple[list[float], float | None, str]:
    market = market_row.get("market")
    side = market_row.get("side")
    if market == "total":
        values = [
            _to_float(point.get("line"))
            for point in (market_row.get("recent_results_vs_market_lines") or [])
            if _to_float(point.get("line")) is not None
        ]
        return [value for value in values if value is not None], _to_float(market_row.get("current_line")), "total"
    if market == "moneyline" and side in {"away", "home"}:
        values = [
            _to_float(point.get("implied_probability"))
            for point in (market_row.get("recent_results_vs_market_prices") or [])
            if _to_float(point.get("implied_probability")) is not None
        ]
        current_implied = _to_float(market_row.get("implied_probability"))
        if current_implied is None:
            current_price = market_row.get("current_price_american")
            if current_price is not None:
                price = int(current_price)
                current_implied = abs(price) / (abs(price) + 100.0) if price < 0 else 100.0 / (price + 100.0)
        return [value for value in values if value is not None], current_implied, "probability"
    if market == "spread" and side in {"away", "home"}:
        recent_games = research.get("team_metrics", {}).get(side, {}).get("recent_games", [])
        values = [
            _to_float(game.get("close_spread"))
            for game in recent_games
            if _to_float(game.get("close_spread")) is not None
        ][:5]
        return [value for value in values if value is not None], _to_float(market_row.get("current_line")), "spread"
    if market in {"team_totals", "pitcher_strikeouts", "batter_hits", "batter_total_bases"}:
        values = [
            _to_float(point.get("line"))
            for point in (market_row.get("recent_results_vs_market_lines") or [])
            if _to_float(point.get("line")) is not None
        ]
        return [value for value in values if value is not None], _to_float(market_row.get("current_line")), "number"
    return [], None, "number"


def _format_profile_value(value: float | None, value_type: str) -> str:
    if value is None:
        return "-"
    if value_type == "probability":
        return f"{value:.0%}"
    if value_type == "spread":
        return f"{value:+g}"
    return f"{value:g}"


def _format_profile_range(values: list[float], value_type: str) -> str:
    if not values:
        return "-"
    low = min(values)
    high = max(values)
    return f"{_format_profile_value(low, value_type)} to {_format_profile_value(high, value_type)}"


def _market_profile_summary(research: dict, market_row: dict) -> list[tuple[str, str]]:
    values, current_value, value_type = _market_profile_values(research, market_row)
    if not values or current_value is None:
        return []
    ordered = sorted(values)
    series = pd.Series(ordered)
    median = float(series.median())
    pct_rank = round(sum(1 for value in values if value <= current_value) / len(values), 3)
    delta_vs_median = round(current_value - median, 3)
    if value_type == "probability":
        delta_label = f"{delta_vs_median:+.1%}"
    else:
        delta_label = f"{delta_vs_median:+g}"
    return [
        ("Sample", str(len(values))),
        ("Recent Range", _format_profile_range(values, value_type)),
        ("Median", _format_profile_value(median, value_type)),
        ("Current vs Median", delta_label),
        ("Current Percentile", f"{pct_rank:.0%}"),
    ]


def _render_metric_grid(metrics: list[tuple[str, str]], *, columns: int = 2) -> None:
    filtered = [(label, value) for label, value in metrics if label]
    if not filtered:
        return
    for start in range(0, len(filtered), columns):
        row_items = filtered[start : start + columns]
        cols = st.columns(len(row_items))
        for col, (label, value) in zip(cols, row_items):
            with col:
                st.metric(label, value)


def _focused_factor_headlines(research: dict, market: str, limit: int = 3) -> list[str]:
    focus = "total" if market == "total" else "side"
    headlines: list[str] = []
    for factor in research.get("why_this_line", []):
        market_focus = factor.get("market_focus")
        if market_focus not in {focus, "game"}:
            continue
        headline = factor.get("headline")
        if not headline:
            continue
        lean = _factor_lean_label(factor.get("lean"))
        summary = headline if lean in {"-", "Neutral"} else f"{headline} ({lean})"
        if summary in headlines:
            continue
        headlines.append(summary)
        if len(headlines) >= limit:
            break
    return headlines


def _numeric_range_label(values: list[object]) -> str:
    clean = [float(value) for value in values if _to_float(value) is not None]
    if not clean:
        return "-"
    low = min(clean)
    high = max(clean)
    if abs(high - low) < 1e-9:
        return f"{low:g}"
    return f"{low:g} to {high:g}"


def _focused_team_order(market_row: dict) -> list[str]:
    side = market_row.get("side")
    if side == "away":
        return ["away", "home"]
    if side == "home":
        return ["home", "away"]
    return ["away", "home"]


def _focused_team_market_block(research: dict, market_row: dict, side: str) -> tuple[str, str, list[tuple[str, str]]]:
    team = research.get(f"{side}_team", {})
    team_name = team.get("name") or side.title()
    selected = market_row.get("side") == side and market_row.get("market") in {"moneyline", "spread"}
    team_metrics = research.get("team_metrics", {}).get(side, {})
    trend = research.get("team_trends", {}).get(side, {})
    recent_games = team_metrics.get("recent_games", [])
    team_line = _research_team_line_row(research, team_name) or {}

    subtitle_bits: list[str] = []
    if selected:
        subtitle_bits.append("Selected side")
    record = team_metrics.get("record")
    if record:
        subtitle_bits.append(f"Record {record}")
    run_diff = _to_float(trend.get("run_diff_l5"))
    if run_diff is not None:
        subtitle_bits.append(f"Run diff L5 {_move(run_diff)}")
    rest_days = trend.get("rest_days")
    if rest_days is not None:
        subtitle_bits.append(f"Rest {rest_days}d")
    line_source = team_line.get("line_source")
    if line_source:
        subtitle_bits.append(_line_source_label(line_source))

    metrics = [
        ("Current TT", _stat_value(_to_float(team_line.get("current_team_total")))),
        (
            "Imp TT Range",
            _numeric_range_label([game.get("close_implied_team_total") for game in recent_games]),
        ),
        (
            "Opp Imp Range",
            _numeric_range_label([game.get("close_implied_opponent_total") for game in recent_games]),
        ),
        ("Runs vs Impl L5", _move(_to_float(team_line.get("avg_runs_vs_close_implied_last_n")))),
        (
            "Allowed vs Opp Impl L5",
            _move(_to_float(team_line.get("avg_allowed_vs_close_implied_last_n"))),
        ),
        ("Avg Runs L5", _stat_value(_to_float(trend.get("avg_runs_for_l5")))),
    ]
    return team_name, " | ".join(subtitle_bits) or "Recent team market profile.", metrics


def _bullpen_usage_summary(research: dict) -> dict[str, dict]:
    summaries: dict[str, dict] = {}
    for row in research.get("bullpen_usage", []):
        team_name = row.get("team_name")
        if not team_name:
            continue
        summary = summaries.setdefault(
            team_name,
            {
                "pitchers": set(),
                "outings_last_3d": 0,
                "outs_last_3d": 0,
                "pitches_last_3d": 0,
                "last_appearance_utc": None,
            },
        )
        pitcher_name = row.get("pitcher_name")
        if pitcher_name:
            summary["pitchers"].add(pitcher_name)
        summary["outings_last_3d"] += int(row.get("outings_last_3d") or 0)
        summary["outs_last_3d"] += int(row.get("outs_last_3d") or 0)
        summary["pitches_last_3d"] += int(row.get("pitches_last_3d") or 0)
        last_appearance = row.get("last_appearance_utc")
        if last_appearance and (
            summary["last_appearance_utc"] is None
            or str(last_appearance) > str(summary["last_appearance_utc"])
        ):
            summary["last_appearance_utc"] = last_appearance
    return summaries


def _focused_pitching_block(research: dict, market_row: dict, side: str) -> tuple[str, str, list[tuple[str, str]]]:
    team = research.get(f"{side}_team", {})
    team_name = team.get("name") or side.title()
    selected = market_row.get("side") == side and market_row.get("market") in {"moneyline", "spread"}
    starter = research.get("starter_context", {}).get(side, {})
    bullpen = _bullpen_usage_summary(research).get(team_name, {})

    subtitle_bits: list[str] = []
    if selected:
        subtitle_bits.append("Selected side")
    starter_name = starter.get("player_name")
    if starter_name:
        subtitle_bits.append(starter_name)
    days_rest = starter.get("days_rest")
    if days_rest is not None:
        subtitle_bits.append(f"Starter rest {days_rest}d")
    pitchers_used = bullpen.get("pitchers")
    if pitchers_used:
        subtitle_bits.append(f"{len(pitchers_used)} RP used L3d")
    elif bullpen.get("outs_last_3d"):
        subtitle_bits.append("Bullpen workload available")
    elif starter.get("note"):
        subtitle_bits.append(starter["note"])

    metrics = [
        ("ERA L3", _stat_value(_to_float(starter.get("era_l3")))),
        ("WHIP L3", _stat_value(_to_float(starter.get("whip_l3")))),
        ("Avg IP L3", _stat_value(_to_float(starter.get("avg_ip_l3")))),
        ("Avg Pitches L3", _stat_value(_to_float(starter.get("avg_pitches_l3")))),
        ("Bullpen Outs L3d", _stat_value(_to_float(bullpen.get("outs_last_3d")))),
        ("Bullpen Pitches L3d", _stat_value(_to_float(bullpen.get("pitches_last_3d")))),
    ]
    return f"{team_name} Pitching", " | ".join(subtitle_bits) or "Starter and bullpen pressure.", metrics


def _environment_wind_value(env: dict) -> str:
    field_wind = env.get("field_wind_label")
    if field_wind and field_wind not in {"orientation pending", "indoor/roof"}:
        if field_wind == "blowing out":
            component = env.get("wind_out_mph")
        elif field_wind == "blowing in":
            component = env.get("wind_in_mph")
        else:
            component = env.get("crosswind_mph")
        return f"{field_wind} {component or 0} mph"
    direction = (env.get("wind_direction") or "").strip()
    speed = env.get("wind_mph")
    if speed is None and not direction:
        return "-"
    prefix = f"{speed} mph" if speed is not None else ""
    return f"{prefix} {direction}".strip()


def _render_mlb_focused_line_reasoning(research: dict, market_row: dict) -> None:
    if research.get("sport") != "baseball_mlb":
        return

    st.markdown("**MLB Line Reasoning**")
    st.caption(
        "Compact team market profile, pitching workload, and run-environment context around the selected number."
    )

    # 1. Compact Matchup DataFrame
    matchup_data = []
    for side in _focused_team_order(market_row):
        t_title, t_sub, t_metrics = _focused_team_market_block(research, market_row, side)
        p_title, p_sub, p_metrics = _focused_pitching_block(research, market_row, side)

        row = {"Matchup": t_title.replace(" Pitching", "").strip()}
        for label, val in t_metrics:
            row[label] = val
        for label, val in p_metrics:
            row[label] = val
        matchup_data.append(row)

    if matchup_data:
        st.dataframe(pd.DataFrame(matchup_data), use_container_width=True, hide_index=True)

    # 2. Flat Environment Ribbon
    env = research.get("environment_context") or {}
    if env.get("available"):
        venue = env.get("venue_name") or "-"
        roof = env.get("roof_type") or "-"
        temp = "-" if env.get("temperature_f") is None else f"{env.get('temperature_f')} F"
        wind = _environment_wind_value(env)
        park_r = _stat_value(_to_float(env.get("park_factor_runs")))
        park_hr = _stat_value(_to_float(env.get("park_factor_hr")))

        st.info(
            f"**{venue}** ({roof}) | **Temp:** {temp} | **Wind:** {wind} | "
            f"**Park Factors:** {park_r} Runs, {park_hr} HR",
            icon="🏟️"
        )
        if env.get("note"):
            st.caption(env["note"])
    else:
        st.caption(env.get("note") or "Run environment context is not available yet.")


def _render_event_market_selector(
    research: dict,
    event_id: int,
    *,
    sport: str,
    mode: str,
    selected_date: date,
) -> None:
    rows = _research_focused_rows(research)
    if not rows:
        return

    st.markdown("**Available Markets**")
    st.caption("Core lines and participant-specific MLB markets available for this game.")

    display_rows = []
    for row in rows:
        display_rows.append(
            {
                "Market": row.get("market_label") or _event_market_label(row.get("market") or ""),
                "Selection": row.get("selection") or row.get("participant_name") or "-",
                "Line": _line_value(
                    {
                        "line": row.get("current_line"),
                        "price_american": row.get("current_price_american"),
                    }
                ),
                "Move": _move(_to_float(row.get("number_move_from_open"))),
                "Evidence": _evidence_tier_label(row.get("evidence_tier")),
                "Readiness": _verdict_label(row.get("market_readiness_verdict")),
            }
        )
    st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True, height=230)

    option_labels = [
        (
            f"{row.get('market_label') or _event_market_label(row.get('market') or '')} | "
            f"{row.get('selection') or row.get('participant_name') or '-'} | "
            f"{_line_value({'line': row.get('current_line'), 'price_american': row.get('current_price_american')})}"
        )
        for row in rows
    ]
    current_key = st.session_state.get("board_focus_key")
    default_index = next(
        (idx for idx, row in enumerate(rows) if row.get("focus_key") == current_key),
        0,
    )
    select_cols = st.columns([4, 1])
    with select_cols[0]:
        selected_label = st.selectbox(
            "Focus market",
            option_labels,
            index=default_index,
            key=f"market_selector_{event_id}",
            label_visibility="collapsed",
        )
    selected_index = option_labels.index(selected_label)
    selected_row = rows[selected_index]
    with select_cols[1]:
        if st.button(
            "Open Line",
            key=f"open_market_selector_{event_id}",
            type="primary",
            use_container_width=True,
        ):
            _set_focused_line(
                event_id=event_id,
                market=selected_row["market"],
                side=selected_row["side"],
                sport=sport,
                mode=mode,
                selected_date=selected_date,
                focus_key=selected_row.get("focus_key"),
            )
            st.rerun()


def _render_focused_line_view(research: dict, event_id: int) -> None:
    market_row = _focused_market_row(research, event_id)
    if not market_row:
        return

    line = _market_row_to_line(market_row)
    game = _research_game_stub(research)
    st.markdown("**Selected Line View**")
    st.caption(
        "This is the direct answer to the clicked line: current price, movement, and the most relevant recent market-backed context."
    )

    open_value = (
        _price(market_row.get("open_price_american"))
        if market_row.get("market") == "moneyline"
        else (
            "-"
            if market_row.get("open_line") is None
            else f"{market_row.get('open_line')} {_price(market_row.get('open_price_american'))}"
        )
    )
    line_items = [
        ("Selection", line.get("label") or "-"),
        ("Open", open_value),
        ("Best Entry", _best_entry_value(market_row)),
        ("Current", _line_value(line)),
        ("Number Move", _move(_to_float(market_row.get("number_move_from_open")))),
        ("Price Move", _price_move(market_row.get("price_move_american_from_open"))),
        ("Updated", _utc_to_et(market_row.get("latest_quote_utc"), include_date=True)),
    ]
    for start in range(0, len(line_items), 4):
        row_items = line_items[start : start + 4]
        cols = st.columns(len(row_items))
        for col, (label, value) in zip(cols, row_items):
            with col:
                st.caption(label)
                st.markdown(f"**{value}**")

    _render_line_thesis(research, market_row)

    _render_focused_evidence_status(research, market_row)

    profile_items = _market_profile_summary(research, market_row)
    factor_headlines = _focused_factor_headlines(research, market_row.get("market") or "")
    if profile_items or factor_headlines:
        st.markdown("**Market Profile**")
        for start in range(0, len(profile_items), 3):
            row_items = profile_items[start : start + 3]
            cols = st.columns(len(row_items))
            for col, (label, value) in zip(cols, row_items):
                with col:
                    st.metric(label, value)
        if factor_headlines:
            st.caption("Top context: " + " | ".join(factor_headlines))

    action_cols = st.columns([1.2, 1.2, 4])
    with action_cols[0]:
        if st.button(
            "Pin Selected Line",
            key=f"pin_focus_{event_id}_{market_row.get('focus_key') or market_row.get('market')}_{market_row.get('side')}",
            use_container_width=True,
        ):
            _add_to_slip(game, line, thesis=_focused_line_thesis(research, market_row))
            st.toast("Added to Research Slip")
    with action_cols[1]:
        if st.button(
            "Clear Focus",
            key=f"clear_focus_{event_id}",
            use_container_width=True,
        ):
            _clear_focused_line()
            st.rerun()
    with action_cols[2]:
        st.caption(
            "Public split and movement notes: "
            + (", ".join(market_row.get("signal_notes", [])) or "none")
        )

    _render_mlb_focused_line_reasoning(research, market_row)

    factor_rows = _focused_factor_rows(research, market_row.get("market") or "")
    if factor_rows:
        st.dataframe(pd.DataFrame(factor_rows), use_container_width=True, hide_index=True)

    market = market_row.get("market")
    side = market_row.get("side")
    if market == "total" or market in {"team_totals", "pitcher_strikeouts", "batter_hits", "batter_total_bases"}:
        chart_specs = []
        if market_row.get("recent_results_vs_current_line"):
            chart_specs.append(
                (
                    f"Recent Results vs Current {_event_market_label(market or '').lower()}",
                    _recent_line_record_label(market_row, "current line"),
                    _recent_market_results_chart_df(market_row.get("recent_results_vs_current_line") or []),
                )
            )
        if market_row.get("recent_results_vs_market_lines"):
            chart_specs.append(
                (
                    "Recent Results vs Posted Lines",
                    _market_line_record_label(market_row, "posted lines"),
                    _recent_market_results_chart_df(market_row.get("recent_results_vs_market_lines") or []),
                )
            )
        if chart_specs:
            cols = st.columns(len(chart_specs))
            for col, (title, label, chart_df) in zip(cols, chart_specs):
                with col:
                    st.caption(title)
                    if label:
                        st.caption(label)
                    if chart_df is not None:
                        st.bar_chart(chart_df, height=220)
    elif market == "moneyline" and side in {"away", "home"}:
        st.caption(_price_record_label(market_row) or "Recent side price context pending.")
        chart_df = _recent_price_results_chart_df(market_row.get("recent_results_vs_market_prices") or [])
        if chart_df is not None:
            st.bar_chart(chart_df, height=220)
        team_metrics = research.get("team_metrics", {}).get(side, {})
        recent_games = team_metrics.get("recent_games", [])
        if recent_games:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Date": _utc_to_et(game.get("start_time_utc"), include_date=True),
                            "Opponent": game.get("opponent", {}).get("name"),
                            "Score": (
                                f"{game.get('team_score')}-{game.get('opp_score')}"
                                if game.get("team_score") is not None
                                else "-"
                            ),
                            "Close ML": _price(game.get("close_ml")),
                            "W/L": "W" if game.get("won") else ("L" if game.get("won") is False else "-"),
                        }
                        for game in recent_games[:5]
                        if game.get("close_ml") is not None
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
    elif market == "spread" and side in {"away", "home"}:
        team_metrics = research.get("team_metrics", {}).get(side, {})
        recent_games = team_metrics.get("recent_games", [])
        record_label = _spread_record_label(recent_games)
        if record_label:
            st.caption(record_label)
        chart_df = _spread_history_chart_df(recent_games)
        if chart_df is not None:
            st.bar_chart(chart_df, height=220)
        if recent_games:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Date": _utc_to_et(game.get("start_time_utc"), include_date=True),
                            "Opponent": game.get("opponent", {}).get("name"),
                            "Score": (
                                f"{game.get('team_score')}-{game.get('opp_score')}"
                                if game.get("team_score") is not None
                                else "-"
                            ),
                            "Close Spread": game.get("close_spread"),
                            "ATS": game.get("spread_result") or "-",
                        }
                        for game in recent_games[:5]
                        if game.get("close_spread") is not None
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )


def _append_research_ledger(
    *,
    item: dict,
    action: str,
    line_snapshot: dict | None = None,
    thesis: dict | None = None,
) -> None:
    _LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "key": item.get("key"),
        "event_id": item.get("event_id"),
        "game": item.get("game"),
        "market": item.get("market"),
        "side": item.get("side"),
        "focus_key": item.get("focus_key"),
        "label": item.get("label"),
        "value": item.get("value"),
        "note": item.get("note", ""),
        "status": item.get("status", "watching"),
        "outcome": item.get("outcome", ""),
        "line_snapshot": line_snapshot or item.get("line_snapshot") or {},
        "thesis": thesis or item.get("thesis") or {},
    }
    with _LEDGER_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


def _clear_focused_line() -> None:
    st.session_state.pop("board_focus_market", None)
    st.session_state.pop("board_focus_side", None)
    st.session_state.pop("board_focus_key", None)


def _set_focused_line(
    *,
    event_id: int,
    market: str,
    side: str,
    sport: str,
    mode: str,
    selected_date: date,
    focus_key: str | None = None,
) -> None:
    st.session_state["board_research_event_id"] = event_id
    st.session_state["board_focus_market"] = market
    st.session_state["board_focus_side"] = side
    st.session_state["board_focus_key"] = focus_key or _focus_key_for_line(market, side)
    lens_state = st.session_state.get("board_lens", "slate")
    lens = lens_state[0] if isinstance(lens_state, tuple) else lens_state
    _sync_board_query_params(
        sport,
        mode,
        selected_date,
        event_id,
        focus_market=market,
        focus_side=side,
        focus_key=st.session_state["board_focus_key"],
        lens=lens if isinstance(lens, str) else "slate",
    )


def _ensure_slip() -> list[dict]:
    if "research_slip" not in st.session_state:
        st.session_state["research_slip"] = []
    return st.session_state["research_slip"]


def _add_to_slip(game: dict, line: dict, *, thesis: dict | None = None) -> None:
    slip = _ensure_slip()
    focus_key = line.get("focus_key") or f"{line['market']}:{line['side']}"
    key = f"{game['event_id']}:{focus_key}"
    if any(item["key"] == key for item in slip):
        return
    item = {
        "key": key,
        "event_id": game["event_id"],
        "game": f"{game['away_team']['name']} @ {game['home_team']['name']}",
        "market": line["market"],
        "side": line["side"],
        "focus_key": focus_key,
        "label": line["label"],
        "value": _line_value(line),
        "age": game.get("odds_age_min"),
        "stale": bool(game.get("odds_stale")),
        "added_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": "",
        "status": "watching",
        "outcome": "",
        "line_snapshot": dict(line),
        "thesis": thesis or {},
    }
    slip.append(item)
    _append_research_ledger(
        item=item,
        action="pinned",
        line_snapshot=dict(line),
        thesis=thesis,
    )


def _render_slip() -> None:
    slip = _ensure_slip()
    st.subheader("Research Slip")
    st.caption("Selected lines for review. This does not place bets.")

    if not slip:
        st.info("Click a line to pin it here.")
        return

    for item in list(slip):
        stale_text = "stale" if item.get("stale") else "fresh"
        with st.container(border=True):
            st.markdown(f"**{item['label']}**")
            st.caption(item["game"])
            st.metric(item["market"].title(), item["value"])
            st.caption(f"Odds age: {item.get('age', '-')} min - {stale_text}")
            if item.get("added_at_utc"):
                st.caption(f"Added: {item['added_at_utc'][:16].replace('T', ' ')} UTC")
            with st.expander("Ledger Note", expanded=False):
                safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", item["key"])
                note = st.text_area("Note", value=item.get("note", ""), key=f"note_{safe_key}")
                status_options = ["watching", "reviewed", "passed", "acted", "closed"]
                current_status = item.get("status", "watching")
                status = st.selectbox(
                    "Status",
                    status_options,
                    index=status_options.index(current_status)
                    if current_status in status_options
                    else 0,
                    key=f"status_{safe_key}",
                )
                outcome_options = ["", "win", "loss", "push", "void", "unknown"]
                current_outcome = item.get("outcome", "")
                outcome = st.selectbox(
                    "Outcome",
                    outcome_options,
                    index=outcome_options.index(current_outcome)
                    if current_outcome in outcome_options
                    else 0,
                    format_func=lambda value: value.title() if value else "Pending",
                    key=f"outcome_{safe_key}",
                )
                if st.button("Save Ledger Note", key=f"save_{item['key']}", use_container_width=True):
                    item["note"] = note
                    item["status"] = status
                    item["outcome"] = outcome
                    _append_research_ledger(item=item, action="updated")
                    st.toast("Research ledger updated")
            if st.button("Remove", key=f"remove_{item['key']}"):
                _append_research_ledger(item=item, action="removed")
                slip.remove(item)
                st.rerun()

    if st.button("Clear slip", use_container_width=True):
        for item in list(slip):
            _append_research_ledger(item=item, action="cleared")
        st.session_state["research_slip"] = []
        st.rerun()


def _line_for(game: dict, market: str, side: str) -> dict | None:
    for line in game.get("lines", []):
        if line["market"] == market and line["side"] == side:
            return line
    return None


def _line_button(
    game: dict,
    line: dict | None,
    label: str,
    *,
    sport: str,
    mode: str,
    selected_date: date,
) -> None:
    if not line:
        st.button(
            f"{label}\n-",
            disabled=True,
            use_container_width=True,
            key=f"missing_{game['event_id']}_{label}",
        )
        return
    cue = _line_button_cue(line)
    button_label = f"{label}\n{_line_value(line)}"
    if cue:
        button_label = f"{button_label}\n{cue}"
    if st.button(
        button_label,
        key=f"slip_{game['event_id']}_{line['market']}_{line['side']}",
        use_container_width=True,
    ):
        _set_focused_line(
            event_id=game["event_id"],
            market=line["market"],
            side=line["side"],
            sport=sport,
            mode=mode,
            selected_date=selected_date,
            focus_key=_focus_key_for_line(line["market"], line["side"], line.get("label")),
        )
        st.toast("Opened focused line view")


def _render_game_row(
    api_base: str,
    game: dict,
    sport: str,
    mode: str,
    selected_date: date,
    *,
    ev_summary: dict | None = None,
    market_readiness: dict | None = None,
) -> None:
    away = game["away_team"]["name"]
    home = game["home_team"]["name"]
    status = game["status"].upper()
    score = ""
    if game.get("home_score") is not None:
        score = f" - {game['away_score']}:{game['home_score']}"
    age = game.get("odds_age_min")
    freshness = "No odds" if age is None else f"{age}m"
    if game.get("odds_stale"):
        freshness += " stale"

    expanded = st.session_state.get("board_research_event_id") == game["event_id"]
    with st.container(border=True):
        header_cols = st.columns([2.4, 1.1, 1.1])
        with header_cols[0]:
            st.markdown(f"**{away} @ {home}**")
            st.caption(_utc_to_et(game["start_time_utc"], include_date=True))
        with header_cols[1]:
            st.caption("Status")
            st.markdown(f"**{status}{score}**")
        with header_cols[2]:
            st.caption("Odds")
            st.markdown(f"**{freshness}**")

        odds_cols = st.columns(6)
        with odds_cols[0]:
            _line_button(
                game,
                _line_for(game, "spread", "away"),
                "Away Sprd",
                sport=sport,
                mode=mode,
                selected_date=selected_date,
            )
        with odds_cols[1]:
            _line_button(
                game,
                _line_for(game, "spread", "home"),
                "Home Sprd",
                sport=sport,
                mode=mode,
                selected_date=selected_date,
            )
        with odds_cols[2]:
            _line_button(
                game,
                _line_for(game, "total", "over"),
                "Over",
                sport=sport,
                mode=mode,
                selected_date=selected_date,
            )
        with odds_cols[3]:
            _line_button(
                game,
                _line_for(game, "total", "under"),
                "Under",
                sport=sport,
                mode=mode,
                selected_date=selected_date,
            )
        with odds_cols[4]:
            _line_button(
                game,
                _line_for(game, "moneyline", "away"),
                "Away ML",
                sport=sport,
                mode=mode,
                selected_date=selected_date,
            )
        with odds_cols[5]:
            _line_button(
                game,
                _line_for(game, "moneyline", "home"),
                "Home ML",
                sport=sport,
                mode=mode,
                selected_date=selected_date,
            )

        _render_slate_intelligence_strip(game)

        action_cols = st.columns([1, 1, 1, 1.2])
        with action_cols[3]:
            if st.button(
                "Research",
                key=f"load_research_{game['event_id']}",
                type="primary" if expanded else "secondary",
                use_container_width=True,
            ):
                st.session_state["board_research_event_id"] = game["event_id"]
                _clear_focused_line()
                lens_state = st.session_state.get("board_lens", "slate")
                lens = lens_state[0] if isinstance(lens_state, tuple) else lens_state
                _sync_board_query_params(
                    sport,
                    mode,
                    selected_date,
                    game["event_id"],
                    lens=lens if isinstance(lens, str) else "slate",
                )
                st.rerun()

        if not expanded:
            return

        if game.get("flags"):
            st.warning(" | ".join(game["flags"]))

        _render_game_market_pulse(
            game,
            ev_summary=ev_summary,
            market_readiness=market_readiness,
        )

        meta_cols = st.columns([1, 1, 1])
        with meta_cols[0]:
            st.caption("Markets")
            st.markdown(f"**{', '.join(game.get('markets_available', [])) or '-'}**")
        with meta_cols[1]:
            st.caption("Event ID")
            st.markdown(f"**{game['event_id']}**")
        with meta_cols[2]:
            st.caption("Research State")
            st.markdown("**Open**")

        lifecycle_rows = []
        for line in game.get("lines", []):
            lifecycle_rows.append(
                {
                    "Selection": line["label"],
                    "Book": line.get("book", "draftkings"),
                    "Open": (
                        "-"
                        if line.get("open_price_american") is None and line.get("open_line") is None
                        else (
                            _price(line.get("open_price_american"))
                            if line.get("market") == "moneyline"
                            else f"{line.get('open_line', '-')}"
                            f" {_price(line.get('open_price_american'))}"
                        )
                    ),
                    "Best Entry": _best_entry_value(line),
                    "Current": _line_value(line),
                    "Number Move": _move(line.get("number_move_from_open")),
                    "Price Move": _price_move(line.get("price_move_american_from_open")),
                }
            )
        if lifecycle_rows:
            st.dataframe(pd.DataFrame(lifecycle_rows), use_container_width=True, hide_index=True)

        _render_research_panel(
            api_base,
            game["event_id"],
            sport=sport,
            mode=mode,
            selected_date=selected_date,
        )


def _render_research_panel(
    api_base: str,
    event_id: int,
    *,
    sport: str,
    mode: str,
    selected_date: date,
) -> None:
    try:
        research = _fetch_research(api_base, event_id)
    except Exception as exc:
        st.error(f"Research payload failed: {exc}")
        return

    warnings = research.get("warnings", [])
    if warnings:
        st.info(" | ".join(warnings))

    _render_event_market_selector(
        research,
        event_id,
        sport=sport,
        mode=mode,
        selected_date=selected_date,
    )

    if _focused_market_row(research, event_id):
        _render_focused_line_view(research, event_id)
    else:
        st.caption(
            "Click a board line above to open a focused, visual explanation for that exact market."
        )

    tab_overview, tab_lines, tab_teams, tab_players, tab_model = st.tabs(
        ["Overview", "Lines", "Team Metrics", "Players", "Model Data"]
    )

    with tab_overview:
        cols = st.columns(4)
        cols[0].metric("Status", research["status"].upper())
        cols[1].metric("Odds Age", f"{research.get('odds_age_min') or '-'} min")
        cols[2].metric("Markets", len({line["market"] for line in research.get("lines", [])}))
        score = "-"
        if research.get("home_score") is not None:
            score = f"{research['away_score']} - {research['home_score']}"
        cols[3].metric("Score", score)

        why_rows = research.get("why_this_line", [])
        if why_rows:
            st.markdown("**Why This Line**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Factor": row.get("factor"),
                            "Focus": row.get("market_focus"),
                            "Lean": _factor_lean_label(row.get("lean")),
                            "Headline": row.get("headline"),
                            "Detail": row.get("detail") or "-",
                        }
                        for row in why_rows
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

        if research.get("sport") == "baseball_mlb":
            away_team_name = research["away_team"]["name"]
            home_team_name = research["home_team"]["name"]
            away_trend = research.get("team_trends", {}).get("away", {})
            home_trend = research.get("team_trends", {}).get("home", {})
            away_starter = research.get("starter_context", {}).get("away", {})
            home_starter = research.get("starter_context", {}).get("home", {})
            total_market = _research_market_row(research, "total", "over")
            away_ml_market = _research_market_row(research, "moneyline", "away")
            home_ml_market = _research_market_row(research, "moneyline", "home")
            away_team_line = _research_team_line_row(research, away_team_name)
            home_team_line = _research_team_line_row(research, home_team_name)

            combined_runs_l5 = None
            if (
                away_trend.get("avg_runs_for_l5") is not None
                and home_trend.get("avg_runs_for_l5") is not None
            ):
                combined_runs_l5 = round(
                    float(away_trend["avg_runs_for_l5"]) + float(home_trend["avg_runs_for_l5"]),
                    2,
                )

            avg_total_vs_market_l5 = None
            away_total_vs_market = _to_float(
                away_team_line.get("avg_game_total_vs_close_total_last_n")
                if away_team_line
                else None
            )
            home_total_vs_market = _to_float(
                home_team_line.get("avg_game_total_vs_close_total_last_n")
                if home_team_line
                else None
            )
            if away_total_vs_market is not None and home_total_vs_market is not None:
                avg_total_vs_market_l5 = round(
                    (away_total_vs_market + home_total_vs_market) / 2.0,
                    2,
                )

            total_delta_l5 = None
            current_total = _to_float(total_market.get("current_line") if total_market else None)
            if combined_runs_l5 is not None and current_total is not None:
                total_delta_l5 = round(combined_runs_l5 - current_total, 2)

            env = research.get("environment_context") or {}
            env_bits = []
            if env.get("venue_name"):
                env_bits.append(env["venue_name"])
            if env.get("field_wind_label"):
                env_bits.append(env["field_wind_label"])
            if env.get("temperature_f") is not None:
                env_bits.append(f"{env['temperature_f']:.0f} F")
            if env.get("park_factor_runs") is not None:
                env_bits.append(f"park runs {env['park_factor_runs']:.1f}")

            total_visual_rows = []
            if total_market:
                if total_market.get("open_line") is not None:
                    total_visual_rows.append(
                        {"Metric": "Open Total", "Value": total_market.get("open_line")}
                    )
                if total_market.get("best_entry_line") is not None:
                    total_visual_rows.append(
                        {"Metric": "Best Entry Total", "Value": total_market.get("best_entry_line")}
                    )
                if total_market.get("current_line") is not None:
                    total_visual_rows.append(
                        {"Metric": "Current Total", "Value": total_market.get("current_line")}
                    )
            if combined_runs_l5 is not None:
                total_visual_rows.append({"Metric": "Combined Runs L5", "Value": combined_runs_l5})

            total_case_rows = []
            if total_market or combined_runs_l5 is not None or avg_total_vs_market_l5 is not None:
                total_case_rows.append(
                    {
                        "Open Total": total_market.get("open_line") if total_market else None,
                        "Current Total": total_market.get("current_line") if total_market else None,
                        "Best Entry": (
                            "-"
                            if not total_market or not total_market.get("best_entry_anchor")
                            else (
                                f"{total_market.get('best_entry_anchor')} "
                                f"{total_market.get('best_entry_line', '-')}"
                            )
                        ),
                        "Combined Runs L5": combined_runs_l5,
                        "Combined vs Current": _move(total_delta_l5),
                        "Avg Total vs Market L5": _move(avg_total_vs_market_l5),
                        "Totals vs Current L6": (
                            total_market.get("record_vs_current_line_last_n") if total_market else None
                        ),
                        "Avg Margin vs Current": _move(
                            _to_float(
                                total_market.get("avg_margin_vs_current_line_last_n")
                                if total_market
                                else None
                            )
                        ),
                        "Totals vs Posted L6": (
                            total_market.get("record_vs_market_line_last_n") if total_market else None
                        ),
                        "Avg Margin vs Posted": _move(
                            _to_float(
                                total_market.get("avg_margin_vs_market_line_last_n")
                                if total_market
                                else None
                            )
                        ),
                        "Updated": _utc_to_et(
                            total_market.get("latest_quote_utc") if total_market else None,
                            include_date=True,
                        ),
                        "Environment": " | ".join(env_bits) if env_bits else "-",
                    }
                )

            team_total_case_rows = []
            for team_name, team_line, opposing_line in (
                (away_team_name, away_team_line, home_team_line),
                (home_team_name, home_team_line, away_team_line),
            ):
                if not team_line:
                    continue
                avg_runs_l5 = _to_float(team_line.get("avg_runs_last_n"))
                current_tt = _to_float(team_line.get("current_team_total"))
                avg_vs_current = None
                if avg_runs_l5 is not None and current_tt is not None:
                    avg_vs_current = round(avg_runs_l5 - current_tt, 2)
                team_total_case_rows.append(
                    {
                        "Team": team_name,
                        "Current TT": team_line.get("current_team_total"),
                        "Open TT": team_line.get("open_team_total"),
                        "Best Entry TT": team_line.get("best_entry_team_total"),
                        "Avg Runs L5": team_line.get("avg_runs_last_n"),
                        "Opp Allowed L5": (
                            opposing_line.get("avg_runs_allowed_last_n") if opposing_line else None
                        ),
                        "Avg vs TT": _move(avg_vs_current),
                        "Runs vs Implied L5": _move(
                            _to_float(team_line.get("avg_runs_vs_close_implied_last_n"))
                        ),
                        "TT O/U/P L5": team_line.get("team_total_record_last_n"),
                        "Updated": _utc_to_et(team_line.get("latest_quote_utc"), include_date=True),
                    }
                )

            team_total_visual_rows = []
            for team_name, team_line, opposing_line in (
                (away_team_name, away_team_line, home_team_line),
                (home_team_name, home_team_line, away_team_line),
            ):
                if not team_line:
                    continue
                team_total_visual_rows.append(
                    {
                        "Team": team_name,
                        "Current TT": team_line.get("current_team_total"),
                        "Avg Runs L5": team_line.get("avg_runs_last_n"),
                        "Opp Allowed L5": (
                            opposing_line.get("avg_runs_allowed_last_n") if opposing_line else None
                        ),
                        "Avg Implied TT L5": team_line.get("avg_close_implied_team_total_last_n"),
                    }
                )

            side_case_rows = []
            for team_name, trend, starter, moneyline in (
                (away_team_name, away_trend, away_starter, away_ml_market),
                (home_team_name, home_trend, home_starter, home_ml_market),
            ):
                if not trend and not starter and not moneyline:
                    continue
                side_case_rows.append(
                    {
                        "Team": team_name,
                        "Current ML": _price(moneyline.get("current_price_american") if moneyline else None),
                        "Open ML": _price(moneyline.get("open_price_american") if moneyline else None),
                        "Best Entry": (
                            "-"
                            if not moneyline or not moneyline.get("best_entry_anchor")
                            else f"{moneyline.get('best_entry_anchor')} {_price(moneyline.get('best_entry_price_american'))}"
                        ),
                        "Run Diff L5": trend.get("run_diff_l5"),
                        "Bullpen Outs L3": trend.get("avg_bullpen_outs_l3"),
                        "Rest Days": trend.get("rest_days"),
                        "Starter": starter.get("player_name"),
                        "Starter ERA L3": starter.get("era_l3"),
                        "Starter WHIP L3": starter.get("whip_l3"),
                        "Starter K-BB L3": starter.get("k_bb_l3"),
                        "W-L L5": moneyline.get("recent_record_last_n") if moneyline else None,
                        "Win % L5": _format_pct(
                            _to_float(moneyline.get("recent_win_rate_last_n")) if moneyline else None
                        ),
                        "Avg Close ML L5": _price(
                            moneyline.get("avg_market_price_american_last_n") if moneyline else None
                        ),
                        "Avg Close Imp L5": _format_pct(
                            _to_float(
                                moneyline.get("avg_market_implied_probability_last_n")
                                if moneyline
                                else None
                            )
                        ),
                        "Current Imp vs Avg": _move(
                            _to_float(
                                moneyline.get("current_implied_delta_vs_avg_last_n")
                                if moneyline
                                else None
                            ),
                            pct=True,
                        ),
                    }
                )

            if total_visual_rows or team_total_visual_rows:
                st.markdown("**Market Placement Visuals**")
                vis_cols = st.columns(2)
                with vis_cols[0]:
                    if total_visual_rows:
                        st.caption(
                            "Game total placement: open, best entry, current, and recent combined scoring."
                        )
                        total_chart_df = pd.DataFrame(total_visual_rows).set_index("Metric")
                        st.bar_chart(total_chart_df, height=240)
                with vis_cols[1]:
                    if team_total_visual_rows:
                        st.caption(
                            "Team total placement: current team totals against recent scoring and opponent prevention."
                        )
                        team_total_chart_df = pd.DataFrame(team_total_visual_rows).set_index("Team")
                        st.bar_chart(team_total_chart_df, height=240)

            if total_case_rows:
                st.markdown("**Game Total Case**")
                st.caption(
                    "Current total, recent combined scoring, and run-environment context in one place."
                )
                st.dataframe(
                    pd.DataFrame(total_case_rows),
                    use_container_width=True,
                    hide_index=True,
                )
                total_history_rows = []
                if total_market:
                    if total_market.get("recent_results_vs_current_line"):
                        total_history_rows.append(
                            (
                                "Recent Total Results vs Current Total",
                                _recent_line_record_label(total_market, "current total"),
                                _recent_market_results_chart_df(
                                    total_market.get("recent_results_vs_current_line") or []
                                ),
                            )
                        )
                    if total_market.get("recent_results_vs_market_lines"):
                        total_history_rows.append(
                            (
                                "Recent Total Results vs Posted Totals",
                                _market_line_record_label(total_market, "posted totals"),
                                _recent_market_results_chart_df(
                                    total_market.get("recent_results_vs_market_lines") or []
                                ),
                            )
                        )
                if total_history_rows:
                    history_cols = st.columns(len(total_history_rows))
                    for col, (title, label, chart_df) in zip(history_cols, total_history_rows):
                        with col:
                            st.caption(title)
                            if label:
                                st.caption(label)
                            if chart_df is not None:
                                st.bar_chart(chart_df, height=220)

            if team_total_case_rows:
                st.markdown("**Team Total Case**")
                st.caption(
                    "Recent team scoring, opponent prevention, and current team-total context."
                )
                st.dataframe(
                    pd.DataFrame(team_total_case_rows),
                    use_container_width=True,
                    hide_index=True,
                )

            if side_case_rows:
                st.markdown("**Side Market Case**")
                st.caption(
                    "Current side price with recent team form, bullpen usage, and starter form."
                )
                st.dataframe(
                    pd.DataFrame(side_case_rows),
                    use_container_width=True,
                    hide_index=True,
                )
                side_markets = [row for row in (away_ml_market, home_ml_market) if row]
                if side_markets:
                    side_cols = st.columns(len(side_markets))
                    for col, moneyline in zip(side_cols, side_markets):
                        with col:
                            st.caption(f"{moneyline.get('selection')} recent side price context")
                            label = _price_record_label(moneyline)
                            if label:
                                st.caption(label)
                            chart_df = _recent_price_results_chart_df(
                                moneyline.get("recent_results_vs_market_prices") or []
                            )
                            if chart_df is not None:
                                st.bar_chart(chart_df, height=220)

        matchup_rows = research.get("matchup_snapshot", [])
        if matchup_rows:
            st.markdown("**MLB Matchup Snapshot**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Category": row.get("category"),
                            "Metric": row.get("metric"),
                            "Away": _stat_value(row.get("away_value")),
                            "Home": _stat_value(row.get("home_value")),
                            "Note": row.get("note") or "-",
                        }
                        for row in matchup_rows
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

        if research.get("split_summary"):
            split_rows = [
                {
                    "Market": s["market"],
                    "Side": s["side"],
                    "Bets %": s.get("bets_pct"),
                    "Handle %": s.get("handle_pct"),
                    "Collected": _utc_to_et(s.get("collected_at_utc"), include_date=True),
                }
                for s in research["split_summary"]
            ]
            st.dataframe(pd.DataFrame(split_rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No public split rows are available for this game.")
        if research.get("data_gaps"):
            st.caption("Data gaps: " + ", ".join(research["data_gaps"]))
        env = research.get("environment_context") or {}
        if env:
            if env.get("available"):
                env_cols = st.columns(4)
                env_cols[0].metric("Weather", env.get("conditions") or "-")
                env_cols[1].metric("Temp", f"{env.get('temperature_f', '-')} F")
                field_wind = env.get("field_wind_label")
                if field_wind and field_wind not in {"orientation pending", "indoor/roof"}:
                    if field_wind == "blowing out":
                        component = env.get("wind_out_mph")
                    elif field_wind == "blowing in":
                        component = env.get("wind_in_mph")
                    else:
                        component = env.get("crosswind_mph")
                    wind_value = f"{field_wind} {component or 0} mph"
                else:
                    wind_value = (
                        f"{env.get('wind_mph', '-')} mph "
                        f"{env.get('wind_direction') or ''}".strip()
                    )
                env_cols[2].metric(
                    "Wind",
                    wind_value,
                )
                env_cols[3].metric("Park", env.get("venue_name") or "-")
                if env.get("note"):
                    st.caption(env["note"])
            else:
                st.caption(env.get("note") or "Environment context is not available yet.")

    with tab_lines:
        market_rows = research.get("market_context", [])
        line_rows = []
        if market_rows:
            for row in market_rows:
                line_rows.append(
                    {
                        "Market": row["market"],
                        "Side": row["side"],
                        "Selection": row["selection"],
                        "Book": row.get("book", "draftkings"),
                        "Best Entry": (
                            "-"
                            if not row.get("best_entry_anchor")
                            else f"{row.get('best_entry_anchor')} "
                            f"{row.get('best_entry_line', '-') if row.get('best_entry_line') is not None else ''} "
                            f"{_price(row.get('best_entry_price_american'))}".strip()
                        ),
                        "Current Line": row.get("current_line"),
                        "Current Price": _price(row.get("current_price_american")),
                        "Open Line": row.get("open_line"),
                        "Open Price": _price(row.get("open_price_american")),
                        "Number Move": _move(row.get("number_move_from_open")),
                        "Price Move": _price_move(row.get("price_move_american_from_open")),
                        "Imp Move": _move(row.get("price_move_implied_from_open"), pct=True),
                        "Vs Current L6": row.get("record_vs_current_line_last_n"),
                        "Avg vs Current L6": _move(_to_float(row.get("avg_margin_vs_current_line_last_n"))),
                        "Vs Posted L6": row.get("record_vs_market_line_last_n"),
                        "Avg vs Posted L6": _move(_to_float(row.get("avg_margin_vs_market_line_last_n"))),
                        "W-L L5": row.get("recent_record_last_n"),
                        "Win % L5": _format_pct(_to_float(row.get("recent_win_rate_last_n"))),
                        "Avg Close ML L5": _price(row.get("avg_market_price_american_last_n")),
                        "Current Imp vs Avg": _move(
                            _to_float(row.get("current_implied_delta_vs_avg_last_n")),
                            pct=True,
                        ),
                        "Bets %": row.get("bets_pct"),
                        "Handle %": row.get("handle_pct"),
                        "Signals": ", ".join(row.get("signal_notes", [])) or "-",
                        "Updated": _utc_to_et(row.get("latest_quote_utc"), include_date=True),
                    }
                )
        else:
            line_rows = [
                {
                    "Market": line["market"],
                    "Side": line["side"],
                    "Selection": line["label"],
                    "Book": line.get("book", "draftkings"),
                    "Best Entry": _best_entry_value(line),
                    "Current": _line_value(line),
                    "Open Line": line.get("open_line"),
                    "Open Price": _price(line.get("open_price_american")),
                    "Number Move": _move(line.get("number_move_from_open")),
                    "Price Move": _price_move(line.get("price_move_american_from_open")),
                    "Imp Move": _move(line.get("price_move_implied_from_open"), pct=True),
                    "Age": _utc_to_et(line.get("collected_at_utc"), include_date=True),
                    "Live": line.get("is_live"),
                    "Stale": line.get("is_stale"),
                }
                for line in research.get("lines", [])
            ]
        if line_rows:
            st.dataframe(pd.DataFrame(line_rows), use_container_width=True, hide_index=True)
        else:
            st.warning("No line data available.")

    with tab_teams:
        team_line_rows = research.get("team_line_evidence", [])
        if team_line_rows:
            st.markdown("**Team Line Evidence**")
            st.caption(
                "Line-backed scoring context from current team totals or derived implied team totals, "
                "paired with recent production versus market expectation."
            )
            st.caption(
                "`Posted TT Sample` counts settled games with a matched stored DraftKings team-total line."
            )
            team_line_notes = [
                row.get("note")
                for row in team_line_rows
                if row.get("note")
            ]
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Team": row.get("team_name"),
                            "Source": _line_source_label(row.get("line_source")),
                            "Current TT": row.get("current_team_total"),
                            "Open TT": row.get("open_team_total"),
                            "Best Entry": (
                                "-"
                                if not row.get("best_entry_anchor")
                                else f"{row.get('best_entry_anchor')} {row.get('best_entry_team_total')}"
                            ),
                            "TT Move": _move(row.get("number_move_from_open")),
                            "Over Move": _price_move(row.get("over_price_move_american_from_open")),
                            "Under Move": _price_move(row.get("under_price_move_american_from_open")),
                            "Updated": _utc_to_et(row.get("latest_quote_utc"), include_date=True),
                            "Games": row.get("games_sampled"),
                            "Posted TT Sample": row.get("posted_line_games_sampled"),
                            "Avg Runs L5": row.get("avg_runs_last_n"),
                            "Avg Allowed L5": row.get("avg_runs_allowed_last_n"),
                            "Close Implied TT L5": row.get("avg_close_implied_team_total_last_n"),
                            "Runs vs Implied TT L5": row.get("avg_runs_vs_close_implied_last_n"),
                            "Allowed - Opp Impl L5": row.get("avg_allowed_vs_close_implied_last_n"),
                            "Game Total - Close L5": row.get("avg_game_total_vs_close_total_last_n"),
                            "Team TT O/U/P L5": row.get("team_total_record_last_n"),
                            "Game O/U/P L5": row.get("game_total_record_last_n"),
                            "Vs Current TT L5": row.get("record_vs_current_line_last_n"),
                            "Avg vs Current TT L5": row.get("avg_margin_vs_current_line_last_n"),
                            "Vs Posted TT L5": row.get("record_vs_market_line_last_n"),
                            "Avg vs Posted TT L5": row.get("avg_margin_vs_market_line_last_n"),
                        }
                        for row in team_line_rows
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
            if team_line_notes:
                st.caption(team_line_notes[0])

            history_cols = st.columns(2)
            team_line_map = {row.get("team_name"): row for row in team_line_rows}
            for col, metric_key in zip(history_cols, ["away", "home"]):
                metrics = research["team_metrics"][metric_key]
                team_name = metrics["team"]["name"]
                current_tt = _to_float(
                    team_line_map.get(team_name, {}).get("current_team_total")
                    if team_line_map.get(team_name)
                    else None
                )
                chart_df = _team_total_history_chart_df(metrics.get("recent_games", []), current_tt)
                with col:
                    st.caption(f"{team_name} recent runs vs team-total context")
                    record_label = _recent_line_record_label(
                        team_line_map.get(team_name, {}) if team_line_map.get(team_name) else {},
                        "current TT",
                    )
                    if record_label:
                        st.caption(record_label)
                    if chart_df is not None:
                        st.bar_chart(chart_df, height=240)
                    else:
                        st.caption("Not enough final games for a team-total history view yet.")

            movement_cols = st.columns(2)
            for col, row in zip(movement_cols, team_line_rows[:2]):
                with col:
                    st.caption(f"{row.get('team_name')} team-total market history")
                    chart_df = _event_odds_history_chart_df(row)
                    if chart_df is not None:
                        st.line_chart(chart_df, height=240)
                    else:
                        st.caption("Not enough team-total snapshots yet.")

            posted_line_cols = st.columns(2)
            for col, row in zip(posted_line_cols, team_line_rows[:2]):
                with col:
                    st.caption(f"{row.get('team_name')} recent team-total results vs posted lines")
                    record_label = _market_line_record_label(row, "posted TT")
                    if record_label:
                        st.caption(record_label)
                    chart_df = _recent_market_results_chart_df(
                        row.get("recent_results_vs_market_lines") or []
                    )
                    if chart_df is not None:
                        st.bar_chart(chart_df, height=220)
                    else:
                        st.caption("Not enough prior team-total market history yet.")

            settled_team_history_rows = []
            for row in team_line_rows:
                for point in row.get("settled_market_history", []):
                    settled_team_history_rows.append(
                        {
                            "Team": row.get("team_name"),
                            "Date": point.get("label"),
                            "Opponent": point.get("opponent_name") or "-",
                            "Actual Runs": point.get("value"),
                            "Posted TT": point.get("line"),
                            "Margin": point.get("margin_vs_line"),
                            "Result": point.get("result"),
                            "Over": _price(point.get("over_price_american")),
                            "Under": _price(point.get("under_price_american")),
                        }
                    )
            if settled_team_history_rows:
                with st.expander("Matched Posted Team-Total History", expanded=False):
                    st.caption(
                        "Settled recent team-total history using matched stored DraftKings team-total lines."
                    )
                    st.dataframe(
                        pd.DataFrame(settled_team_history_rows),
                        use_container_width=True,
                        hide_index=True,
                    )

        trend_rows = []
        for side in ["away", "home"]:
            trend = research.get("team_trends", {}).get(side)
            if trend:
                trend_rows.append(
                    {
                        "Team": trend["team"]["name"],
                        "Games": trend.get("games", 0),
                        "Win %": trend.get("win_pct"),
                        "Runs For L5": trend.get("avg_runs_for_l5"),
                        "Runs Against L5": trend.get("avg_runs_against_l5"),
                        "Run Diff L5": trend.get("run_diff_l5"),
                        "Hits L5": trend.get("avg_hits_l5"),
                        "HR L5": trend.get("avg_home_runs_l5"),
                        "BB L5": trend.get("avg_walks_l5"),
                        "K L5": trend.get("avg_strikeouts_l5"),
                        "AVG L5": trend.get("batting_avg_l5"),
                        "SLG L5": trend.get("slugging_l5"),
                        "Bullpen Outs L3": trend.get("avg_bullpen_outs_l3"),
                        "Rest Days": trend.get("rest_days"),
                        "Last Game": _utc_to_et(trend.get("last_game_utc"), include_date=True),
                    }
                )
        if trend_rows:
            st.markdown("**MLB Team Trends**")
            st.dataframe(pd.DataFrame(trend_rows), use_container_width=True, hide_index=True)

        bullpen_rows = research.get("bullpen_usage", [])
        if bullpen_rows:
            st.markdown("**Recent Bullpen Workload**")
            st.caption("Recent relief usage from the last 3 days before the market snapshot.")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Team": row.get("team_name"),
                            "Pitcher": row.get("pitcher_name"),
                            "Outings L3d": row.get("outings_last_3d"),
                            "Outs L3d": row.get("outs_last_3d"),
                            "Pitches L3d": row.get("pitches_last_3d"),
                            "K L3d": row.get("strikeouts_last_3d"),
                            "BB L3d": row.get("walks_last_3d"),
                            "ER L3d": row.get("earned_runs_last_3d"),
                            "HR L3d": row.get("home_runs_allowed_last_3d"),
                            "Last App": _utc_to_et(row.get("last_appearance_utc"), include_date=True),
                        }
                        for row in bullpen_rows
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

        for side in ["away", "home"]:
            metric_key = "away" if side == "away" else "home"
            metrics = research["team_metrics"][metric_key]
            st.markdown(f"**{metrics['team']['name']}**")
            cols = st.columns(3)
            cols[0].metric("Record", metrics["record"])
            cols[1].metric("ATS", metrics["ats_record"])
            cols[2].metric("O/U", metrics["ou_record"])
            rows = []
            for row in metrics.get("recent_games", []):
                if research.get("sport") == "baseball_mlb":
                    open_ml = row.get("open_ml")
                    close_ml = row.get("close_ml")
                    rows.append(
                        {
                            "Date": _utc_to_et(row["start_time_utc"], include_date=True),
                            "Opponent": row["opponent"]["name"],
                            "H/A": "Home" if row["is_home"] else "Away",
                            "Score": (
                                f"{row['team_score']}-{row['opp_score']}"
                                if row.get("team_score") is not None
                                else "-"
                            ),
                            "Open ML": _price(open_ml),
                            "Close ML": _price(close_ml),
                            "ML Move": (
                                _price_move(close_ml - open_ml)
                                if open_ml is not None and close_ml is not None
                                else "-"
                            ),
                            "Open Total": row.get("open_total"),
                            "Close Total": row.get("close_total"),
                            "Total Move": (
                                _move(
                                    float(row["close_total"]) - float(row["open_total"])
                                )
                                if row.get("open_total") is not None and row.get("close_total") is not None
                                else "-"
                            ),
                            "Close Implied TT": row.get("close_implied_team_total"),
                            "Runs vs Implied TT": row.get("team_runs_vs_close_implied"),
                            "Game Total - Close": row.get("game_total_vs_close_total"),
                            "ML Result": "W" if row.get("won") else ("L" if row.get("won") is False else "-"),
                            "Team TT": row.get("team_total_result") or "-",
                            "Total Result": row.get("total_result") or "-",
                        }
                    )
                else:
                    rows.append(
                        {
                            "Date": _utc_to_et(row["start_time_utc"], include_date=True),
                            "Opponent": row["opponent"]["name"],
                            "H/A": "Home" if row["is_home"] else "Away",
                            "Status": row["status"],
                            "Score": (
                                f"{row['team_score']}-{row['opp_score']}"
                                if row.get("team_score") is not None
                                else "-"
                            ),
                            "Open Sprd": row.get("open_spread"),
                            "Close Sprd": row.get("close_spread"),
                            "ATS": row.get("spread_result") or "-",
                            "O/U": row.get("total_result") or "-",
                        }
                    )
            if research.get("sport") == "baseball_mlb":
                st.caption("Recent team-vs-market history using open/close moneyline and total context.")
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tab_players:
        starter_game_rows = []
        starter_rows = []
        for side in ["away", "home"]:
            starter = research.get("starter_context", {}).get(side)
            if starter:
                starter_rows.append(
                    {
                        "Team": starter["team"]["name"],
                        "Starter": starter.get("player_name") or "-",
                        "Prior Starts": starter.get("prior_starts", 0),
                        "Days Rest": starter.get("days_rest"),
                        "ERA L3": starter.get("era_l3"),
                        "WHIP L3": starter.get("whip_l3"),
                        "K-BB L3": starter.get("k_bb_l3"),
                        "Avg IP L3": starter.get("avg_ip_l3"),
                        "Avg Pitches L3": starter.get("avg_pitches_l3"),
                        "HR Allowed L3": starter.get("avg_home_runs_allowed_l3"),
                        "Last Start": _utc_to_et(starter.get("last_start_utc"), include_date=True),
                        "Note": starter.get("note") or "",
                    }
                )
        if starter_rows:
            st.markdown("**Probable Starter Context**")
            st.dataframe(pd.DataFrame(starter_rows), use_container_width=True, hide_index=True)
        player_rows = research.get("player_stats", [])
        for row in player_rows:
            if row.get("role") == "probable_starter" and row.get("last_games"):
                for game in row.get("last_games", []):
                    starter_game_rows.append(
                        {
                            "Team": row.get("team_name"),
                            "Starter": row.get("player_name"),
                            "Date": game.get("date"),
                            "IP": game.get("ip"),
                            "ER": game.get("er"),
                            "H": game.get("h"),
                            "BB": game.get("bb"),
                            "K": game.get("k"),
                            "Pitches": game.get("pitches"),
                        }
                    )
        if starter_game_rows:
            st.caption("Recent starts shown from the last 3 local starts before the market snapshot.")
            st.dataframe(pd.DataFrame(starter_game_rows), use_container_width=True, hide_index=True)

        prop_rows = research.get("player_prop_insights", [])
        if prop_rows:
            st.markdown("**Current Player Lines vs Recent Production**")
            st.caption(
                "This is the first honest player-line layer: current sportsbook line, recent average, "
                "and recent hit rate, with matchup context alongside it."
            )
            st.caption(
                "`Posted Line Sample` counts settled games with a matched stored DraftKings line for that prop market."
            )
            prop_gap_rows = []
            for row in prop_rows:
                avg_last_n = _to_float(row.get("avg_last_n"))
                current_line = _to_float(row.get("current_line"))
                if avg_last_n is None or current_line is None:
                    continue
                prop_gap_rows.append(
                    {
                        "Player / Market": f"{row.get('player_name')} | {row.get('market_label')}",
                        "Avg - Line": round(avg_last_n - current_line, 2),
                    }
                )
            if prop_gap_rows:
                st.caption(
                    "Player line gap snapshot: difference between recent average and current line. "
                    "Descriptive only, not settlement-aware."
                )
                prop_gap_df = (
                    pd.DataFrame(prop_gap_rows)
                    .assign(_abs_gap=lambda df: df["Avg - Line"].abs())
                    .sort_values(["_abs_gap", "Player / Market"], ascending=[False, True])
                    .drop(columns=["_abs_gap"])
                    .head(8)
                    .set_index("Player / Market")
                )
                st.bar_chart(prop_gap_df, height=260)
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Team": row.get("team_name"),
                            "Player": row.get("player_name"),
                            "Market": row.get("market_label"),
                            "Open Line": row.get("open_line"),
                            "Current Line": row.get("current_line"),
                            "Best Entry": (
                                "-"
                                if not row.get("best_entry_anchor")
                                else f"{row.get('best_entry_anchor')} {row.get('best_entry_line')}"
                            ),
                            "Line Move": _move(row.get("number_move_from_open")),
                            "Over": _price(row.get("over_price_american")),
                            "Over Move": _price_move(row.get("over_price_move_american_from_open")),
                            "Under": _price(row.get("under_price_american")),
                            "Under Move": _price_move(row.get("under_price_move_american_from_open")),
                            "Updated": _utc_to_et(row.get("latest_quote_utc"), include_date=True),
                            "Games": row.get("games_sampled"),
                            "Posted Line Sample": row.get("posted_line_games_sampled"),
                            "Avg L5": row.get("avg_last_n"),
                            "Avg - Line": (
                                round(
                                    _to_float(row.get("avg_last_n")) - _to_float(row.get("current_line")),
                                    2,
                                )
                                if _to_float(row.get("avg_last_n")) is not None
                                and _to_float(row.get("current_line")) is not None
                                else None
                            ),
                            "Vs Current Line L5": row.get("record_vs_current_line_last_n"),
                            "Avg vs Current Line L5": row.get("avg_margin_vs_current_line_last_n"),
                            "Vs Posted Line L5": row.get("record_vs_market_line_last_n"),
                            "Avg vs Posted Line L5": row.get("avg_margin_vs_market_line_last_n"),
                            "Stronger L5 Side": _prop_stronger_side(row),
                            "Over Hit Rate L5": _format_pct(row.get("hit_rate_over_last_n")),
                            "Under Hit Rate L5": _format_pct(row.get("hit_rate_under_last_n")),
                            "Last Values": ", ".join(str(value) for value in row.get("last_values", [])),
                            "Context": row.get("context_note") or row.get("note") or "",
                        }
                        for row in prop_rows
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

            movement_rows = [
                row
                for row in prop_rows
                if row.get("history_points")
            ][:4]
            if movement_rows:
                st.markdown("**Prop Market History**")
                st.caption(
                    "Line and price history for the stored pregame snapshots of the current prop market."
                )
                movement_cols = st.columns(2)
                for idx, row in enumerate(movement_rows):
                    with movement_cols[idx % 2]:
                        st.caption(f"{row.get('player_name')} | {row.get('market_label')}")
                        chart_df = _event_odds_history_chart_df(row)
                        if chart_df is not None:
                            st.line_chart(chart_df, height=220)

            posted_line_rows = [
                row
                for row in prop_rows
                if row.get("recent_results_vs_market_lines")
            ][:4]
            if posted_line_rows:
                st.markdown("**Recent Prop Results vs Posted Lines**")
                st.caption(
                    "These charts use each player’s own recent posted market line, not today’s line."
                )
                posted_cols = st.columns(2)
                for idx, row in enumerate(posted_line_rows):
                    with posted_cols[idx % 2]:
                        st.caption(f"{row.get('player_name')} | {row.get('market_label')}")
                        record_label = _market_line_record_label(row, "posted lines")
                        if record_label:
                            st.caption(record_label)
                        chart_df = _recent_market_results_chart_df(
                            row.get("recent_results_vs_market_lines") or []
                        )
                        if chart_df is not None:
                            st.bar_chart(chart_df, height=220)

            settled_prop_history_rows = []
            for row in prop_rows:
                for point in row.get("settled_market_history", []):
                    settled_prop_history_rows.append(
                        {
                            "Player": row.get("player_name"),
                            "Market": row.get("market_label"),
                            "Date": point.get("label"),
                            "Actual": point.get("value"),
                            "Posted Line": point.get("line"),
                            "Margin": point.get("margin_vs_line"),
                            "Result": point.get("result"),
                            "Over": _price(point.get("over_price_american")),
                            "Under": _price(point.get("under_price_american")),
                        }
                    )
            if settled_prop_history_rows:
                with st.expander("Matched Posted Prop History", expanded=False):
                    st.caption(
                        "Settled recent prop history using matched stored DraftKings player-market lines."
                    )
                    st.dataframe(
                        pd.DataFrame(settled_prop_history_rows),
                        use_container_width=True,
                        hide_index=True,
                    )

            chart_rows = [
                row
                for row in prop_rows
                if row.get("recent_results") and _to_float(row.get("current_line")) is not None
            ][:4]
            if chart_rows:
                st.markdown("**Recent Prop Results vs Current Line**")
                st.caption(
                    "Each chart shows recent game-by-game results against today’s line for the same stat family."
                )
                chart_cols = st.columns(2)
                for idx, row in enumerate(chart_rows):
                    with chart_cols[idx % 2]:
                        st.caption(f"{row.get('player_name')} | {row.get('market_label')}")
                        record_label = _recent_line_record_label(row, "current line")
                        if record_label:
                            st.caption(record_label)
                        chart_df = _player_prop_history_chart_df(row)
                        if chart_df is not None:
                            st.bar_chart(chart_df, height=220)
        elif research.get("player_props_note"):
            st.caption(research.get("player_props_note"))

        hitter_rows = [
            {
                "Team": row.get("team_name"),
                "Player": row.get("player_name"),
                "Pos": row.get("position"),
                "Games": row.get("games"),
                "AB": row.get("at_bats"),
                "H": row.get("hits"),
                "HR": row.get("home_runs"),
                "RBI": row.get("rbi"),
                "BB": row.get("base_on_balls"),
                "K": row.get("strike_outs"),
                "SB": row.get("stolen_bases"),
                "AVG": row.get("batting_avg"),
                "SLG": row.get("slugging"),
                "OBP*": row.get("obp_proxy"),
                "OPS*": row.get("ops_proxy"),
            }
            for row in player_rows
            if row.get("role") == "recent_hitter"
        ]
        if hitter_rows:
            st.markdown("**Recent Player Averages**")
            st.caption(
                "Fallback hitter production from local boxscores. Use this when current event-specific "
                "player lines are missing or thin."
            )
            st.dataframe(pd.DataFrame(hitter_rows), use_container_width=True, hide_index=True)
        elif not starter_rows:
            st.warning(research.get("player_stats_note", "Player stats source pending."))

    with tab_model:
        rows = []
        for feat in research.get("features", []):
            rows.append(
                {
                    "Market": feat.get("market"),
                    "Side": feat.get("side"),
                    "Imp OPEN": feat.get("implied_OPEN"),
                    "Imp T60": feat.get("implied_T60"),
                    "Imp T30": feat.get("implied_T30"),
                    "Imp CLOSE": feat.get("implied_CLOSE"),
                    "CLV OPEN": feat.get("clv_OPEN"),
                    "CLV T60": feat.get("clv_T60"),
                    "Late Steam": feat.get("late_steam"),
                    "EV": feat.get("model_expected_value"),
                }
            )
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No feature rows are available yet.")


def _starter_readiness_label(starter: dict | None) -> str:
    if not starter:
        return "missing"
    name = starter.get("player_name") or "starter"
    starts = starter.get("prior_starts", 0)
    return f"{name} ({starts} prior)"


def _render_mlb_readiness(readiness: dict) -> None:
    summary = readiness.get("summary", {})
    with st.container(border=True):
        st.markdown("**MLB Readiness Checks**")
        st.caption(
            "Local-only checks for the MLB odds, schedule, result, team trend, and starter "
            "history needed before strict entry-EV can train."
        )
        cols = st.columns(6)
        cols[0].metric("Settled Quoted", summary.get("settled_quoted_events", 0))
        cols[1].metric("Settled Trainable", summary.get("settled_trainable_events", 0))
        cols[2].metric("Pending Pregame", summary.get("pending_pregame_events", 0))
        cols[3].metric("Provider Mapped", summary.get("events_with_provider_key", 0))
        cols[4].metric("Team History", summary.get("events_with_both_team_history", 0))
        cols[5].metric("Starter History", summary.get("events_with_both_starter_history", 0))

        ready_count = summary.get("ready_after_settlement_events", 0)
        if ready_count:
            st.success(f"{ready_count} MLB games look ready to become modelable after settlement.")
        else:
            st.info("No MLB games are fully ready after settlement yet. The table shows the gaps.")

        for warning in readiness.get("warnings", []):
            st.warning(warning)

        rows = []
        for event in readiness.get("events", [])[:12]:
            rows.append(
                {
                    "Game": f"{event['away_team']['name']} @ {event['home_team']['name']}",
                    "Start": _utc_to_et(event.get("start_time_utc"), include_date=True),
                    "Status": event.get("status"),
                    "Pregame Odds": event.get("pregame_quote_count", 0),
                    "Team Logs": (
                        f"{event.get('away_team_logs_prior', 0)}/"
                        f"{event.get('home_team_logs_prior', 0)}"
                    ),
                    "Away Starter": _starter_readiness_label(event.get("away_starter")),
                    "Home Starter": _starter_readiness_label(event.get("home_starter")),
                    "Gaps": ", ".join(event.get("gaps", [])) or "none",
                }
            )
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _verdict_label(value: str | None) -> str:
    mapping = {
        "ready": "Ready",
        "thin": "Thin",
        "collect_more": "Collect More",
        "missing_data": "Missing Data",
    }
    return mapping.get(value or "", value or "-")


def _render_mlb_market_readiness(readiness: dict, growth: dict | None = None) -> None:
    summary = readiness.get("summary", {})
    markets = readiness.get("markets", [])
    with st.container(border=True):
        st.markdown("**MLB Market Readiness**")
        st.caption(
            "Market-level evidence checks: current DK lines, settled line history, strict OOF "
            "coverage, participant linkage, and stats context."
        )
        cols = st.columns(5)
        cols[0].metric("Ready", summary.get("markets_ready", 0))
        cols[1].metric("Thin", summary.get("markets_thin", 0))
        cols[2].metric("Collect More", summary.get("markets_collect_more", 0))
        cols[3].metric("Current Rows", summary.get("total_current_quoted_rows", 0))
        cols[4].metric("OOF Rows", summary.get("total_oof_predicted_rows", 0))

        if summary.get("artifact_anchor"):
            st.caption(
                f"Strict artifact: {summary.get('artifact_anchor')} | "
                f"{summary.get('artifact_generated_at_utc') or 'generated time unknown'}"
            )

        if growth and growth.get("available"):
            growth_summary = growth.get("summary", {})
            st.markdown("**Evidence Growth Snapshot**")
            growth_cols = st.columns(5)
            growth_cols[0].metric(
                "Event-Specific Quotes",
                growth_summary.get("event_specific_quotes", 0),
            )
            growth_cols[1].metric(
                "Event-Specific Games",
                growth_summary.get("event_specific_pregame_events", 0),
            )
            growth_cols[2].metric("OOF Rows", growth_summary.get("total_oof_predicted_rows", 0))
            growth_cols[3].metric(
                "Unlinked Props",
                growth_summary.get("unlinked_event_specific_player_quotes", 0),
            )
            growth_cols[4].metric(
                "Top Action",
                growth_summary.get("top_next_action_label") or "-",
            )
            st.caption(f"Last growth log: {growth.get('generated_at_utc')}")
            growth_markets = growth.get("markets") or []
            visible_growth = [
                row
                for row in growth_markets
                if row.get("verdict_changed")
                or row.get("current_quoted_rows_delta")
                or row.get("settled_quoted_rows_delta")
                or row.get("oof_predicted_rows_delta")
                or row.get("next_action") != "ready_for_review"
            ]
            if not visible_growth:
                visible_growth = growth_markets
            if visible_growth:
                growth_table = pd.DataFrame(
                    [
                        {
                            "Market": row.get("label") or row.get("market"),
                            "Verdict": _verdict_label(row.get("verdict")),
                            "Prev": _verdict_label(row.get("previous_verdict")),
                            "Current": (
                                f"{row.get('current_quoted_rows', 0) or 0} "
                                f"({int(row.get('current_quoted_rows_delta') or 0):+d})"
                            ),
                            "Settled": (
                                f"{row.get('settled_quoted_rows', 0) or 0} "
                                f"({int(row.get('settled_quoted_rows_delta') or 0):+d})"
                            ),
                            "OOF": (
                                f"{row.get('oof_predicted_rows', 0) or 0} "
                                f"({int(row.get('oof_predicted_rows_delta') or 0):+d})"
                            ),
                            "Next": row.get("next_action_label") or "-",
                            "Gaps": ", ".join(row.get("gaps", [])) or "none",
                        }
                        for row in visible_growth
                    ]
                )
                with st.expander("Market Growth Deltas", expanded=False):
                    st.dataframe(growth_table, use_container_width=True, hide_index=True)

        for warning in readiness.get("warnings", []):
            st.warning(warning)
        if growth and growth.get("available"):
            for warning in growth.get("warnings", []):
                st.warning(warning)

        if not markets:
            st.info("No MLB market readiness rows are available yet.")
            return

        priority_rows = sorted(
            [
                row
                for row in markets
                if row.get("next_action") and row.get("next_action") != "ready_for_review"
            ],
            key=lambda row: int(row.get("priority_score") or 0),
            reverse=True,
        )
        if priority_rows:
            st.markdown("**What To Collect Next**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Priority": row.get("priority_score", 0),
                            "Market": row.get("label"),
                            "Next Action": row.get("next_action_label"),
                            "Reason": row.get("next_action_reason") or "-",
                            "Command": row.get("next_action_command") or "-",
                        }
                        for row in priority_rows[:5]
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
            commands = [
                f"# {row.get('label')}: {row.get('next_action_label')}\n{row.get('next_action_command')}"
                for row in priority_rows[:3]
                if row.get("next_action_command")
            ]
            if commands:
                with st.expander("Run Commands", expanded=False):
                    st.code("\n\n".join(commands), language="powershell")

        table = pd.DataFrame(
            [
                {
                    "Market": row.get("label"),
                    "Verdict": _verdict_label(row.get("verdict")),
                    "Next Action": row.get("next_action_label") or "-",
                    "Current Rows": row.get("current_quoted_rows", 0),
                    "Current Events": row.get("current_quoted_events", 0),
                    "Settled Rows": row.get("settled_quoted_rows", 0),
                    "Settled Events": row.get("settled_quoted_events", 0),
                    "OOF Rows": row.get("oof_predicted_rows", 0),
                    "OOF Recs": row.get("oof_recommended_rows", 0),
                    "Link Rate": (
                        "-"
                        if row.get("participant_link_rate") is None
                        else f"{row.get('participant_link_rate'):.0%}"
                    ),
                    "Stats": (
                        f"{row.get('stat_context_rows', 0)} "
                        f"{row.get('stat_context_label') or ''}"
                    ).strip(),
                    "Gaps": ", ".join(row.get("gaps", [])) or "none",
                }
                for row in markets
            ]
        )
        st.dataframe(table, use_container_width=True, hide_index=True)


def render(api_base: str) -> None:
    st.markdown(
        """
        <style>
            div[data-testid="stButton"] button[kind="secondary"],
            div[data-testid="stButton"] [data-testid="stBaseButton-secondary"] {
                background: #f8fafc !important;
                color: #0f172a !important;
                border-color: #d7e0e6 !important;
                font-weight: 700 !important;
                min-height: 3.8rem !important;
                line-height: 1.25 !important;
                white-space: pre-line !important;
                padding: 0.35rem 0.45rem !important;
            }
            div[data-testid="stButton"] button[kind="secondary"]:hover,
            div[data-testid="stButton"] [data-testid="stBaseButton-secondary"]:hover {
                background: #e8f1ec !important;
                color: #0a0f0d !important;
                border-color: #86efac !important;
            }
            div[data-testid="stButton"] button[kind="primary"],
            div[data-testid="stButton"] [data-testid="stBaseButton-primary"] {
                color: #ffffff !important;
                font-weight: 800 !important;
                min-height: 3.8rem !important;
                line-height: 1.25 !important;
                white-space: pre-line !important;
                padding: 0.35rem 0.45rem !important;
            }
            div[role="radiogroup"] label p,
            label[data-testid="stWidgetLabel"] p {
                color: #d7ede3 !important;
                font-weight: 650 !important;
            }
            @media (max-width: 700px) {
                div[data-testid="stButton"] button,
                div[data-testid="stButton"] [data-testid^="stBaseButton"] {
                    min-height: 3.2rem !important;
                    font-size: 0.92rem !important;
                }
                div[data-testid="stMetric"] {
                    padding: 0.35rem 0.45rem !important;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.header("Sportsbook Board")
    st.caption("Private research board for lines, movement, freshness, and game context.")

    sports = ui_sport_choices()
    sport_keys = [key for key, _ in sports]
    query_sport = _query_value("sport")
    default_sport = query_sport if query_sport in sport_keys else sport_keys[0]

    mode_keys = [key for key, _ in _MODES]
    query_mode = _query_value("mode")
    default_mode = query_mode if query_mode in mode_keys else "today"

    query_event_id = _query_event_id()
    if query_event_id is not None:
        st.session_state["board_research_event_id"] = query_event_id
        query_focus_market = _query_focus_market()
        query_focus_side = _query_focus_side()
        if query_focus_market and query_focus_side:
            st.session_state["board_focus_market"] = query_focus_market
            st.session_state["board_focus_side"] = query_focus_side
            st.session_state["board_focus_key"] = _query_focus_key() or _focus_key_for_line(
                query_focus_market,
                query_focus_side,
            )
        else:
            _clear_focused_line()

    lens_keys = [key for key, _ in _LENSES]
    query_lens = _query_lens()
    raw_lens_state = query_lens or st.session_state.get("board_lens", "slate")
    default_lens = raw_lens_state[0] if isinstance(raw_lens_state, tuple) else raw_lens_state
    if default_lens not in lens_keys:
        default_lens = "slate"

    control_cols = st.columns([1.4, 1.2, 1.2, 1.4])
    with control_cols[0]:
        sport = st.radio(
            "Sport",
            sports,
            index=sport_keys.index(default_sport),
            format_func=lambda item: item[1],
            horizontal=True,
            key="board_sport",
        )[0]
    with control_cols[1]:
        mode = st.radio(
            "Board",
            _MODES,
            index=mode_keys.index(default_mode),
            format_func=lambda item: item[1],
            horizontal=True,
            key="board_mode",
        )[0]
    with control_cols[2]:
        selected_date = st.date_input("Date", value=_query_date("date"), key="board_date")
    with control_cols[3]:
        lens = st.radio(
            "Lens",
            _LENSES,
            index=lens_keys.index(default_lens),
            format_func=lambda item: item[1],
            horizontal=True,
            key="board_lens",
        )[0]

    selected_event_id = st.session_state.get("board_research_event_id")
    _sync_board_query_params(
        sport,
        mode,
        selected_date,
        selected_event_id,
        focus_market=st.session_state.get("board_focus_market"),
        focus_side=st.session_state.get("board_focus_side"),
        focus_key=st.session_state.get("board_focus_key"),
        lens=lens,
    )

    try:
        board = _fetch_board(api_base, sport, mode, selected_date)
    except Exception as exc:
        st.error(f"API error: {exc}")
        st.info("Start the API with: `uvicorn api.main:app --host 127.0.0.1 --port 8000`")
        return

    for warning in board.get("warnings", []):
        st.warning(warning)

    games = board.get("games", [])
    selected_event_id = st.session_state.get("board_research_event_id")
    if selected_event_id and not any(game["event_id"] == selected_event_id for game in games):
        st.session_state.pop("board_research_event_id", None)
        selected_event_id = None
        _clear_focused_line()
        _sync_board_query_params(sport, mode, selected_date, None, lens=lens)

    st.markdown("**Actionable Slate**")
    st.caption("Scan freshness, movement, and split pressure first. Keep diagnostics below unless you need to verify the pipeline.")
    _render_slate_summary(games)
    mlb_market_readiness: dict | None = None
    if sport == "baseball_mlb":
        try:
            mlb_market_readiness = _fetch_mlb_market_readiness(api_base)
            try:
                mlb_growth = _fetch_mlb_evidence_growth(api_base)
            except Exception:
                mlb_growth = None
            _render_mlb_market_readiness(mlb_market_readiness, growth=mlb_growth)
        except Exception as exc:
            st.warning(f"MLB market readiness unavailable: {exc}")

    try:
        ev_summary = _fetch_entry_ev_summary(api_base)
    except Exception:
        ev_summary = {"available": False, "warnings": ["Entry-EV artifact status unavailable."]}

    if lens == "queue":
        _render_watch_queue(
            games,
            ev_summary=ev_summary,
            market_readiness=mlb_market_readiness,
            expanded=True,
        )
        display_games = _queue_sorted_games(
            games,
            ev_summary=ev_summary,
            market_readiness=mlb_market_readiness,
        )
    else:
        display_games = games

    with st.expander("Research Evidence vs Validated Evidence", expanded=False):
        st.caption(
            "Use the board to investigate movement, splits, matchup context, and freshness. "
            "Only treat model-driven edge as validated when a strict OOF entry-EV artifact exists."
        )
        if ev_summary.get("available"):
            cols = st.columns(5)
            cols[0].metric("Anchor", ev_summary.get("anchor") or "-")
            cols[1].metric("OOF Rows", ev_summary.get("rows_predicted", 0))
            cols[2].metric("OOF Flagged", ev_summary.get("recommended_count", 0))
            cols[3].metric("ROI", f"{ev_summary.get('recommended_roi', 0.0):+.1%}")
            cols[4].metric(
                "Promotion",
                str(ev_summary.get("promotion_status") or "research_only")
                .replace("_", " ")
                .title(),
            )
            st.caption(
                "Evidence is from out-of-fold historical rows. OOF flagged rows are validation "
                "outputs, not automatic bet recommendations."
            )
            if float(ev_summary.get("recommended_roi") or 0.0) < 0:
                st.warning("Latest OOF-flagged rows are negative ROI. Treat this as validation plumbing, not a betting feed.")
            if ev_summary.get("promotion_gaps"):
                st.caption("Promotion gates: " + ", ".join(ev_summary.get("promotion_gaps", [])))
            market_counts = ev_summary.get("rows_predicted_by_market") or {}
            if market_counts:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Market": market,
                                "OOF Rows": count,
                                "Recommended": (ev_summary.get("recommended_by_market") or {}).get(
                                    market,
                                    0,
                                ),
                            }
                            for market, count in market_counts.items()
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            recommendations = ev_summary.get("recommendations") or []
            if recommendations:
                rec_df = pd.DataFrame(recommendations)
                rename = {
                    "participant_name": "Participant",
                    "participant_entity_type": "Type",
                    "entry_line": "Line",
                    "entry_price_american": "Price",
                    "oof_win_prob": "OOF Win Prob",
                    "break_even_prob": "Break Even",
                    "model_ev_units": "EV Units",
                    "actual_profit_units_1u": "Result Units",
                }
                st.dataframe(
                    rec_df.rename(columns=rename),
                    use_container_width=True,
                    hide_index=True,
                )
            for warning in ev_summary.get("warnings", []):
                st.warning(warning)
        else:
            st.info(
                "No validated entry-EV artifact is available yet. "
                "Run `python -m dk_ncaab oof-entry-ev` after collecting settled odds with prices."
            )

    if sport == "baseball_mlb":
        with st.expander("MLB Data Readiness", expanded=False):
            try:
                _render_mlb_readiness(_fetch_mlb_readiness(api_base))
            except Exception as exc:
                st.warning(f"MLB readiness diagnostics unavailable: {exc}")

    main_col, slip_col = st.columns([3.2, 1.15], gap="large")
    with slip_col:
        _render_slip()

    with main_col:
        if not games:
            st.info("No games matched this board filter. Try Upcoming or another sport.")
            return

        for game in display_games:
            _render_game_row(
                api_base,
                game,
                sport,
                mode,
                selected_date,
                ev_summary=ev_summary,
                market_readiness=mlb_market_readiness,
            )
