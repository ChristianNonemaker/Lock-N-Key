"""Settlement-aware MLB event-market history artifact export.

This module turns supported settled MLB team totals and player props into a
local parquet artifact that later feature-generation and strict EV work can
reuse. It uses only local DB data and never calls provider APIs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from dk_ncaab.config.props import PropMarketSpec, prop_market_specs_for_sport
from dk_ncaab.config.sports import league_key_for_sport
from dk_ncaab.db.models import (
    Event,
    EventOddsQuote,
    EventResult,
    League,
    MlbPlayerGameLog,
    MlbTeamGameLog,
    Player,
    Team,
)
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.etl.normalize import normalize_team_name

DEFAULT_OUT_DIR = Path("artifacts/market_history/mlb")


@dataclass(frozen=True)
class MlbMarketHistoryArtifactResult:
    run_dir: Path
    parquet_path: Path
    manifest_path: Path
    summary_path: Path
    latest_path: Path
    summary: dict


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _result_against_line(value: float | None, line: float | None) -> tuple[str | None, float | None]:
    if value is None or line is None:
        return None, None
    margin = round(float(value) - float(line), 3)
    if margin > 0:
        return "O", margin
    if margin < 0:
        return "U", margin
    return "P", margin


def _event_quote_at_anchor(
    quotes: list[EventOddsQuote],
    start_time_utc: datetime,
    anchor: str,
) -> EventOddsQuote | None:
    start = _ensure_utc(start_time_utc) or datetime.now(timezone.utc)
    if anchor == "OPEN":
        eligible = [quote for quote in quotes if (_ensure_utc(quote.collected_at_utc) or start) < start]
        if not eligible:
            return None
        return min(eligible, key=lambda quote: (_ensure_utc(quote.collected_at_utc), quote.id))

    offset_minutes = {"T60": 60, "T30": 30, "CLOSE": 0}[anchor]
    cutoff = start - timedelta(minutes=offset_minutes)
    eligible = [quote for quote in quotes if (_ensure_utc(quote.collected_at_utc) or start) < cutoff]
    if not eligible:
        return None
    return max(eligible, key=lambda quote: (_ensure_utc(quote.collected_at_utc), quote.id))


def _quote_snapshot_for_anchor(
    quote_by_side: dict[str, list[EventOddsQuote]],
    start_time_utc: datetime,
    anchor: str,
) -> dict[str, EventOddsQuote]:
    return {
        side: quote
        for side, quotes in quote_by_side.items()
        if (quote := _event_quote_at_anchor(quotes, start_time_utc, anchor)) is not None
    }


def _event_quote_line(quote_by_side: dict[str, EventOddsQuote]) -> float | None:
    quote = (
        quote_by_side.get("over")
        or quote_by_side.get("under")
        or quote_by_side.get("yes")
        or quote_by_side.get("no")
    )
    return float(quote.line) if quote and quote.line is not None else None


def _best_entry_snapshot(
    open_by_side: dict[str, EventOddsQuote],
    t60_by_side: dict[str, EventOddsQuote],
    t30_by_side: dict[str, EventOddsQuote],
) -> tuple[str | None, dict[str, EventOddsQuote]]:
    if t30_by_side:
        return "T30", t30_by_side
    if t60_by_side:
        return "T60", t60_by_side
    if open_by_side:
        return "OPEN", open_by_side
    return None, {}


def _mlb_player_market_value(log_row: MlbPlayerGameLog, stat_key: str) -> float | None:
    if stat_key == "pitching_strike_outs":
        return (
            float(log_row.pitching_strike_outs)
            if log_row.pitching_strike_outs is not None
            else None
        )
    if stat_key == "hits":
        return float(log_row.hits) if log_row.hits is not None else None
    if stat_key == "total_bases":
        if log_row.hits is None:
            return None
        doubles = log_row.doubles or 0
        triples = log_row.triples or 0
        home_runs = log_row.home_runs or 0
        singles = max(int(log_row.hits) - doubles - triples - home_runs, 0)
        return float(singles + (2 * doubles) + (3 * triples) + (4 * home_runs))
    return None


def _supported_market_specs(
    market_keys: list[str] | None,
) -> tuple[list[PropMarketSpec], list[str]]:
    supported = prop_market_specs_for_sport("baseball_mlb", collection_enabled_only=True)
    supported_keys = {spec.market_key for spec in supported}
    if market_keys:
        requested = list(dict.fromkeys(market_keys))
        unsupported = sorted(set(requested) - supported_keys)
        if unsupported:
            raise ValueError(
                "Unsupported MLB market-history markets: " + ", ".join(unsupported)
            )
        supported = [spec for spec in supported if spec.market_key in requested]
    return supported, [spec.market_key for spec in supported]


def _quote_group_key(quote: EventOddsQuote) -> tuple[int, str, str, int | None, int | None, str]:
    if quote.entity_type == "team":
        participant_key = normalize_team_name(quote.participant_name or "")
    else:
        participant_key = (quote.participant_name or "").strip().lower()
    return (
        quote.event_id,
        quote.market_key,
        quote.entity_type,
        quote.team_id,
        quote.player_id,
        participant_key,
    )


def _resolve_team_id_for_quote(
    quote: EventOddsQuote,
    event: Event,
    home_team: Team,
    away_team: Team,
) -> int | None:
    if quote.team_id in {event.home_team_id, event.away_team_id}:
        return quote.team_id
    participant = normalize_team_name(quote.participant_name or "")
    if participant == normalize_team_name(home_team.name):
        return home_team.id
    if participant == normalize_team_name(away_team.name):
        return away_team.id
    return quote.team_id


def build_mlb_market_history_frame(
    session: Session,
    *,
    market_keys: list[str] | None = None,
    event_limit: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Build a settled MLB team-total/prop history frame from local DB data only."""
    specs, selected_market_keys = _supported_market_specs(market_keys)
    specs_by_market = {spec.market_key: spec for spec in specs}
    league_key = league_key_for_sport("baseball_mlb")

    event_id_rows = session.execute(
        select(Event.id)
        .join(League, League.id == Event.league_id)
        .join(EventOddsQuote, EventOddsQuote.event_id == Event.id)
        .where(League.key == league_key)
        .where(Event.status == "final")
        .where(EventOddsQuote.book == "draftkings")
        .where(EventOddsQuote.market_key.in_(selected_market_keys))
        .group_by(Event.id)
        .order_by(Event.start_time_utc.desc(), Event.id.desc())
        .limit(event_limit)
        if event_limit
        else select(Event.id)
        .join(League, League.id == Event.league_id)
        .join(EventOddsQuote, EventOddsQuote.event_id == Event.id)
        .where(League.key == league_key)
        .where(Event.status == "final")
        .where(EventOddsQuote.book == "draftkings")
        .where(EventOddsQuote.market_key.in_(selected_market_keys))
        .group_by(Event.id)
        .order_by(Event.start_time_utc.desc(), Event.id.desc())
    ).all()
    event_ids = [row[0] for row in event_id_rows]
    if not event_ids:
        return pd.DataFrame(), {
            "sport": "baseball_mlb",
            "league_key": league_key,
            "market_keys": selected_market_keys,
            "events_considered": 0,
            "rows_exported": 0,
            "skipped_unresolved_participant": 0,
            "skipped_missing_actual": 0,
            "skipped_missing_pregame_line": 0,
            "rows_by_market": {},
        }

    events = list(
        session.execute(
            select(Event).where(Event.id.in_(event_ids)).order_by(Event.start_time_utc.asc(), Event.id.asc())
        ).scalars()
    )
    event_by_id = {event.id: event for event in events}

    event_results = {
        row.event_id: row
        for row in session.execute(
            select(EventResult).where(EventResult.event_id.in_(event_ids))
        ).scalars()
    }

    team_ids = {
        team_id
        for event in events
        for team_id in (event.home_team_id, event.away_team_id)
    }
    team_by_id = {
        team.id: team
        for team in session.execute(select(Team).where(Team.id.in_(team_ids))).scalars()
    }

    quotes = list(
        session.execute(
            select(EventOddsQuote)
            .where(EventOddsQuote.event_id.in_(event_ids))
            .where(EventOddsQuote.book == "draftkings")
            .where(EventOddsQuote.market_key.in_(selected_market_keys))
            .order_by(EventOddsQuote.event_id.asc(), EventOddsQuote.collected_at_utc.asc(), EventOddsQuote.id.asc())
        ).scalars()
    )
    grouped_quotes: dict[tuple[int, str, str, int | None, int | None, str], dict[str, list[EventOddsQuote]]] = {}
    player_ids: set[int] = set()
    for quote in quotes:
        grouped_quotes.setdefault(_quote_group_key(quote), {}).setdefault(quote.side, []).append(quote)
        if quote.player_id is not None:
            player_ids.add(quote.player_id)

    team_logs_by_event_team = {
        (row.event_id, row.team_id): row
        for row in session.execute(
            select(MlbTeamGameLog).where(MlbTeamGameLog.event_id.in_(event_ids))
        ).scalars()
    }
    player_logs_by_event_player = {
        (row.event_id, row.player_id): row
        for row in session.execute(
            select(MlbPlayerGameLog).where(MlbPlayerGameLog.event_id.in_(event_ids))
        ).scalars()
    }
    players_by_id = (
        {
            row.id: row
            for row in session.execute(select(Player).where(Player.id.in_(player_ids))).scalars()
        }
        if player_ids
        else {}
    )

    summary = {
        "sport": "baseball_mlb",
        "league_key": league_key,
        "market_keys": selected_market_keys,
        "events_considered": len(event_ids),
        "rows_exported": 0,
        "skipped_unresolved_participant": 0,
        "skipped_missing_actual": 0,
        "skipped_missing_pregame_line": 0,
        "rows_by_market": {market_key: 0 for market_key in selected_market_keys},
    }

    rows: list[dict] = []
    for group_key in sorted(grouped_quotes):
        event_id, market_key, entity_type, _team_id, player_id, _participant_key = group_key
        event = event_by_id.get(event_id)
        spec = specs_by_market.get(market_key)
        if event is None or spec is None:
            continue
        home_team = team_by_id.get(event.home_team_id)
        away_team = team_by_id.get(event.away_team_id)
        if home_team is None or away_team is None:
            summary["skipped_unresolved_participant"] += 1
            continue

        side_history = grouped_quotes[group_key]
        open_by_side = _quote_snapshot_for_anchor(side_history, event.start_time_utc, "OPEN")
        t60_by_side = _quote_snapshot_for_anchor(side_history, event.start_time_utc, "T60")
        t30_by_side = _quote_snapshot_for_anchor(side_history, event.start_time_utc, "T30")
        close_by_side = _quote_snapshot_for_anchor(side_history, event.start_time_utc, "CLOSE")
        close_line = _event_quote_line(close_by_side)
        if close_line is None:
            summary["skipped_missing_pregame_line"] += 1
            continue
        best_entry_anchor, best_entry_by_side = _best_entry_snapshot(open_by_side, t60_by_side, t30_by_side)

        representative = (
            close_by_side.get("over")
            or close_by_side.get("under")
            or open_by_side.get("over")
            or open_by_side.get("under")
        )
        if representative is None:
            summary["skipped_missing_pregame_line"] += 1
            continue

        participant_team_id: int | None = None
        participant_team_name: str | None = None
        opponent_team_id: int | None = None
        opponent_team_name: str | None = None
        participant_name = representative.participant_name
        actual_value: float | None = None
        is_home: bool | None = None

        if entity_type == "team":
            resolved_team_id = _resolve_team_id_for_quote(representative, event, home_team, away_team)
            if resolved_team_id is None:
                summary["skipped_unresolved_participant"] += 1
                continue
            team_log = team_logs_by_event_team.get((event_id, resolved_team_id))
            if team_log is None or team_log.runs_for is None:
                summary["skipped_missing_actual"] += 1
                continue
            participant_team_id = resolved_team_id
            participant_team_name = team_by_id.get(resolved_team_id).name if team_by_id.get(resolved_team_id) else None
            opponent_team_id = team_log.opponent_team_id
            opponent_team_name = (
                team_by_id.get(opponent_team_id).name if opponent_team_id in team_by_id else None
            )
            actual_value = float(team_log.runs_for)
            is_home = bool(team_log.is_home)
        else:
            if player_id is None:
                summary["skipped_unresolved_participant"] += 1
                continue
            log_row = player_logs_by_event_player.get((event_id, player_id))
            if log_row is None:
                summary["skipped_missing_actual"] += 1
                continue
            actual_value = _mlb_player_market_value(log_row, spec.stat_key)
            if actual_value is None:
                summary["skipped_missing_actual"] += 1
                continue
            player = players_by_id.get(player_id)
            participant_name = player.full_name if player else representative.participant_name
            participant_team_id = log_row.team_id
            participant_team_name = (
                team_by_id.get(participant_team_id).name if participant_team_id in team_by_id else None
            )
            is_home = bool(log_row.is_home)
            if participant_team_id == event.home_team_id:
                opponent_team_id = event.away_team_id
            elif participant_team_id == event.away_team_id:
                opponent_team_id = event.home_team_id
            opponent_team_name = (
                team_by_id.get(opponent_team_id).name if opponent_team_id in team_by_id else None
            )

        settled_result, margin_vs_close = _result_against_line(actual_value, close_line)
        if settled_result is None or margin_vs_close is None:
            summary["skipped_missing_pregame_line"] += 1
            continue

        event_result = event_results.get(event_id)
        open_line = _event_quote_line(open_by_side)
        t60_line = _event_quote_line(t60_by_side)
        t30_line = _event_quote_line(t30_by_side)
        best_entry_line = _event_quote_line(best_entry_by_side)

        rows.append(
            {
                "sport": "baseball_mlb",
                "league_key": league_key,
                "event_id": event.id,
                "external_event_key": event.external_event_key,
                "start_time_utc": _ensure_utc(event.start_time_utc),
                "completed_at_utc": _ensure_utc(event_result.completed_at_utc) if event_result else None,
                "market_key": spec.market_key,
                "market_label": spec.label,
                "provider_market_key": spec.provider_market_key,
                "entity_type": spec.entity_type,
                "selection_type": spec.selection_type,
                "stat_key": spec.stat_key,
                "participant_name": participant_name,
                "player_id": player_id,
                "participant_team_id": participant_team_id,
                "participant_team_name": participant_team_name,
                "opponent_team_id": opponent_team_id,
                "opponent_team_name": opponent_team_name,
                "is_home": is_home,
                "home_team_id": event.home_team_id,
                "home_team_name": home_team.name,
                "away_team_id": event.away_team_id,
                "away_team_name": away_team.name,
                "actual_value": round(float(actual_value), 3),
                "settled_result": settled_result,
                "margin_vs_line_CLOSE": margin_vs_close,
                "line_OPEN": open_line,
                "line_T60": t60_line,
                "line_T30": t30_line,
                "line_CLOSE": close_line,
                "over_price_american_OPEN": open_by_side.get("over").price_american if open_by_side.get("over") else None,
                "under_price_american_OPEN": open_by_side.get("under").price_american if open_by_side.get("under") else None,
                "over_price_american_T60": t60_by_side.get("over").price_american if t60_by_side.get("over") else None,
                "under_price_american_T60": t60_by_side.get("under").price_american if t60_by_side.get("under") else None,
                "over_price_american_T30": t30_by_side.get("over").price_american if t30_by_side.get("over") else None,
                "under_price_american_T30": t30_by_side.get("under").price_american if t30_by_side.get("under") else None,
                "over_price_american_CLOSE": close_by_side.get("over").price_american if close_by_side.get("over") else None,
                "under_price_american_CLOSE": close_by_side.get("under").price_american if close_by_side.get("under") else None,
                "best_entry_anchor": best_entry_anchor,
                "line_best_entry": best_entry_line,
                "over_price_american_best_entry": (
                    best_entry_by_side.get("over").price_american if best_entry_by_side.get("over") else None
                ),
                "under_price_american_best_entry": (
                    best_entry_by_side.get("under").price_american if best_entry_by_side.get("under") else None
                ),
                "number_move_from_open_to_close": (
                    round(float(close_line - open_line), 3)
                    if close_line is not None and open_line is not None
                    else None
                ),
                "over_price_move_american_from_open_to_close": (
                    close_by_side["over"].price_american - open_by_side["over"].price_american
                    if close_by_side.get("over") and open_by_side.get("over")
                    else None
                ),
                "under_price_move_american_from_open_to_close": (
                    close_by_side["under"].price_american - open_by_side["under"].price_american
                    if close_by_side.get("under") and open_by_side.get("under")
                    else None
                ),
            }
        )
        summary["rows_exported"] += 1
        summary["rows_by_market"][spec.market_key] = summary["rows_by_market"].get(spec.market_key, 0) + 1

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.sort_values(
            by=["start_time_utc", "market_key", "participant_name"],
            kind="stable",
        ).reset_index(drop=True)
        summary["events_exported"] = int(frame["event_id"].nunique())
    else:
        summary["events_exported"] = 0
    return frame, summary


def generate_mlb_market_history_artifact(
    *,
    session: Session | None = None,
    market_keys: list[str] | None = None,
    event_limit: int | None = None,
    out_dir: str | Path = DEFAULT_OUT_DIR,
) -> MlbMarketHistoryArtifactResult:
    """Build and export a local MLB event-market history artifact bundle."""
    own_session = session is None
    if own_session:
        session = SessionLocal()

    try:
        frame, summary = build_mlb_market_history_frame(
            session,
            market_keys=market_keys,
            event_limit=event_limit,
        )
    finally:
        if own_session:
            session.close()

    if frame.empty:
        raise ValueError("No settled MLB event-specific market history rows were available to export.")

    generated_at = datetime.now(timezone.utc)
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(out_dir) / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    parquet_path = run_dir / "market_history.parquet"
    manifest_path = run_dir / "manifest.json"
    summary_path = run_dir / "summary.json"
    latest_path = Path(out_dir) / "latest.json"

    frame.to_parquet(parquet_path, index=False)

    artifact_summary = {
        **summary,
        "generated_at_utc": generated_at.isoformat(),
        "parquet_path": str(parquet_path),
    }
    manifest = {
        **artifact_summary,
        "event_limit": event_limit,
        "market_keys": market_keys or summary["market_keys"],
    }

    summary_path.write_text(json.dumps(artifact_summary, indent=2, sort_keys=True), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    latest_tmp = latest_path.with_suffix(".tmp")
    latest_tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    latest_tmp.replace(latest_path)

    return MlbMarketHistoryArtifactResult(
        run_dir=run_dir,
        parquet_path=parquet_path,
        manifest_path=manifest_path,
        summary_path=summary_path,
        latest_path=latest_path,
        summary=artifact_summary,
    )


def read_latest_mlb_market_history(out_dir: str | Path = DEFAULT_OUT_DIR) -> dict | None:
    latest = Path(out_dir) / "latest.json"
    if not latest.exists():
        return None
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        return None
