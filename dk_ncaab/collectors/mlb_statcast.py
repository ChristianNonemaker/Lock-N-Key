"""Import Baseball Savant / Statcast CSV rows into daily MLB feature tables."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from sqlalchemy import select
from sqlalchemy.orm import Session

from dk_ncaab.db.models import MlbStatcastDaily, Player
from dk_ncaab.db.session import SessionLocal


@dataclass(frozen=True)
class MlbStatcastImportResult:
    rows_seen: int
    daily_rows_upserted: int
    linked_to_local_players: int
    source: str


@dataclass(frozen=True)
class MlbStatcastBackfillWindow:
    start_date: date
    end_date: date
    csv_path: str
    source_url: str
    downloaded: bool
    result: MlbStatcastImportResult | None = None


@dataclass(frozen=True)
class MlbStatcastBackfillResult:
    windows: list[MlbStatcastBackfillWindow]
    totals: dict[str, int]
    dry_run: bool


_HIT_EVENTS = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
_STRIKEOUT_EVENTS = {"strikeout", "strikeout_double_play"}
_WALK_EVENTS = {"walk", "intent_walk"}
_NON_AB_EVENTS = _WALK_EVENTS | {
    "hit_by_pitch",
    "sac_bunt",
    "sac_fly",
    "catcher_interf",
    "sac_fly_double_play",
    "sac_bunt_double_play",
}
_SWING_DESCRIPTIONS = {
    "swinging_strike",
    "swinging_strike_blocked",
    "foul",
    "foul_tip",
    "foul_bunt",
    "missed_bunt",
    "hit_into_play",
    "hit_into_play_no_out",
    "hit_into_play_score",
}
_WHIFF_DESCRIPTIONS = {"swinging_strike", "swinging_strike_blocked", "missed_bunt"}
_CSW_DESCRIPTIONS = _WHIFF_DESCRIPTIONS | {"called_strike"}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _float(value: Any) -> float | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date(value: Any) -> datetime | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _mean(values: list[float]) -> float | None:
    return float(sum(values) / len(values)) if values else None


def _rate(num: int, den: int) -> float | None:
    return float(num / den) if den else None


def _windows(start_date: date, end_date: date, window_days: int) -> list[tuple[date, date]]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    windows: list[tuple[date, date]] = []
    current = start_date
    while current <= end_date:
        window_end = min(current + timedelta(days=window_days - 1), end_date)
        windows.append((current, window_end))
        current = window_end + timedelta(days=1)
    return windows


def _statcast_search_csv_url(start_date: date, end_date: date) -> str:
    season = f"{start_date.year}|"
    params = {
        "all": "true",
        "hfPT": "",
        "hfAB": "",
        "hfGT": "R|",
        "hfPR": "",
        "hfZ": "",
        "stadium": "",
        "hfBBL": "",
        "hfNewZones": "",
        "hfPull": "",
        "hfC": "",
        "hfSea": season if start_date.year == end_date.year else "",
        "hfSit": "",
        "player_type": "batter",
        "hfOuts": "",
        "opponent": "",
        "pitcher_throws": "",
        "batter_stands": "",
        "hfSA": "",
        "game_date_gt": start_date.isoformat(),
        "game_date_lt": end_date.isoformat(),
        "hfMo": "",
        "hfTeam": "",
        "home_road": "",
        "hfRO": "",
        "position": "",
        "hfInfield": "",
        "hfOutfield": "",
        "hfInn": "",
        "hfBBT": "",
        "hfFlag": "",
        "metric_1": "",
        "group_by": "name",
        "min_pitches": "0",
        "min_results": "0",
        "min_pas": "0",
        "type": "details",
    }
    return "https://baseballsavant.mlb.com/statcast_search/csv?" + urlencode(params)


def _statcast_csv_path(out_dir: str | Path, start_date: date, end_date: date) -> Path:
    return Path(out_dir) / f"statcast_{start_date.isoformat()}_{end_date.isoformat()}.csv"


def _looks_like_statcast_csv(content: bytes) -> bool:
    head = content[:512].decode("utf-8-sig", errors="ignore").lower()
    return "game_date" in head and "batter" in head and "pitcher" in head


def _local_player(session: Session, key_mlbam: str) -> Player | None:
    return session.execute(
        select(Player).where(
            Player.provider == "mlb_stats_api",
            Player.external_player_key == key_mlbam,
        )
    ).scalar_one_or_none()


def _new_bucket() -> dict[str, Any]:
    return {
        "pitches": 0,
        "plate_appearances": 0,
        "at_bats": 0,
        "hits": 0,
        "total_bases": 0,
        "home_runs": 0,
        "strikeouts": 0,
        "walks": 0,
        "batted_balls": 0,
        "exit_velocities": [],
        "hard_hit_balls": 0,
        "barrels": 0,
        "xba_values": [],
        "xslg_values": [],
        "xwoba_values": [],
        "swings": 0,
        "whiffs": 0,
        "called_or_whiff": 0,
        "player_name": None,
    }


def _add_pitch(bucket: dict[str, Any], row: dict[str, Any]) -> None:
    bucket["pitches"] += 1
    event = (_clean(row.get("events")) or "").lower()
    if event:
        bucket["plate_appearances"] += 1
        if event not in _NON_AB_EVENTS:
            bucket["at_bats"] += 1
        if event in _HIT_EVENTS:
            bucket["hits"] += 1
            bucket["total_bases"] += _HIT_EVENTS[event]
        bucket["home_runs"] += int(event == "home_run")
        bucket["strikeouts"] += int(event in _STRIKEOUT_EVENTS)
        bucket["walks"] += int(event in _WALK_EVENTS)

    description = (_clean(row.get("description")) or "").lower()
    if description in _SWING_DESCRIPTIONS:
        bucket["swings"] += 1
    if description in _WHIFF_DESCRIPTIONS:
        bucket["whiffs"] += 1
    if description in _CSW_DESCRIPTIONS:
        bucket["called_or_whiff"] += 1

    launch_speed = _float(row.get("launch_speed"))
    if launch_speed is not None:
        bucket["batted_balls"] += 1
        bucket["exit_velocities"].append(launch_speed)
        bucket["hard_hit_balls"] += int(launch_speed >= 95.0)
    launch_speed_angle = _clean(row.get("launch_speed_angle"))
    bucket["barrels"] += int(launch_speed_angle == "6")
    for source_col, bucket_key in (
        ("estimated_ba_using_speedangle", "xba_values"),
        ("estimated_slg_using_speedangle", "xslg_values"),
        ("estimated_woba_using_speedangle", "xwoba_values"),
    ):
        value = _float(row.get(source_col))
        if value is not None:
            bucket[bucket_key].append(value)


def _bucket_to_values(bucket: dict[str, Any]) -> dict[str, Any]:
    batted_balls = bucket["batted_balls"]
    return {
        "pitches": bucket["pitches"],
        "plate_appearances": bucket["plate_appearances"],
        "at_bats": bucket["at_bats"],
        "hits": bucket["hits"],
        "total_bases": bucket["total_bases"],
        "home_runs": bucket["home_runs"],
        "strikeouts": bucket["strikeouts"],
        "walks": bucket["walks"],
        "batted_balls": batted_balls,
        "avg_exit_velocity": _mean(bucket["exit_velocities"]),
        "hard_hit_rate": _rate(bucket["hard_hit_balls"], batted_balls),
        "barrel_rate": _rate(bucket["barrels"], batted_balls),
        "xba_mean": _mean(bucket["xba_values"]),
        "xslg_mean": _mean(bucket["xslg_values"]),
        "xwoba_mean": _mean(bucket["xwoba_values"]),
        "whiff_rate": _rate(bucket["whiffs"], bucket["swings"]),
        "csw_rate": _rate(bucket["called_or_whiff"], bucket["pitches"]),
    }


def _player_name(row: dict[str, Any], player_type: str) -> str | None:
    if player_type == "pitcher":
        return _clean(row.get("player_name")) or _clean(row.get("pitcher_name"))
    return _clean(row.get("batter_name")) or _clean(row.get("player_name"))


def import_statcast_daily_csv(
    csv_path: str | Path,
    *,
    source: str = "baseball_savant_csv",
    source_url: str | None = None,
    session: Session | None = None,
) -> MlbStatcastImportResult:
    """Aggregate raw Statcast pitch rows to daily batter and pitcher feature rows."""
    own_session = session is None
    session = session or SessionLocal()
    rows_seen = 0
    imported_at = datetime.now(timezone.utc)
    buckets: dict[tuple[str, datetime, str], dict[str, Any]] = defaultdict(_new_bucket)

    try:
        with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows_seen += 1
                game_date = _date(row.get("game_date"))
                if game_date is None:
                    continue
                for player_type, id_col in (("batter", "batter"), ("pitcher", "pitcher")):
                    key_mlbam = _clean(row.get(id_col))
                    if key_mlbam is None:
                        continue
                    bucket = buckets[(key_mlbam, game_date, player_type)]
                    bucket["player_name"] = bucket["player_name"] or _player_name(row, player_type)
                    _add_pitch(bucket, row)

        rows_upserted = 0
        linked = 0
        for (key_mlbam, game_date, player_type), bucket in buckets.items():
            player = _local_player(session, key_mlbam)
            existing = session.execute(
                select(MlbStatcastDaily).where(
                    MlbStatcastDaily.key_mlbam == key_mlbam,
                    MlbStatcastDaily.game_date_utc == game_date,
                    MlbStatcastDaily.player_type == player_type,
                    MlbStatcastDaily.source == source,
                )
            ).scalar_one_or_none()
            if existing is None:
                existing = MlbStatcastDaily(
                    key_mlbam=key_mlbam,
                    game_date_utc=game_date,
                    player_type=player_type,
                    source=source,
                )
                session.add(existing)
            existing.player_id = player.id if player else None
            existing.player_name = bucket["player_name"]
            existing.source_url = source_url
            existing.imported_at_utc = imported_at
            for key, value in _bucket_to_values(bucket).items():
                setattr(existing, key, value)
            rows_upserted += 1
            linked += int(player is not None)

        session.commit()
    finally:
        if own_session:
            session.close()

    return MlbStatcastImportResult(
        rows_seen=rows_seen,
        daily_rows_upserted=rows_upserted,
        linked_to_local_players=linked,
        source=source,
    )


def download_statcast_csv(
    start_date: date,
    end_date: date,
    *,
    out_dir: str | Path = "artifacts/raw/mlb/statcast",
    timeout_sec: float = 120.0,
    skip_existing: bool = True,
) -> tuple[Path, str, bool]:
    """Download one bounded Baseball Savant Statcast CSV window."""
    csv_path = _statcast_csv_path(out_dir, start_date, end_date)
    source_url = _statcast_search_csv_url(start_date, end_date)
    if skip_existing and csv_path.exists() and csv_path.stat().st_size > 0:
        return csv_path, source_url, False

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=timeout_sec, follow_redirects=True) as client:
        response = client.get(source_url)
        response.raise_for_status()
    content = response.content
    if not _looks_like_statcast_csv(content):
        preview = content[:160].decode("utf-8", errors="replace").replace("\n", " ")
        raise ValueError(f"Baseball Savant response did not look like Statcast CSV: {preview}")
    csv_path.write_bytes(content)
    return csv_path, source_url, True


def backfill_statcast_daily(
    *,
    start_date: date,
    end_date: date,
    window_days: int = 1,
    out_dir: str | Path = "artifacts/raw/mlb/statcast",
    request_delay_sec: float | None = None,
    skip_existing_downloads: bool = True,
    dry_run: bool = False,
) -> MlbStatcastBackfillResult:
    """Download and import Baseball Savant pitch rows in restartable windows."""
    windows: list[MlbStatcastBackfillWindow] = []
    totals = {
        "rows_seen": 0,
        "daily_rows_upserted": 0,
        "linked_to_local_players": 0,
        "downloads": 0,
    }

    for index, (win_start, win_end) in enumerate(_windows(start_date, end_date, window_days)):
        csv_path = _statcast_csv_path(out_dir, win_start, win_end)
        source_url = _statcast_search_csv_url(win_start, win_end)
        if dry_run:
            windows.append(
                MlbStatcastBackfillWindow(
                    start_date=win_start,
                    end_date=win_end,
                    csv_path=str(csv_path),
                    source_url=source_url,
                    downloaded=False,
                )
            )
            continue

        if index and request_delay_sec:
            import time

            time.sleep(request_delay_sec)
        csv_path, source_url, downloaded = download_statcast_csv(
            win_start,
            win_end,
            out_dir=out_dir,
            skip_existing=skip_existing_downloads,
        )
        result = import_statcast_daily_csv(csv_path, source_url=source_url)
        totals["rows_seen"] += result.rows_seen
        totals["daily_rows_upserted"] += result.daily_rows_upserted
        totals["linked_to_local_players"] += result.linked_to_local_players
        totals["downloads"] += int(downloaded)
        windows.append(
            MlbStatcastBackfillWindow(
                start_date=win_start,
                end_date=win_end,
                csv_path=str(csv_path),
                source_url=source_url,
                downloaded=downloaded,
                result=result,
            )
        )

    return MlbStatcastBackfillResult(windows=windows, totals=totals, dry_run=dry_run)
