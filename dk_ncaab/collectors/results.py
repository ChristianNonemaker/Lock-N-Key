"""
Results collector – polls The-Odds-API /scores endpoint for completed games.

Populates event_results and updates events.status.
"""

from __future__ import annotations

import logging
import time
import random
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from dk_ncaab.config.settings import get_settings
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event, EventResult

log = logging.getLogger(__name__)


def _fetch_scores(client: httpx.Client) -> list[dict] | None:
    """GET /v4/sports/{sport}/scores for completed games."""
    cfg = get_settings().odds_api
    url = f"{cfg.base_url}/sports/{cfg.sport}/scores"
    params = {
        "apiKey": cfg.key,
        "daysFrom": "3",  # look back 3 days
    }
    for attempt in range(4):
        try:
            resp = client.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            wait = (2 ** attempt) + random.uniform(0, 1)
            log.warning("Scores fetch error %s, retry in %.1fs", e, wait)
            time.sleep(wait)
    return None


def _process_score(session: Session, game: dict) -> bool:
    """
    Process a single game score response.
    Returns True if a new result was inserted.
    """
    if not game.get("completed"):
        return False

    ext_key = game.get("id", "")
    event = session.query(Event).filter_by(external_event_key=ext_key).first()
    if not event:
        log.debug("No matching event for score key=%s", ext_key)
        return False

    # Already have result?
    existing = session.query(EventResult).filter_by(event_id=event.id).first()
    if existing:
        return False

    # Parse scores: scores is a list of {name, score} dicts
    scores_list = game.get("scores") or []
    score_map: dict[str, int] = {}
    for s in scores_list:
        try:
            score_map[s["name"]] = int(s["score"])
        except (KeyError, ValueError, TypeError):
            continue

    home_name = game.get("home_team", "")
    away_name = game.get("away_team", "")
    home_score = score_map.get(home_name)
    away_score = score_map.get(away_name)

    if home_score is None or away_score is None:
        log.warning("Could not parse scores for %s vs %s", home_name, away_name)
        return False

    now = datetime.now(timezone.utc)
    result = EventResult(
        event_id=event.id,
        home_score=home_score,
        away_score=away_score,
        status="final",
        completed_at_utc=now,
    )
    session.add(result)

    # Update event lifecycle
    event.status = "final"
    log.info("Result: %s %d – %s %d", home_name, home_score, away_name, away_score)
    return True


def collect_results() -> None:
    """Single poll cycle for game results."""
    log.info("Starting results collection cycle")

    with httpx.Client() as client:
        data = _fetch_scores(client)

    if data is None:
        log.error("Scores fetch returned None, skipping")
        return

    with SessionLocal() as session:
        inserted = sum(_process_score(session, g) for g in data)
        session.commit()

    log.info("Results cycle complete: %d new results", inserted)
