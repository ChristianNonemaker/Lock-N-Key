"""Append-only MLB evidence growth snapshots."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from dk_ncaab.analysis.mlb_inventory import build_mlb_data_inventory
from dk_ncaab.analysis.mlb_market_readiness import build_mlb_market_readiness
from dk_ncaab.db.session import SessionLocal

DEFAULT_OUT_DIR = Path("artifacts/evidence_growth")
LATEST_FILENAME = "mlb_evidence_growth_latest.json"
JSONL_FILENAME = "mlb_evidence_growth.jsonl"


def _load_latest(out_dir: Path) -> dict[str, Any] | None:
    latest_path = out_dir / LATEST_FILENAME
    if not latest_path.exists():
        return None
    try:
        data = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def read_latest_mlb_evidence_growth(
    *,
    out_dir: str | Path = DEFAULT_OUT_DIR,
) -> dict[str, Any] | None:
    """Read the latest local MLB evidence growth snapshot, if present."""
    return _load_latest(Path(out_dir))


def _market_snapshot(row: Any) -> dict[str, Any]:
    return {
        "market": row.market,
        "label": row.label,
        "verdict": row.verdict,
        "current_quoted_rows": row.current_quoted_rows,
        "current_quoted_events": row.current_quoted_events,
        "settled_quoted_rows": row.settled_quoted_rows,
        "settled_quoted_events": row.settled_quoted_events,
        "oof_predicted_rows": row.oof_predicted_rows,
        "oof_recommended_rows": row.oof_recommended_rows,
        "participant_link_rate": row.participant_link_rate,
        "priority_score": row.priority_score,
        "next_action": row.next_action,
        "next_action_label": row.next_action_label,
        "next_action_command": row.next_action_command,
        "next_action_reason": row.next_action_reason,
        "gaps": list(row.gaps),
    }


def _delta(current: int, previous: dict[str, Any] | None, key: str) -> int:
    if not previous:
        return 0
    return int(current or 0) - int(previous.get(key) or 0)


def _market_growth(row: Any, previous_by_market: dict[str, dict[str, Any]]) -> dict[str, Any]:
    previous = previous_by_market.get(row.market)
    snapshot = _market_snapshot(row)
    snapshot.update(
        {
            "previous_verdict": previous.get("verdict") if previous else None,
            "verdict_changed": bool(previous and previous.get("verdict") != row.verdict),
            "current_quoted_rows_delta": _delta(
                row.current_quoted_rows,
                previous,
                "current_quoted_rows",
            ),
            "current_quoted_events_delta": _delta(
                row.current_quoted_events,
                previous,
                "current_quoted_events",
            ),
            "settled_quoted_rows_delta": _delta(
                row.settled_quoted_rows,
                previous,
                "settled_quoted_rows",
            ),
            "settled_quoted_events_delta": _delta(
                row.settled_quoted_events,
                previous,
                "settled_quoted_events",
            ),
            "oof_predicted_rows_delta": _delta(
                row.oof_predicted_rows,
                previous,
                "oof_predicted_rows",
            ),
        }
    )
    return snapshot


def _inventory_summary(inventory: dict[str, Any]) -> dict[str, Any]:
    line_history = inventory.get("line_history", {})
    mlb_stats = inventory.get("mlb_stats", {})
    statcast = inventory.get("statcast", {})
    environment = inventory.get("environment", {})
    return {
        "events_total": inventory.get("events", {}).get("total", 0),
        "events_final": inventory.get("events", {}).get("final", 0),
        "core_quotes": line_history.get("odds_quotes", 0),
        "core_pregame_events": line_history.get("draftkings_pregame_events", 0),
        "settled_core_pregame_events": line_history.get("settled_draftkings_pregame_events", 0),
        "event_specific_quotes": line_history.get("event_specific_quotes", 0),
        "event_specific_pregame_events": line_history.get("event_specific_pregame_events", 0),
        "event_specific_quotes_by_market": line_history.get("event_specific_quotes_by_market", {}),
        "unlinked_event_specific_player_quotes": line_history.get(
            "unlinked_event_specific_player_quotes",
            0,
        ),
        "unlinked_event_specific_team_quotes": line_history.get(
            "unlinked_event_specific_team_quotes",
            0,
        ),
        "team_logs": mlb_stats.get("team_logs", 0),
        "player_logs": mlb_stats.get("player_logs", 0),
        "statcast_daily_rows": statcast.get("daily_rows", 0),
        "park_factors": environment.get("park_factors", 0),
    }


def build_mlb_evidence_growth_log(
    *,
    session: Session | None = None,
    out_dir: str | Path = DEFAULT_OUT_DIR,
    label: str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Build and optionally append a local MLB evidence growth snapshot."""
    own_session = session is None
    session = session or SessionLocal()
    out_path = Path(out_dir)
    previous = _load_latest(out_path)
    previous_by_market = {
        str(row.get("market")): row
        for row in (previous or {}).get("markets", [])
        if isinstance(row, dict) and row.get("market")
    }
    try:
        generated_at = datetime.now(timezone.utc).isoformat()
        readiness = build_mlb_market_readiness(session)
        inventory = build_mlb_data_inventory(session=session, out_dir=None).summary
        markets = [
            _market_growth(row, previous_by_market)
            for row in sorted(readiness.markets, key=lambda item: item.market)
        ]
        inventory_summary = _inventory_summary(inventory)
        warnings = list(readiness.warnings)
        unlinked_player_quotes = int(
            inventory_summary.get("unlinked_event_specific_player_quotes") or 0
        )
        unlinked_team_quotes = int(inventory_summary.get("unlinked_event_specific_team_quotes") or 0)
        if unlinked_player_quotes:
            warnings.append(
                f"{unlinked_player_quotes} event-specific player quotes are not linked to "
                "local player identities."
            )
        if unlinked_team_quotes:
            warnings.append(
                f"{unlinked_team_quotes} event-specific team quotes are not linked to "
                "local team identities."
            )
        priority = sorted(
            [
                row
                for row in markets
                if row.get("next_action") and row.get("next_action") != "ready_for_review"
            ],
            key=lambda row: int(row.get("priority_score") or 0),
            reverse=True,
        )
        result = {
            "generated_at_utc": generated_at,
            "label": label,
            "previous_generated_at_utc": (previous or {}).get("generated_at_utc"),
            "summary": {
                **inventory_summary,
                "markets_ready": readiness.summary.markets_ready,
                "markets_thin": readiness.summary.markets_thin,
                "markets_collect_more": readiness.summary.markets_collect_more,
                "markets_missing_data": readiness.summary.markets_missing_data,
                "total_oof_predicted_rows": readiness.summary.total_oof_predicted_rows,
                "top_next_action": priority[0]["next_action"] if priority else "ready_for_review",
                "top_next_action_label": (
                    priority[0]["next_action_label"] if priority else "Ready for review"
                ),
            },
            "priority_markets": priority[:5],
            "markets": markets,
            "warnings": warnings,
        }
        if write:
            out_path.mkdir(parents=True, exist_ok=True)
            latest_path = out_path / LATEST_FILENAME
            jsonl_path = out_path / JSONL_FILENAME
            latest_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
            with jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result, sort_keys=True) + "\n")
            result["latest_path"] = str(latest_path)
            result["jsonl_path"] = str(jsonl_path)
        return result
    finally:
        if own_session:
            session.close()
