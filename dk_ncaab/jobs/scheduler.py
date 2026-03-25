"""
Job scheduler – orchestrates collectors and nightly builds.

Polling cadence adapts based on proximity to tip:
  - Odds: 5 min baseline → 90s inside 90 min → 60s inside 30 min
  - Splits: 30 min baseline → 10 min inside 90 min
  - Results: every 5 min (constant)
  - Nightly dataset build: 3:00 AM UTC

Uses APScheduler with a BlockingScheduler for simplicity.
Run as: python -m dk_ncaab.jobs.scheduler
"""

from __future__ import annotations

import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from dk_ncaab.config.settings import get_settings
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event
from dk_ncaab.collectors.odds_api import collect_odds
from dk_ncaab.collectors.results import collect_results
from dk_ncaab.collectors.splits_dknetwork import collect_splits
from dk_ncaab.analysis.dataset_build import run_dataset_build

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)


# ── Adaptive polling wrappers ───────────────────────────────────

def _minutes_to_nearest_tip() -> float | None:
    """How many minutes until the closest upcoming event tips off?"""
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        stmt = (
            select(Event.start_time_utc)
            .where(Event.status == "upcoming", Event.start_time_utc > now)
            .order_by(Event.start_time_utc.asc())
            .limit(1)
        )
        row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        return None
    return (row - now).total_seconds() / 60


def _adaptive_odds_interval() -> int:
    """Return current odds polling interval in seconds."""
    cfg = get_settings().polling
    mins = _minutes_to_nearest_tip()
    if mins is None:
        return cfg.odds_baseline_sec
    if mins <= 30:
        return cfg.odds_pre30_sec
    if mins <= 90:
        return cfg.odds_pre90_sec
    return cfg.odds_baseline_sec


def odds_job() -> None:
    """Wrapper that logs the adaptive interval."""
    interval = _adaptive_odds_interval()
    log.info("Odds poll (next in %ds)", interval)
    collect_odds()


def splits_job() -> None:
    """Splits collection with logging."""
    mins = _minutes_to_nearest_tip()
    log.info("Splits poll (nearest tip: %s min)", f"{mins:.0f}" if mins else "none")
    collect_splits()


# ── Staleness monitoring ────────────────────────────────────────

def staleness_check() -> None:
    """Warn if no odds rows were inserted in the last 10 minutes."""
    from dk_ncaab.db.models import OddsQuote
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    with SessionLocal() as session:
        recent = session.query(OddsQuote).filter(
            OddsQuote.collected_at_utc >= cutoff
        ).count()
    if recent == 0:
        log.critical("STALENESS ALERT: No odds rows in the last 10 minutes!")


# ── Main scheduler ──────────────────────────────────────────────

def create_scheduler() -> BlockingScheduler:
    cfg = get_settings().polling
    scheduler = BlockingScheduler(timezone="UTC")

    # Odds: poll at baseline interval (adaptive wrapper adjusts internally)
    scheduler.add_job(
        odds_job,
        IntervalTrigger(seconds=cfg.odds_pre90_sec),  # check frequently, wrapper decides
        id="odds_collector",
        name="Odds Collector",
        max_instances=1,
        coalesce=True,
    )

    # Splits: baseline interval
    scheduler.add_job(
        splits_job,
        IntervalTrigger(seconds=cfg.splits_pre90_sec),
        id="splits_collector",
        name="Splits Collector",
        max_instances=1,
        coalesce=True,
    )

    # Results: constant interval
    scheduler.add_job(
        collect_results,
        IntervalTrigger(seconds=cfg.results_sec),
        id="results_collector",
        name="Results Collector",
        max_instances=1,
        coalesce=True,
    )

    # Nightly dataset build at 3:00 AM UTC
    scheduler.add_job(
        run_dataset_build,
        CronTrigger(hour=3, minute=0),
        id="nightly_build",
        name="Nightly Dataset Build",
    )

    # Staleness check every 5 minutes
    scheduler.add_job(
        staleness_check,
        IntervalTrigger(minutes=5),
        id="staleness_check",
        name="Staleness Monitor",
    )

    return scheduler


def main() -> None:
    """Entry point: start the scheduler with graceful shutdown."""
    log.info("Starting DK NCAAB scheduler")
    scheduler = create_scheduler()

    def shutdown(signum, frame):
        log.info("Shutdown signal received, stopping scheduler")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")


if __name__ == "__main__":
    main()
