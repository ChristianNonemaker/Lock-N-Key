"""
Dataset builder – joins snapshots, features, and results into
a flat analytical table exported to Parquet.

Run nightly after all collectors have finished.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from dk_ncaab.config.props import prop_market_specs_for_sport
from dk_ncaab.config.settings import get_settings
from dk_ncaab.db.models import Event, EventOddsQuote, EventResult
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.etl.features import build_features

log = logging.getLogger(__name__)

# Markets × sides to build features for
_CORE_COMBOS = [
    ("moneyline", "home"),
    ("moneyline", "away"),
    ("spread", "home"),
    ("spread", "away"),
    ("total", "over"),
    ("total", "under"),
]
_EVENT_MARKET_KEYS = tuple(
    spec.market_key
    for spec in prop_market_specs_for_sport("baseball_mlb", collection_enabled_only=True)
)
_SIDE_ORDER = {"over": 0, "under": 1, "yes": 2, "no": 3}


def _get_finished_event_ids(session: Session) -> list[int]:
    """Return event IDs that have results and at least one odds quote."""
    stmt = (
        select(Event.id)
        .join(EventResult, EventResult.event_id == Event.id)
        .where(Event.status == "final")
    )
    return [r[0] for r in session.execute(stmt)]


def _event_specific_feature_inputs(session: Session, event_id: int) -> list[dict[str, object]]:
    """Return participant-specific event-market rows that have pre-tip quote history."""
    event = session.get(Event, event_id)
    if not event or not _EVENT_MARKET_KEYS:
        return []

    rows = session.execute(
        select(
            EventOddsQuote.market_key,
            EventOddsQuote.side,
            EventOddsQuote.entity_type,
            EventOddsQuote.team_id,
            EventOddsQuote.player_id,
            EventOddsQuote.participant_name,
        )
        .where(EventOddsQuote.event_id == event_id)
        .where(EventOddsQuote.book == "draftkings")
        .where(EventOddsQuote.market_key.in_(_EVENT_MARKET_KEYS))
        .where(EventOddsQuote.collected_at_utc < event.start_time_utc)
        .order_by(
            EventOddsQuote.market_key.asc(),
            EventOddsQuote.entity_type.asc(),
            EventOddsQuote.participant_name.asc(),
            EventOddsQuote.side.asc(),
        )
    ).all()

    grouped: dict[tuple[str, str, int | None, int | None, str], set[str]] = {}
    for market, side, entity_type, team_id, player_id, participant_name in rows:
        key = (market, entity_type, team_id, player_id, participant_name)
        grouped.setdefault(key, set()).add(side)

    inputs: list[dict[str, object]] = []
    for (market, entity_type, team_id, player_id, participant_name), sides in grouped.items():
        for side in sorted(sides, key=lambda value: _SIDE_ORDER.get(value, 99)):
            inputs.append(
                {
                    "market": market,
                    "side": side,
                    "participant_name": participant_name,
                    "participant_entity_type": entity_type,
                    "participant_team_id": team_id,
                    "participant_player_id": player_id,
                }
            )
    return inputs


def build_dataset(
    session: Session | None = None,
    event_ids: list[int] | None = None,
) -> pd.DataFrame:
    """
    Build the full analytical DataFrame.
    If event_ids is None, use all completed events.
    """
    own_session = session is None
    if own_session:
        session = SessionLocal()

    try:
        if event_ids is None:
            event_ids = _get_finished_event_ids(session)

        log.info(
            "Building core features for %d events x %d combos",
            len(event_ids),
            len(_CORE_COMBOS),
        )

        rows: list[dict] = []
        for eid in event_ids:
            event_inputs: list[dict[str, object]] = [
                {"market": market, "side": side}
                for market, side in _CORE_COMBOS
            ]
            event_inputs.extend(_event_specific_feature_inputs(session, eid))
            for feature_input in event_inputs:
                market = str(feature_input["market"])
                side = str(feature_input["side"])
                participant_name = feature_input.get("participant_name")
                participant_entity_type = feature_input.get("participant_entity_type")
                participant_team_id = feature_input.get("participant_team_id")
                participant_player_id = feature_input.get("participant_player_id")
                try:
                    fr = build_features(
                        session,
                        eid,
                        market,
                        side,
                        participant_name=participant_name
                        if isinstance(participant_name, str)
                        else None,
                        participant_entity_type=participant_entity_type
                        if isinstance(participant_entity_type, str)
                        else None,
                        participant_team_id=participant_team_id
                        if isinstance(participant_team_id, int)
                        else None,
                        participant_player_id=participant_player_id
                        if isinstance(participant_player_id, int)
                        else None,
                    )
                    rows.append(fr.to_dict())
                except Exception as e:
                    participant = feature_input.get("participant_name") or "-"
                    log.warning(
                        "Feature build failed event=%d %s/%s participant=%s: %s",
                        eid,
                        market,
                        side,
                        participant,
                        e,
                    )

        df = pd.DataFrame(rows)
        log.info("Dataset built: %d rows × %d cols", len(df), len(df.columns))
        return df

    finally:
        if own_session:
            session.close()


def export_parquet(df: pd.DataFrame, tag: str | None = None) -> Path:
    """Write dataset to Parquet in artifacts dir."""
    cfg = get_settings().storage
    out_dir = Path(cfg.parquet_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = tag or datetime.now(timezone.utc).strftime("%Y%m%d")
    path = out_dir / f"features_{ts}.parquet"
    df.to_parquet(path, index=False, engine="pyarrow")
    log.info("Exported %s (%d rows)", path, len(df))
    return path


def run_dataset_build() -> None:
    """Convenience entrypoint for the scheduler."""
    df = build_dataset()
    if not df.empty:
        export_parquet(df)
