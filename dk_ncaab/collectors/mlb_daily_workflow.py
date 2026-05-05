"""Quota-aware MLB daily research workflow planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class MlbDailyWorkflowStep:
    name: str
    command: str
    quota: str
    note: str


def build_mlb_daily_research_steps(
    *,
    slate_date: date,
    settled_start_date: date | None = None,
    settled_end_date: date | None = None,
    statcast_start_date: date | None = None,
    statcast_end_date: date | None = None,
    event_odds_max_events: int = 1,
    include_event_odds: bool = False,
) -> list[MlbDailyWorkflowStep]:
    """Return the bounded local MLB workflow without executing provider calls."""
    settled_end = settled_end_date or max(slate_date - timedelta(days=1), slate_date)
    settled_start = settled_start_date or max(settled_end - timedelta(days=2), date(settled_end.year, 4, 1))
    statcast_end = statcast_end_date or settled_end
    statcast_start = statcast_start_date or settled_start

    steps = [
        MlbDailyWorkflowStep(
            name="Load MLB slate",
            command=f"python -m dk_ncaab load-games --sport baseball_mlb --date {slate_date.isoformat()}",
            quota="free",
            note="Seeds or refreshes today/upcoming ESPN schedule rows.",
        ),
        MlbDailyWorkflowStep(
            name="Settle recent games",
            command="python -m dk_ncaab update-results --sport baseball_mlb",
            quota="free",
            note="Updates pending ESPN results before feature rebuilds.",
        ),
        MlbDailyWorkflowStep(
            name="Backfill MLB logs",
            command=(
                "python -m dk_ncaab backfill-mlb-current-season "
                f"--start-date {settled_start.isoformat()} --end-date {settled_end.isoformat()} "
                "--window-days 1"
            ),
            quota="free",
            note="Fills team, player, and probable-starter logs for newly final dates.",
        ),
        MlbDailyWorkflowStep(
            name="Backfill Statcast daily",
            command=(
                "python -m dk_ncaab backfill-mlb-statcast-daily "
                f"--start-date {statcast_start.isoformat()} --end-date {statcast_end.isoformat()} "
                "--window-days 1"
            ),
            quota="free",
            note="Refreshes pitcher/batter context for props and starter form.",
        ),
        MlbDailyWorkflowStep(
            name="Collect MLB environment",
            command="python -m dk_ncaab collect-mlb-environment --max-events 12",
            quota="free",
            note="Adds bounded NWS/venue context for upcoming MLB games.",
        ),
    ]
    if include_event_odds:
        steps.append(
            MlbDailyWorkflowStep(
                name="Collect event-specific odds",
                command=(
                    "python -m dk_ncaab collect-event-odds --sport baseball_mlb "
                    f"--max-events {event_odds_max_events} "
                    "--markets team_totals,pitcher_strikeouts,batter_total_bases"
                ),
                quota="odds_api",
                note="Explicit opt-in only; use for thin markets before broad polling.",
            )
        )
    else:
        steps.append(
            MlbDailyWorkflowStep(
                name="Event-specific odds skipped",
                command=(
                    "python -m dk_ncaab collect-event-odds --sport baseball_mlb "
                    f"--max-events {event_odds_max_events} "
                    "--markets team_totals,pitcher_strikeouts,batter_total_bases"
                ),
                quota="odds_api_skipped",
                note="Add --include-event-odds to include this quota-spending step in the runbook.",
            )
        )

    steps.extend(
        [
            MlbDailyWorkflowStep(
                name="Rebuild feature parquet",
                command="python -m dk_ncaab build-dataset",
                quota="local",
                note="Exports current local DB features for strict EV validation.",
            ),
            MlbDailyWorkflowStep(
                name="Run strict entry EV",
                command="python -m dk_ncaab oof-entry-ev --sport baseball_mlb --anchor T60",
                quota="local",
                note="Builds OOF predictions with entry prices and settlement math.",
            ),
            MlbDailyWorkflowStep(
                name="Refresh MLB inventory",
                command="python -m dk_ncaab mlb-data-inventory",
                quota="local",
                note="Updates table counts, date ranges, line history, and join gaps.",
            ),
            MlbDailyWorkflowStep(
                name="Append evidence growth log",
                command="python -m dk_ncaab mlb-evidence-growth-log --label daily-research-cycle",
                quota="local",
                note="Records readiness deltas, OOF row growth, and next collection actions.",
            ),
        ]
    )
    return steps


def format_mlb_daily_research_steps(steps: list[MlbDailyWorkflowStep]) -> str:
    lines = ["MLB daily research workflow:"]
    for idx, step in enumerate(steps, start=1):
        lines.append(f"{idx}. {step.name} [{step.quota}]")
        lines.append(f"   {step.command}")
        lines.append(f"   {step.note}")
    return "\n".join(lines)
