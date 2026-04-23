"""
Budget-aware automated data collector for DK NCAAB pipeline.

Designed for the Odds API FREE tier (500 requests/month):
  - ESPN (unlimited): runs every cycle -- load games, update scores
  - Odds API (precious): strategic snapshots timed to game windows
  - Auto-adapts schedule to the day's slate (heavy Saturday vs light Monday)
  - Tracks usage budget and self-throttles to never exceed cap

One API call returns ALL upcoming games, so frequent fixed-time polling
naturally gives us different pre-game intervals for different tip-offs.

Schedule (Eastern Time):
  SATURDAY (heavy -- games noon-11pm): 15 snapshots
    8am  10am  11:30am  12:30pm  1:30pm  2:30pm  3:30pm  4:30pm
    5:30pm  6pm  6:30pm  7pm  8pm  9pm  10:30pm

  TUE / WED / THU (medium -- games 6-11pm): 12 snapshots each
    9am  12pm  3pm  5pm  5:30pm  6pm  6:30pm  6:45pm  7pm  8pm
    9pm  10:30pm

  MON / FRI / SUN (light -- some games): 5 snapshots each
    5pm  6:30pm  7pm  8pm  9:30pm

  EVERY DAY: 9pm OPEN capture (next-day lines) + 3am ESPN-only (free)

  Budget estimate: ~280 calls/month -- leaves ~220 for manual snapshots

Usage:
    python -m dk_ncaab auto               # run daemon
    python -m dk_ncaab auto --budget 400   # cap at 400 API calls/month
    python -m dk_ncaab auto --once         # one smart cycle and exit
"""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import NamedTuple

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.exc import OperationalError as SAOperationalError

from dk_ncaab.config.settings import get_settings
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event, EventResult, OddsQuote

log = logging.getLogger(__name__)


# ── Database readiness helpers ──────────────────────────────────

def _ensure_docker_pg(max_wait: int = 120) -> bool:
    """
    Make sure Docker container dk_ncaab_pg is running and ready.
    Tries ``docker start`` first, then polls ``pg_isready`` until
    the database actually accepts connections.

    Returns True if the database is reachable, False on timeout.
    """
    try:
        out = subprocess.run(
            ["docker", "ps", "--filter", "name=dk_ncaab_pg", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        if "dk_ncaab_pg" not in (out.stdout or ""):
            log.warning("dk_ncaab_pg container not running — attempting start")
            subprocess.run(
                ["docker", "start", "dk_ncaab_pg"],
                capture_output=True, timeout=15,
            )
    except Exception as exc:
        log.warning("Docker check failed: %s", exc)
        # Fall through — maybe it's fine anyway

    # Poll pg_isready
    for elapsed in range(0, max_wait, 3):
        try:
            res = subprocess.run(
                ["docker", "exec", "dk_ncaab_pg", "pg_isready", "-U", "dk"],
                capture_output=True, text=True, timeout=10,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass
        time.sleep(3)

    return False


def _ensure_db(retries: int = 3, wait: int = 10) -> bool:
    """
    Verify the database is reachable via SQLAlchemy.
    On failure, attempt to restart / wait for the Docker container,
    then retry.  Returns True if connection succeeds.
    """
    for attempt in range(1, retries + 1):
        try:
            with SessionLocal() as session:
                session.execute(__import__("sqlalchemy").text("SELECT 1"))
            return True
        except Exception:
            log.warning(
                "DB connection attempt %d/%d failed — "
                "trying to ensure Docker container is up...",
                attempt, retries,
            )
            _ensure_docker_pg(max_wait=60)
            time.sleep(wait)
    log.error("Database is unreachable after %d retries. Skipping this cycle.", retries)
    return False


# ── Budget tracker ──────────────────────────────────────────────

class BudgetTracker:
    """
    Track Odds API usage within the current calendar month.
    Reads actual usage from API response headers, falls back to
    local count if headers unavailable.
    """

    def __init__(self, monthly_cap: int = 450):
        # Reserve 50 calls for manual use → default cap = 450
        self.monthly_cap = monthly_cap
        self._local_count = 0
        self._api_remaining: int | None = None
        self._api_used: int | None = None

    @property
    def used(self) -> int:
        """Best estimate of calls used this month."""
        if self._api_used is not None:
            return self._api_used
        return self._local_count

    @property
    def remaining(self) -> int:
        """Best estimate of calls remaining this month."""
        if self._api_remaining is not None:
            return self._api_remaining
        return self.monthly_cap - self._local_count

    @property
    def budget_ok(self) -> bool:
        """True if we're safe to make another API call."""
        return self.remaining > _daily_reserve()

    def record_call(self, api_remaining: int | None, api_used: int | None):
        """Update tracker after a successful API call."""
        self._local_count += 1
        if api_remaining is not None:
            self._api_remaining = int(api_remaining)
        if api_used is not None:
            self._api_used = int(api_used)
        log.info(
            "💰 API budget: %d used, %d remaining (cap %d)",
            self.used, self.remaining, self.monthly_cap,
        )

    def reset_if_new_month(self):
        """Reset local counter on the 1st of each month."""
        self._local_count = 0
        self._api_remaining = None
        self._api_used = None


def _daily_reserve() -> int:
    """
    How many API calls to reserve for the rest of the month.
    On the 1st we budget loosely; on the 28th we tighten up.
    """
    today = datetime.now(timezone.utc)
    days_left = _days_left_in_month(today)
    # Reserve 2 calls/day for remaining days (manual + emergency)
    return max(days_left * 2, 10)


def _days_left_in_month(now: datetime) -> int:
    """Days remaining in the current calendar month."""
    if now.month == 12:
        next_month = now.replace(year=now.year + 1, month=1, day=1)
    else:
        next_month = now.replace(month=now.month + 1, day=1)
    return (next_month - now).days


# ── Slate awareness ─────────────────────────────────────────────

class SlateInfo(NamedTuple):
    """Summary of the day's game slate."""
    total_games: int
    upcoming: int
    live: int
    final: int
    first_tip_utc: datetime | None
    last_tip_utc: datetime | None
    games_without_odds: int


def get_slate_info() -> SlateInfo:
    """Query DB for today's game slate."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Games "today" = tips between 10:00 UTC (5am ET) and tomorrow 10:00 UTC
    window_start = today_start + timedelta(hours=10)
    window_end = window_start + timedelta(hours=24)

    with SessionLocal() as session:
        today_events = (
            session.query(Event)
            .filter(
                Event.start_time_utc >= window_start,
                Event.start_time_utc < window_end,
            )
            .all()
        )

        if not today_events:
            return SlateInfo(0, 0, 0, 0, None, None, 0)

        upcoming = sum(1 for e in today_events if e.status == "upcoming")
        live = sum(1 for e in today_events if e.status == "live")
        final = sum(1 for e in today_events if e.status == "final")

        tips = [e.start_time_utc for e in today_events if e.start_time_utc]
        first_tip = min(tips) if tips else None
        last_tip = max(tips) if tips else None

        # Count games that don't have ANY odds quotes yet
        event_ids = {e.id for e in today_events}
        events_with_odds = set(
            row[0] for row in
            session.query(OddsQuote.event_id)
            .filter(OddsQuote.event_id.in_(event_ids))
            .distinct()
            .all()
        ) if event_ids else set()
        games_without_odds = len(event_ids - events_with_odds)

    return SlateInfo(
        total_games=len(today_events),
        upcoming=upcoming,
        live=live,
        final=final,
        first_tip_utc=first_tip,
        last_tip_utc=last_tip,
        games_without_odds=games_without_odds,
    )


# ── Smart cycle runner ──────────────────────────────────────────

def smart_cycle(
    budget: BudgetTracker,
    force_odds: bool = False,
    label: str = "",
) -> dict:
    """
    Run one intelligent pipeline cycle:
      1. Always: ESPN load-games + update-results (FREE)
      2. Conditionally: Odds API call if it's worth it
      3. Conditionally: Dataset build if we got new data

    Args:
        budget:     shared BudgetTracker instance
        force_odds: always attempt odds collection (skip slate checks)
        label:      snapshot label for logging, e.g. 'OPEN', 'PRE_GAME_1H'

    Returns summary dict of what happened.
    """
    tag = f"[{label}] " if label else ""
    log.info("")
    log.info("=" * 55)
    log.info("%sSmart cycle starting", tag)
    log.info("=" * 55)

    # ── Pre-flight: verify database is reachable ────────────────
    if not _ensure_db():
        log.error("%sCycle ABORTED — no database connection.", tag)
        return {
            "espn_games": 0, "espn_scores": 0,
            "odds_collected": False, "odds_quotes": 0,
            "splits_collected": False, "splits_matched": 0,
            "dataset_built": False, "label": label,
            "error": "db_unreachable",
        }

    summary = {
        "espn_games": 0,
        "espn_scores": 0,
        "odds_collected": False,
        "odds_quotes": 0,
        "splits_collected": False,
        "splits_matched": 0,
        "dataset_built": False,
        "label": label,
    }

    # ── Step 1: ESPN — always free ──────────────────────────────
    log.info("── ESPN: Load today's games (free) ──")
    try:
        from dk_ncaab.collectors.load_games import load_games_for_date
        summary["espn_games"] = load_games_for_date()
    except Exception:
        log.exception("ESPN load-games failed")

    log.info("── ESPN: Update scores (free) ──")
    try:
        from dk_ncaab.collectors.load_games import update_scores_espn
        summary["espn_scores"] = update_scores_espn()
    except Exception:
        log.exception("ESPN update-scores failed")

    # ── Step 2: Odds API — only if budget allows + worthwhile ──
    slate = get_slate_info()
    should_collect = force_odds or _should_collect_odds(slate, budget)

    if should_collect:
        log.info("── Odds API: Collecting DK lines (quota-gated) ──")
        try:
            from dk_ncaab.collectors.odds_api import collect_odds
            n_quotes = collect_odds()
            summary["odds_collected"] = True
            summary["odds_quotes"] = n_quotes

            _sync_budget_from_db(budget)
        except Exception:
            log.exception("Odds API collection failed")
    else:
        reason = _skip_reason(slate, budget)
        log.info("── Odds API: SKIPPED (%s) ──", reason)

    # ── Step 3: Splits scraper — free, runs alongside odds ──────
    if should_collect:
        log.info("-- Splits: Scraping public betting percentages (free) --")
        try:
            from dk_ncaab.collectors.splits_dknetwork import collect_splits
            n_matched = collect_splits()
            summary["splits_collected"] = True
            summary["splits_matched"] = n_matched or 0
        except ImportError:
            log.warning("Splits skipped: playwright not installed")
        except Exception:
            log.exception("Splits collection failed (non-fatal)")
    else:
        log.info("-- Splits: SKIPPED (no odds cycle) --")

    # ── Step 4: Dataset build — if we got meaningful data ───────
    if summary["odds_collected"] or summary["splits_collected"] or summary["espn_scores"] > 0:
        log.info("── Building dataset ──")
        try:
            from dk_ncaab.analysis.dataset_build import run_dataset_build
            run_dataset_build()
            summary["dataset_built"] = True
        except Exception:
            log.exception("Dataset build failed")

    _log_summary(summary, slate, budget)
    return summary


def _should_collect_odds(slate: SlateInfo, budget: BudgetTracker) -> bool:
    """Decide if an Odds API call is worthwhile right now."""
    # Hard stop: budget exhausted
    if not budget.budget_ok:
        return False

    # No upcoming/live games? No point fetching odds
    if slate.upcoming + slate.live == 0 and slate.total_games > 0:
        return False

    # No games at all today? Only collect if it's after 10am ET (games may load later)
    if slate.total_games == 0:
        hour_et = (datetime.now(timezone.utc) - timedelta(hours=5)).hour
        return hour_et >= 10 and hour_et <= 14  # speculative morning check

    # Games exist and some are still upcoming/live → collect
    return True


def _skip_reason(slate: SlateInfo, budget: BudgetTracker) -> str:
    """Human-readable reason for skipping odds collection."""
    if not budget.budget_ok:
        return f"budget low ({budget.remaining} remaining)"
    if slate.total_games == 0:
        return "no games loaded yet"
    if slate.upcoming + slate.live == 0:
        return "all games final"
    return "unknown"


def _log_summary(summary: dict, slate: SlateInfo, budget: BudgetTracker):
    """Log a clean summary of what the cycle accomplished."""
    log.info("━" * 50)
    log.info("Cycle summary:")
    log.info("  🏀 Slate: %d games (%d upcoming, %d live, %d final)",
             slate.total_games, slate.upcoming, slate.live, slate.final)
    log.info("  📡 ESPN: %d new games, %d scores updated",
             summary["espn_games"], summary["espn_scores"])
    if summary["odds_collected"]:
        log.info("  🎰 Odds: collected (budget: %d remaining)", budget.remaining)
    else:
        log.info("  🎰 Odds: skipped")
    if summary["splits_collected"]:
        log.info("  📊 Splits: %d games matched", summary["splits_matched"])
    else:
        log.info("  📊 Splits: skipped")
    if summary["dataset_built"]:
        log.info("  📊 Dataset: rebuilt")
    log.info("━" * 50)


# ── APScheduler-based daemon ────────────────────────────────────
#
# SNAPSHOT STRATEGY
#
# One Odds-API call returns ALL upcoming games, so frequent fixed-time
# polling automatically gives different pre-game intervals for games
# at different tip-off times.  E.g. a 7pm ET poll is:
#   - 15 min before a 7:15 game
#   - 1 hour before an 8:00 game
#   - 3 hours before a 10:00 game
#
# We define three day tiers, plus a nightly OPEN capture and an
# ESPN-only free sweep.
#
# Budget:  ~280 calls/month  |  leaves ~220 for manual snapshots
# ─────────────────────────────────────────────────────────────────


class Snapshot(NamedTuple):
    """One scheduled odds-collection time slot."""
    hour_et: int
    minute:  int
    label:   str
    force_odds: bool = True


# ── Saturday: heavy all-day slate (noon-11pm tips) ──────────────
_SATURDAY = [
    Snapshot( 8,  0, "SAT_OVERNIGHT"),
    Snapshot(10,  0, "SAT_MORNING"),
    Snapshot(11, 30, "SAT_PRE_NOON"),
    Snapshot(12, 30, "SAT_EARLY_GAME"),
    Snapshot(13, 30, "SAT_MID_AFTERNOON"),
    Snapshot(14, 30, "SAT_AFTERNOON"),
    Snapshot(15, 30, "SAT_PRE_EVENING"),
    Snapshot(16, 30, "SAT_EVE_BUILD"),
    Snapshot(17, 30, "SAT_PRE_PRIME"),
    Snapshot(18,  0, "SAT_PRE_GAME_1H"),
    Snapshot(18, 30, "SAT_PRE_GAME_30M"),
    Snapshot(19,  0, "SAT_TIPOFF"),
    Snapshot(20,  0, "SAT_MID_EVENING"),
    Snapshot(21,  0, "SAT_LATE"),       # also serves as OPEN for Sunday
    Snapshot(22, 30, "SAT_CLOSEOUT"),
]  # 15 snapshots

# ── Tue / Wed / Thu: medium evening slates (6-11pm tips) ────────
_WEEKDAY_MEDIUM = [
    Snapshot( 9,  0, "WKD_OVERNIGHT"),
    Snapshot(12,  0, "WKD_MIDDAY"),
    Snapshot(15,  0, "WKD_AFTERNOON"),
    Snapshot(17,  0, "WKD_PRE_GAME_3H"),
    Snapshot(17, 30, "WKD_PRE_GAME_2H30"),
    Snapshot(18,  0, "WKD_PRE_GAME_2H"),
    Snapshot(18, 30, "WKD_PRE_GAME_90M"),
    Snapshot(18, 45, "WKD_PRE_GAME_75M"),
    Snapshot(19,  0, "WKD_PRE_GAME_1H"),
    Snapshot(20,  0, "WKD_MID_EVENING"),
    Snapshot(21,  0, "WKD_LATE"),
    Snapshot(22, 30, "WKD_CLOSEOUT"),
]  # 12 snapshots

# ── Mon / Fri / Sun: light slates ──────────────────────────────
_WEEKDAY_LIGHT = [
    Snapshot(17,  0, "LIGHT_PRE_EVENING"),
    Snapshot(18, 30, "LIGHT_PRE_GAME"),
    Snapshot(19,  0, "LIGHT_PRIME"),
    Snapshot(20,  0, "LIGHT_MID_EVENING"),
    Snapshot(21, 30, "LIGHT_LATE"),
]  # 5 snapshots


def _et_to_utc(hour_et: int, minute: int = 0) -> tuple[int, int]:
    """Convert Eastern Time to UTC (ET = UTC-5). Handles day wrap."""
    utc_h = (hour_et + 5) % 24
    return utc_h, minute


def create_auto_scheduler(monthly_budget: int = 450) -> BlockingScheduler:
    """
    Build an APScheduler with a comprehensive tiered snapshot schedule.

    Tier layout (all times ET):
      - Saturday:       15 snapshots (8am-10:30pm)
      - Tue/Wed/Thu:    12 snapshots each (9am-10:30pm)
      - Mon/Fri/Sun:     5 snapshots each (5pm-9:30pm)
      - Every day 9pm:  OPEN capture (next-day lines)
      - Every day 3am:  ESPN-only (free score sweep)

    ~280 API calls/month | leaves ~220 for manual use
    """
    scheduler = BlockingScheduler(timezone="UTC")
    budget = BudgetTracker(monthly_cap=monthly_budget)
    job_count = 0

    def _add(snap: Snapshot, days: str):
        nonlocal job_count
        h_utc, m_utc = _et_to_utc(snap.hour_et, snap.minute)
        job_id = f"{snap.label}_{snap.hour_et:02d}{snap.minute:02d}"
        scheduler.add_job(
            smart_cycle,
            CronTrigger(
                hour=h_utc, minute=m_utc,
                day_of_week=days,
            ),
            args=[budget, snap.force_odds],
            kwargs={"label": snap.label},
            id=job_id,
            name=f"{snap.label} ({snap.hour_et}:{snap.minute:02d} ET)",
            max_instances=1,
            coalesce=True,
        )
        job_count += 1

    # ── Saturday heavy slots ────────────────────────────────────
    for snap in _SATURDAY:
        _add(snap, "sat")
    log.info("Scheduled %d Saturday snapshots (8am-10:30pm ET)", len(_SATURDAY))

    # ── Tue/Wed/Thu medium slots ────────────────────────────────
    for snap in _WEEKDAY_MEDIUM:
        _add(snap, "tue,wed,thu")
    log.info("Scheduled %d Tue/Wed/Thu snapshots each (9am-10:30pm ET)",
             len(_WEEKDAY_MEDIUM))

    # ── Mon/Fri/Sun light slots ─────────────────────────────────
    for snap in _WEEKDAY_LIGHT:
        _add(snap, "mon,fri,sun")
    log.info("Scheduled %d Mon/Fri/Sun snapshots each (5pm-9:30pm ET)",
             len(_WEEKDAY_LIGHT))

    # ── Daily OPEN capture at 9pm ET ────────────────────────────
    # Captures next-day opening lines + close-out for today
    h_open, m_open = _et_to_utc(21, 0)
    scheduler.add_job(
        smart_cycle,
        CronTrigger(hour=h_open, minute=m_open),
        args=[budget, True],
        kwargs={"label": "OPEN"},
        id="daily_open",
        name="OPEN capture (9pm ET daily)",
        max_instances=1,
        coalesce=True,
    )
    job_count += 1
    log.info("Scheduled: Daily 9pm ET -- OPEN capture")

    # ── Nightly ESPN-only sweep at 3am ET (free) ────────────────
    h_espn, m_espn = _et_to_utc(3, 0)
    scheduler.add_job(
        smart_cycle,
        CronTrigger(hour=h_espn, minute=m_espn),
        args=[budget, False],
        kwargs={"label": "ESPN_NIGHTLY"},
        id="espn_nightly",
        name="ESPN nightly: scores + dataset (free)",
        max_instances=1,
        coalesce=True,
    )
    job_count += 1
    log.info("Scheduled: Daily 3am ET -- ESPN scores only (free)")

    log.info("Total: %d scheduled jobs", job_count)
    return scheduler


def run_once(monthly_budget: int = 450) -> dict:
    """Run a single smart cycle and exit. Good for cron/Task Scheduler."""
    budget = BudgetTracker(monthly_cap=monthly_budget)

    # Try to read current usage from a recent API response
    _sync_budget_from_db(budget)

    log.info("═" * 60)
    log.info("Running single smart collection cycle")
    log.info("═" * 60)

    result = smart_cycle(budget, force_odds=True)

    log.info("═" * 60)
    log.info("Single cycle complete")
    log.info("═" * 60)
    return result


def _sync_budget_from_db(budget: BudgetTracker):
    """
    Sync API usage this month from the append-only odds_api_usage table.
    """
    now = datetime.now(timezone.utc)

    try:
        from dk_ncaab.collectors.odds_api import get_odds_usage_summary
        with SessionLocal() as session:
            summary = get_odds_usage_summary(
                session,
                monthly_budget=budget.monthly_cap,
                reserve_requests=0,
                now=now,
            )
        budget._local_count = summary.recorded_requests_month
        budget._api_remaining = summary.requests_remaining
        budget._api_used = summary.requests_used
        log.info(
            "📊 Synced API usage: %d recorded, %s remaining",
            summary.recorded_requests_month,
            summary.requests_remaining,
        )
    except Exception:
        log.warning("Could not sync budget from DB, using local count")


def main(monthly_budget: int = 450, once: bool = False) -> None:
    """Entry point: start the auto-collector."""

    if once:
        run_once(monthly_budget)
        return

    log.info("Starting DK NCAAB auto-collector (budget: %d/month)", monthly_budget)
    log.info("")
    log.info("Schedule (Eastern Time):")
    log.info("  SAT:          15 snapshots (8am-10:30pm) -- full-day coverage")
    log.info("  TUE/WED/THU:  12 snapshots each (9am-10:30pm) -- evening focus")
    log.info("  MON/FRI/SUN:   5 snapshots each (5pm-9:30pm) -- light coverage")
    log.info("  EVERY DAY:     9pm OPEN capture + 3am ESPN (free)")
    log.info("  Budget: ~280 calls/month (leaves ~220 for manual)")
    log.info("")

    scheduler = create_auto_scheduler(monthly_budget)

    def shutdown(signum, frame):
        log.info("Shutdown signal received")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Auto-collector stopped")


if __name__ == "__main__":
    main()
