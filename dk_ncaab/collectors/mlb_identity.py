"""MLB player identity imports for Chadwick-style public crosswalk files."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from dk_ncaab.db.models import MlbPlayerIdCrosswalk, Player
from dk_ncaab.db.session import SessionLocal


@dataclass(frozen=True)
class MlbPlayerIdImportResult:
    rows_seen: int
    rows_upserted: int
    linked_to_local_players: int
    source: str
    files_imported: int = 1


_COLUMN_ALIASES = {
    "key_mlbam": ("key_mlbam", "mlbam_id", "mlbam", "mlb_id", "mlb_person_id"),
    "key_retro": ("key_retro", "retrosheet_id", "retro_id", "key_retro"),
    "key_bbref": ("key_bbref", "bbref_id", "baseball_reference_id"),
    "key_fangraphs": ("key_fangraphs", "fangraphs_id", "fg_id"),
    "name_first": ("name_first", "first_name", "first"),
    "name_last": ("name_last", "last_name", "last"),
    "name_full": ("name_full", "full_name", "name"),
    "mlb_played_first": ("mlb_played_first", "mlb_first", "played_first"),
    "mlb_played_last": ("mlb_played_last", "mlb_last", "played_last"),
}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _int(value: Any) -> int | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _get(row: dict[str, Any], canonical: str) -> str | None:
    lowered = {key.lower(): value for key, value in row.items()}
    for alias in _COLUMN_ALIASES[canonical]:
        if alias.lower() in lowered:
            return _clean(lowered[alias.lower()])
    return None


def _source_row_key(values: dict[str, Any]) -> str:
    keys = [
        values.get("key_mlbam"),
        values.get("key_retro"),
        values.get("key_bbref"),
        values.get("key_fangraphs"),
        values.get("name_full"),
    ]
    return "|".join(str(v) for v in keys if v) or "unknown"


def _local_player_for_mlbam(session: Session, key_mlbam: str | None) -> Player | None:
    if not key_mlbam:
        return None
    return session.execute(
        select(Player).where(
            Player.provider == "mlb_stats_api",
            Player.external_player_key == key_mlbam,
        )
    ).scalar_one_or_none()


def _csv_paths(path: str | Path) -> list[Path]:
    root = Path(path)
    if root.is_file():
        return [root]
    if root.is_dir():
        files = sorted(root.glob("people-*.csv"))
        if not files:
            files = sorted(root.glob("*.csv"))
        return files
    raise FileNotFoundError(f"MLB player ID path does not exist: {root}")


def _iter_rows(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            yield from csv.DictReader(handle)


def import_chadwick_player_ids_csv(
    csv_path: str | Path,
    *,
    source: str = "chadwick_register_csv",
    session: Session | None = None,
) -> MlbPlayerIdImportResult:
    """Import a Chadwick-style ID crosswalk CSV and link rows to local MLBAM players."""
    own_session = session is None
    session = session or SessionLocal()
    rows_seen = 0
    rows_upserted = 0
    linked = 0
    imported_at = datetime.now(timezone.utc)

    try:
        paths = _csv_paths(csv_path)
        for raw in _iter_rows(paths):
            rows_seen += 1
            name_first = _get(raw, "name_first")
            name_last = _get(raw, "name_last")
            name_full = _get(raw, "name_full") or " ".join(
                part for part in [name_first, name_last] if part
            ) or None
            values = {
                "key_mlbam": _get(raw, "key_mlbam"),
                "key_retro": _get(raw, "key_retro"),
                "key_bbref": _get(raw, "key_bbref"),
                "key_fangraphs": _get(raw, "key_fangraphs"),
                "name_first": name_first,
                "name_last": name_last,
                "name_full": name_full,
                "mlb_played_first": _int(_get(raw, "mlb_played_first")),
                "mlb_played_last": _int(_get(raw, "mlb_played_last")),
            }
            if not any(
                values.get(key)
                for key in ("key_mlbam", "key_retro", "key_bbref", "key_fangraphs")
            ):
                continue
            row_key = _source_row_key(values)
            player = _local_player_for_mlbam(session, values["key_mlbam"])
            existing = session.execute(
                select(MlbPlayerIdCrosswalk).where(
                    MlbPlayerIdCrosswalk.source == source,
                    MlbPlayerIdCrosswalk.source_row_key == row_key,
                )
            ).scalar_one_or_none()
            if existing is None:
                existing = MlbPlayerIdCrosswalk(source=source, source_row_key=row_key)
                session.add(existing)
            existing.player_id = player.id if player else None
            existing.imported_at_utc = imported_at
            for key, value in values.items():
                setattr(existing, key, value)
            rows_upserted += 1
            linked += int(player is not None)

        session.commit()
    finally:
        if own_session:
            session.close()

    return MlbPlayerIdImportResult(
        rows_seen=rows_seen,
        rows_upserted=rows_upserted,
        linked_to_local_players=linked,
        source=source,
        files_imported=len(paths),
    )
