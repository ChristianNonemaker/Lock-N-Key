"""Bounded MLB Stats API backfill orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from dk_ncaab.collectors.mlb_stats import collect_mlb_stats


@dataclass(frozen=True)
class MlbStatsBackfillWindow:
    start_date: date
    end_date: date
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class MlbStatsBackfillResult:
    windows: list[MlbStatsBackfillWindow]
    totals: dict[str, int]
    dry_run: bool


def _default_start(today: date) -> date:
    # Early April keeps the default focused on the current MLB regular-season shape
    # without unexpectedly replaying a full historical archive.
    return date(today.year, 4, 1)


def _windows(start_date: date, end_date: date, window_days: int) -> list[tuple[date, date]]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    out: list[tuple[date, date]] = []
    current = start_date
    while current <= end_date:
        window_end = min(current + timedelta(days=window_days - 1), end_date)
        out.append((current, window_end))
        current = window_end + timedelta(days=1)
    return out


def backfill_current_mlb_stats(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    window_days: int = 3,
    max_boxscores_per_window: int | None = None,
    request_delay_sec: float | None = None,
    final_only: bool = True,
    skip_existing_boxscores: bool = True,
    dry_run: bool = False,
) -> MlbStatsBackfillResult:
    """Backfill current-season MLB Stats API data in small restartable windows."""
    today = datetime.now(timezone.utc).date()
    start_date = start_date or _default_start(today)
    end_date = end_date or today
    windows: list[MlbStatsBackfillWindow] = []
    totals: dict[str, int] = {}

    for win_start, win_end in _windows(start_date, end_date, window_days):
        if dry_run:
            windows.append(MlbStatsBackfillWindow(start_date=win_start, end_date=win_end))
            continue
        result = collect_mlb_stats(
            start_date=win_start,
            end_date=win_end,
            final_only=final_only,
            max_boxscores=max_boxscores_per_window,
            request_delay_sec=request_delay_sec,
            skip_existing_boxscores=skip_existing_boxscores,
        )
        result_dict = asdict(result)
        for key, value in result_dict.items():
            totals[key] = totals.get(key, 0) + int(value or 0)
        windows.append(
            MlbStatsBackfillWindow(
                start_date=win_start,
                end_date=win_end,
                result=result_dict,
            )
        )

    return MlbStatsBackfillResult(windows=windows, totals=totals, dry_run=dry_run)
