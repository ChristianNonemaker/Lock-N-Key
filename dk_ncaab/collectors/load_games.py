"""
ESPN-based game loader + score updater.

Uses ESPN's free, unlimited public API to:
  1. Load all games for a given date into the events table.
  2. Capture final scores into event_results (no Odds API needed).
  3. Update event status (upcoming → live → final) on re-run.

This is the backbone for historical backfill and daily result updates.
ESPN data costs $0 and has no rate limits.

Usage via CLI:
    python -m dk_ncaab load-games                     # today
    python -m dk_ncaab load-games --date 2026-02-14
    python -m dk_ncaab backfill --days 60
    python -m dk_ncaab update-results
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta

import httpx
from sqlalchemy.orm import Session

from dk_ncaab.db.models import League, Event, EventResult
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.etl.normalize import resolve_team, get_or_create_team

log = logging.getLogger(__name__)

_ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/"
    "basketball/mens-college-basketball/scoreboard"
)

# ESPN event status mapping
_STATUS_MAP = {"pre": "upcoming", "in": "live", "post": "final"}


# ── Public entrypoints ──────────────────────────────────────────

def load_games_for_date(target_date: datetime | None = None) -> int:
    """
    Fetch the ESPN scoreboard for *target_date*, upsert events,
    and capture any completed scores.
    Returns count of newly created events.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc)

    date_str = target_date.strftime("%Y%m%d")
    log.info("Loading games for %s from ESPN …", date_str)

    espn_events = _fetch_espn_scoreboard(date_str)
    if not espn_events:
        log.warning("ESPN returned 0 events for %s", date_str)
        return 0

    log.info("ESPN returned %d events for %s", len(espn_events), date_str)

    created = 0
    scores_added = 0
    status_updated = 0
    failed = 0

    with SessionLocal() as session:
        league = _ensure_league(session)

        for ev in espn_events:
            try:
                c, s, u = _process_espn_event(session, ev, league)
                created += c
                scores_added += s
                status_updated += u
            except Exception:
                failed += 1
                log.exception(
                    "Failed to process ESPN event %s", ev.get("id", "?")
                )

        session.commit()

    log.info(
        "Done — new_events=%d  scores_added=%d  status_updated=%d  failed=%d",
        created, scores_added, status_updated, failed,
    )
    return created


def update_scores_espn() -> int:
    """
    Re-check every non-final event via ESPN and capture scores.
    Call this periodically (free!) to close out finished games.
    Returns count of newly added results.
    """
    log.info("Updating scores for non-final events via ESPN …")

    with SessionLocal() as session:
        pending = (
            session.query(Event)
            .filter(Event.status.in_(["upcoming", "live"]))
            .all()
        )
        if not pending:
            log.info("No pending events to update")
            return 0

        log.info("Checking %d pending events", len(pending))

        # Group events by date to batch ESPN calls
        dates: set[str] = set()
        for e in pending:
            dates.add(e.start_time_utc.strftime("%Y%m%d"))

        league = _ensure_league(session)
        scores_added = 0
        status_updated = 0

        for date_str in sorted(dates):
            espn_events = _fetch_espn_scoreboard(date_str)
            for ev in espn_events:
                try:
                    _, s, u = _process_espn_event(session, ev, league)
                    scores_added += s
                    status_updated += u
                except Exception:
                    log.exception(
                        "Score update failed for ESPN event %s",
                        ev.get("id", "?"),
                    )

        session.commit()

    log.info(
        "Score update done — scores_added=%d  status_updated=%d",
        scores_added, status_updated,
    )
    return scores_added


def backfill_espn(days: int = 30) -> dict:
    """
    Backfill N days of historical games + scores from ESPN.
    Completely free — no Odds API calls used.
    Returns summary dict.
    """
    log.info("Backfilling %d days of ESPN data …", days)
    today = datetime.now(timezone.utc)
    summary = {"days": days, "total_created": 0, "total_scores": 0}

    for offset in range(days, 0, -1):
        target = today - timedelta(days=offset)
        date_str = target.strftime("%Y%m%d")

        try:
            espn_events = _fetch_espn_scoreboard(date_str)
            if not espn_events:
                continue

            created = 0
            scores = 0

            with SessionLocal() as session:
                league = _ensure_league(session)
                for ev in espn_events:
                    try:
                        c, s, _ = _process_espn_event(session, ev, league)
                        created += c
                        scores += s
                    except Exception:
                        pass
                session.commit()

            if created > 0 or scores > 0:
                log.info(
                    "  %s: +%d events, +%d scores",
                    target.strftime("%Y-%m-%d"), created, scores,
                )
            summary["total_created"] += created
            summary["total_scores"] += scores
        except (KeyboardInterrupt, SystemExit):
            log.warning("Backfill interrupted at %s — saving progress", date_str)
            break
        except Exception:
            log.exception("Backfill error on date %s — skipping", date_str)

        # Polite delay between date fetches (ESPN may throttle rapid calls)
        time.sleep(1.5)

    log.info(
        "Backfill complete: %d events, %d scores across %d days",
        summary["total_created"], summary["total_scores"], days,
    )
    return summary


# ── ESPN API helpers ────────────────────────────────────────────

def _fetch_espn_scoreboard(date_str: str) -> list[dict]:
    """Fetch ESPN scoreboard JSON for a YYYYMMDD date string, with retry.

    Catches *all* exceptions (including KeyboardInterrupt on Windows
    SSL timeouts) so that one bad date never crashes a full backfill.
    """
    params = {"dates": date_str, "limit": 200, "groups": "50"}
    for attempt in range(3):
        try:
            resp = httpx.get(_ESPN_SCOREBOARD, params=params, timeout=60)
            resp.raise_for_status()
            return resp.json().get("events", [])
        except (KeyboardInterrupt, SystemExit):
            # On Windows, SSL timeouts can surface as KeyboardInterrupt.
            # Wait longer and retry; only propagate on final attempt.
            log.warning(
                "ESPN fetch interrupted for %s (attempt %d/3)", date_str, attempt + 1
            )
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            log.error("ESPN fetch failed for %s after 3 attempts (interrupt)", date_str)
            return []
        except Exception:
            log.warning(
                "ESPN fetch error for %s (attempt %d/3): %s",
                date_str, attempt + 1,
                __import__("traceback").format_exc().splitlines()[-1],
            )
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            log.error("ESPN fetch failed for %s after 3 retries", date_str)
            return []


def _ensure_league(session: Session) -> League:
    league = session.query(League).filter_by(key="ncaab").first()
    if not league:
        league = League(key="ncaab", name="NCAA Men's Basketball")
        session.add(league)
        session.flush()
    return league


# ── Event processing ────────────────────────────────────────────

def _process_espn_event(
    session: Session, ev: dict, league: League
) -> tuple[int, int, int]:
    """
    Process one ESPN event dict.
    Returns (created, score_added, status_updated) — each 0 or 1.
    """
    espn_id = str(ev["id"])
    external_key = f"espn:{espn_id}"

    # Parse competitors
    competitions = ev.get("competitions", [])
    if not competitions:
        return 0, 0, 0

    comp = competitions[0]
    competitors = comp.get("competitors", [])
    if len(competitors) < 2:
        return 0, 0, 0

    # Identify home/away + scores
    home_raw = away_raw = None
    home_score_raw = away_score_raw = None
    for c in competitors:
        team_info = c.get("team", {})
        name = (
            team_info.get("displayName")
            or team_info.get("shortDisplayName")
            or team_info.get("name", "")
        )
        ha = c.get("homeAway", "")
        score = c.get("score")
        if ha == "home":
            home_raw = name
            home_score_raw = score
        else:
            away_raw = name
            away_score_raw = score

    if not home_raw or not away_raw:
        return 0, 0, 0

    # Parse status
    status_obj = ev.get("status", {})
    status_type = status_obj.get("type", {})
    espn_state = status_type.get("state", "pre")
    db_status = _STATUS_MAP.get(espn_state, "upcoming")

    # Parse start time
    start_str = ev.get("date", "")
    start_utc = (
        datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        if start_str
        else datetime.now(timezone.utc)
    )

    # ── Check if event already exists ──
    existing = (
        session.query(Event)
        .filter_by(external_event_key=external_key)
        .first()
    )

    created = 0
    status_updated = 0
    if existing:
        event = existing
        # Update status if it progressed
        if event.status != db_status and db_status in ("live", "final"):
            event.status = db_status
            status_updated = 1
    else:
        # Resolve/create teams and insert event
        home_team = _resolve_or_create(session, home_raw, league.id)
        away_team = _resolve_or_create(session, away_raw, league.id)
        event = Event(
            league_id=league.id,
            external_event_key=external_key,
            start_time_utc=start_utc,
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            status=db_status,
        )
        session.add(event)
        session.flush()
        created = 1
        log.info(
            "  ✓ %s @ %s  (%s)  [%s]",
            away_raw, home_raw,
            start_utc.strftime("%H:%M UTC"), db_status,
        )

    # ── Capture scores for completed games ──
    score_added = 0
    if (
        db_status == "final"
        and home_score_raw is not None
        and away_score_raw is not None
    ):
        existing_result = (
            session.query(EventResult)
            .filter_by(event_id=event.id)
            .first()
        )
        if not existing_result:
            try:
                home_score = int(home_score_raw)
                away_score = int(away_score_raw)
                result = EventResult(
                    event_id=event.id,
                    home_score=home_score,
                    away_score=away_score,
                    status="final",
                    completed_at_utc=start_utc,
                )
                session.add(result)
                session.flush()
                score_added = 1
                log.info(
                    "  📊 %s %d – %s %d",
                    home_raw, home_score, away_raw, away_score,
                )
            except (ValueError, TypeError):
                pass

    return created, score_added, status_updated


def _resolve_or_create(session: Session, raw_name: str, league_id: int):
    """Try alias table first; fall back to get_or_create_team."""
    team = resolve_team(session, raw_name, source="espn", league_id=league_id)
    if team:
        return team
    return get_or_create_team(session, raw_name, source="espn", league_id=league_id)
