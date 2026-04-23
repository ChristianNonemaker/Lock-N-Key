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
from sqlalchemy import or_
from sqlalchemy.orm import Session

from dk_ncaab.config.settings import get_settings
from dk_ncaab.config.sports import (
    espn_scoreboard_params_for,
    espn_scoreboard_url_for,
    league_for_sport,
    sport_for_league_key,
)
from dk_ncaab.db.models import League, Event, EventResult
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.etl.normalize import resolve_team, get_or_create_team

log = logging.getLogger(__name__)

# ESPN event status mapping
_STATUS_MAP = {"pre": "upcoming", "in": "live", "post": "final"}


# ── Public entrypoints ──────────────────────────────────────────

def load_games_for_date(
    target_date: datetime | None = None,
    sport: str | None = None,
) -> int:
    """
    Fetch the ESPN scoreboard for *target_date*, upsert events,
    and capture any completed scores.
    Returns count of newly created events.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc)

    date_str = target_date.strftime("%Y%m%d")
    log.info("Loading games for %s from ESPN …", date_str)

    sports = _active_sports(sport)

    created = 0
    scores_added = 0
    status_updated = 0
    failed = 0

    with SessionLocal() as session:
        for sp in sports:
            espn_events = _fetch_espn_scoreboard(date_str, sp)
            if not espn_events:
                log.warning("ESPN returned 0 events for %s [%s]", date_str, sp)
                continue

            log.info("ESPN returned %d events for %s [%s]", len(espn_events), date_str, sp)
            league = _ensure_league(session, sp)

            for ev in espn_events:
                try:
                    c, s, u = _process_espn_event(session, ev, league, sp)
                    created += c
                    scores_added += s
                    status_updated += u
                except Exception:
                    failed += 1
                    log.exception(
                        "Failed to process ESPN event %s [%s]", ev.get("id", "?"), sp
                    )

        session.commit()

    log.info(
        "Done — new_events=%d  scores_added=%d  status_updated=%d  failed=%d",
        created, scores_added, status_updated, failed,
    )
    return created


def load_games_window(
    start_date: datetime | None = None,
    days: int | None = None,
    sport: str | None = None,
) -> dict:
    """
    Load a forward ESPN schedule window.

    This is free and is the normal cron path. It seeds upcoming slates before
    any paid odds call runs, so the board can show games even when odds polling
    is disabled or very low-frequency.
    """
    cfg = get_settings().schedule
    if start_date is None:
        start_date = datetime.now(timezone.utc)
    if days is None:
        days = cfg.lookahead_days
    if days < 1:
        raise ValueError("days must be >= 1")

    summary = {"days": days, "total_created": 0}
    log.info("Loading ESPN schedule window: start=%s days=%d", start_date.date(), days)

    for offset in range(days):
        target = start_date + timedelta(days=offset)
        created = load_games_for_date(target, sport=sport)
        summary["total_created"] += created
        if offset < days - 1 and cfg.request_delay_sec > 0:
            time.sleep(cfg.request_delay_sec)

    log.info(
        "Schedule window complete: %d new events across %d days",
        summary["total_created"],
        days,
    )
    return summary


def update_scores_espn(sport: str | None = None) -> int:
    """
    Re-check every non-final event via ESPN and capture scores.
    Call this periodically (free!) to close out finished games.
    Returns count of newly added results.
    """
    log.info("Updating scores for non-final events via ESPN …")

    with SessionLocal() as session:
        pending = (
            session.query(Event)
            .outerjoin(EventResult, EventResult.event_id == Event.id)
            .filter(
                or_(
                    Event.status.in_(["upcoming", "live"]),
                    EventResult.event_id.is_(None),
                )
            )
            .all()
        )
        if not pending:
            log.info("No pending events to update")
            return 0

        log.info("Checking %d pending events", len(pending))

        # Group events by sport/date to batch ESPN calls.
        grouped_dates: dict[str, set[str]] = {}
        for e in pending:
            sp = _sport_from_event(e)
            if sport and sp != sport:
                continue
            grouped_dates.setdefault(sp, set()).add(e.start_time_utc.strftime("%Y%m%d"))

        scores_added = 0
        status_updated = 0

        for sp, dates in grouped_dates.items():
            league = _ensure_league(session, sp)
            for date_str in sorted(dates):
                espn_events = _fetch_espn_scoreboard(date_str, sp)
                for ev in espn_events:
                    try:
                        _, s, u = _process_espn_event(session, ev, league, sp)
                        scores_added += s
                        status_updated += u
                    except Exception:
                        log.exception(
                            "Score update failed for ESPN event %s [%s]",
                            ev.get("id", "?"),
                            sp,
                        )

        session.commit()

    log.info(
        "Score update done — scores_added=%d  status_updated=%d",
        scores_added, status_updated,
    )
    return scores_added


def backfill_espn(days: int = 30, sport: str | None = None) -> dict:
    """
    Backfill N days of historical games + scores from ESPN.
    Completely free — no Odds API calls used.
    Returns summary dict.
    """
    log.info("Backfilling %d days of ESPN data …", days)
    today = datetime.now(timezone.utc)
    summary = {"days": days, "total_created": 0, "total_scores": 0}

    sports = _active_sports(sport)

    for offset in range(days, 0, -1):
        target = today - timedelta(days=offset)
        date_str = target.strftime("%Y%m%d")

        try:
            created = 0
            scores = 0

            with SessionLocal() as session:
                for sp in sports:
                    espn_events = _fetch_espn_scoreboard(date_str, sp)
                    if not espn_events:
                        continue

                    league = _ensure_league(session, sp)
                    for ev in espn_events:
                        try:
                            c, s, _ = _process_espn_event(session, ev, league, sp)
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

def _fetch_espn_scoreboard(date_str: str, sport: str) -> list[dict]:
    """Fetch ESPN scoreboard JSON for a YYYYMMDD date string, with retry.

    Catches *all* exceptions (including KeyboardInterrupt on Windows
    SSL timeouts) so that one bad date never crashes a full backfill.
    """
    params = _espn_scoreboard_params(date_str, sport)
    scoreboard_url = espn_scoreboard_url_for(sport)
    for attempt in range(3):
        try:
            resp = httpx.get(scoreboard_url, params=params, timeout=60)
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

    return []


def _espn_scoreboard_params(date_str: str, sport: str) -> dict[str, str]:
    return espn_scoreboard_params_for(sport, date_str)


def _ensure_league(session: Session, sport: str) -> League:
    league_key, league_name = league_for_sport(sport)
    league = session.query(League).filter_by(key=league_key).first()
    if not league:
        league = League(key=league_key, name=league_name)
        session.add(league)
        session.flush()
    return league


# ── Event processing ────────────────────────────────────────────

def _process_espn_event(
    session: Session,
    ev: dict,
    league: League,
    sport: str,
) -> tuple[int, int, int]:
    """
    Process one ESPN event dict.
    Returns (created, score_added, status_updated) — each 0 or 1.
    """
    espn_id = str(ev["id"])
    # Backward-compatible for existing NCAAB rows.
    external_key = f"espn:{espn_id}" if sport == "basketball_ncaab" else f"espn:{sport}:{espn_id}"

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
        elif ha == "away":
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
        if event.status != "final" and event.status != db_status and db_status in ("live", "final"):
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


def _active_sports(sport: str | None) -> list[str]:
    if sport:
        espn_scoreboard_url_for(sport)
        return [sport]

    return get_settings().schedule.active_sports()


def _sport_from_event(event: Event) -> str:
    key = event.external_event_key or ""
    if key.startswith("espn:"):
        parts = key.split(":")
        if len(parts) >= 3:
            return parts[1]
    if event.league and event.league.key:
        try:
            return sport_for_league_key(event.league.key)
        except ValueError:
            pass
    return "basketball_ncaab"
