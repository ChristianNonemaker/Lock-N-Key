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

from dk_ncaab.config.settings import get_settings
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event, EventResult, OddsQuote
from dk_ncaab.etl.features import build_features, FeatureRow

log = logging.getLogger(__name__)

# Markets × sides to build features for
_COMBOS = [
    ("moneyline", "home"),
    ("moneyline", "away"),
    ("spread", "home"),
    ("spread", "away"),
    ("total", "over"),
    ("total", "under"),
]


def _get_finished_event_ids(session: Session) -> list[int]:
    """Return event IDs that have results and at least one odds quote."""
    stmt = (
        select(Event.id)
        .join(EventResult, EventResult.event_id == Event.id)
        .where(Event.status == "final")
    )
    return [r[0] for r in session.execute(stmt)]


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

        log.info("Building features for %d events × %d combos", len(event_ids), len(_COMBOS))

        rows: list[dict] = []
        for eid in event_ids:
            for market, side in _COMBOS:
                try:
                    fr = build_features(session, eid, market, side)
                    rows.append(fr.to_dict())
                except Exception as e:
                    log.warning("Feature build failed event=%d %s/%s: %s", eid, market, side, e)

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
