"""
AP Rankings collector — ingests weekly AP Top-25 polls.

Sources:
  1. CSV upload (manual)
  2. Future: API or scraper from sports-reference.com

Per team per poll week, stores rank (1-25) and votes.
Unranked teams are NOT stored — absence implies unranked.

Features computed from rankings:
  - ap_rank_home, ap_rank_away (0 = unranked for modeling)
  - ap_rank_diff
  - ranked_vs_unranked flag (1 = ranked team vs unranked opponent)
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import APRanking, Team
from dk_ncaab.etl.normalize import normalize_team_name

log = logging.getLogger(__name__)

# Sentinel for "unranked" in feature computation (rank 0 or 26+)
UNRANKED_SENTINEL = 0


def get_rank_for_event(
    session: Session,
    team_id: int,
    event_date: datetime,
) -> int:
    """
    Get the most recent AP ranking for a team on or before event_date.
    Returns the rank (1-25) or UNRANKED_SENTINEL (0) if not ranked.
    """
    row = (
        session.query(APRanking)
        .filter(
            APRanking.team_id == team_id,
            APRanking.poll_date <= event_date,
        )
        .order_by(APRanking.poll_date.desc())
        .first()
    )
    if not row:
        return UNRANKED_SENTINEL

    # If the poll is more than 14 days old, treat as stale → unranked
    days_diff = (event_date - row.poll_date).days
    if days_diff > 14:
        return UNRANKED_SENTINEL

    return row.rank


def compute_event_ap_features(
    session: Session,
    home_team_id: int,
    away_team_id: int,
    event_date: datetime,
) -> dict:
    """
    Compute AP-ranking-derived features for an event.

    Returns dict with:
      ap_rank_home:     1-25 or 0 (unranked)
      ap_rank_away:     1-25 or 0 (unranked)
      ap_rank_diff:     away_rank - home_rank (positive = home is better-ranked)
      ranked_vs_unranked: 1 if exactly one team is ranked, else 0
    """
    home_rank = get_rank_for_event(session, home_team_id, event_date)
    away_rank = get_rank_for_event(session, away_team_id, event_date)

    # For diff: use 26 as proxy for unranked so the math is meaningful
    home_eff = home_rank if home_rank > 0 else 26
    away_eff = away_rank if away_rank > 0 else 26
    rank_diff = away_eff - home_eff  # positive = home ranked higher

    home_ranked = home_rank > 0
    away_ranked = away_rank > 0
    ranked_vs_unranked = int(home_ranked != away_ranked)

    return {
        "ap_rank_home": home_rank,
        "ap_rank_away": away_rank,
        "ap_rank_diff": rank_diff,
        "ranked_vs_unranked": ranked_vs_unranked,
    }


# ── CSV import ──────────────────────────────────────────────────

def import_ap_csv(
    csv_path: str | Path,
    poll_date: datetime | None = None,
    session: Session | None = None,
) -> int:
    """
    Import AP rankings from a CSV file.

    Expected columns: Rank, Team, Votes (optional)

    Args:
        csv_path: Path to the CSV.
        poll_date: Monday of the poll week (defaults to today).
        session: DB session (creates one if None).

    Returns: Number of rows inserted/updated.
    """
    own_session = session is None
    if own_session:
        session = SessionLocal()

    csv_path = Path(csv_path)
    if not csv_path.exists():
        log.error("CSV not found: %s", csv_path)
        return 0

    if poll_date is None:
        poll_date = datetime.now(timezone.utc)

    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                log.error("Empty CSV")
                return 0

            col_map = _map_columns(reader.fieldnames)
            if not col_map:
                log.error("Cannot map AP CSV columns: %s", reader.fieldnames)
                return 0

            count = 0
            for row in reader:
                rank_str = row.get(col_map.get("rank", ""), "").strip()
                team_name = row.get(col_map["team"], "").strip()
                votes_str = row.get(col_map.get("votes", ""), "").strip()

                if not team_name or not rank_str:
                    continue

                try:
                    rank = int(rank_str)
                except ValueError:
                    continue

                if rank < 1 or rank > 25:
                    continue

                norm = normalize_team_name(team_name)
                team = session.query(Team).filter_by(normalized_name=norm).first()
                if not team:
                    log.debug("Unknown AP team: %s → %s", team_name, norm)
                    continue

                votes = None
                if votes_str:
                    try:
                        votes = int(votes_str)
                    except ValueError:
                        pass

                existing = (
                    session.query(APRanking)
                    .filter_by(team_id=team.id, poll_date=poll_date)
                    .first()
                )

                if existing:
                    existing.rank = rank
                    existing.votes = votes
                else:
                    session.add(APRanking(
                        team_id=team.id,
                        poll_date=poll_date,
                        rank=rank,
                        votes=votes,
                    ))
                count += 1

            session.commit()
            log.info("Imported %d AP rankings for %s", count, poll_date.date())
            return count

    finally:
        if own_session:
            session.close()


def _map_columns(fieldnames: list[str]) -> dict[str, str]:
    """Map CSV columns to internal names."""
    mapping = {}
    lower_map = {f.lower().strip(): f for f in fieldnames}

    for candidate in ["team", "school", "teamname", "team_name"]:
        if candidate in lower_map:
            mapping["team"] = lower_map[candidate]
            break

    for candidate in ["rank", "rk", "ranking", "ap_rank"]:
        if candidate in lower_map:
            mapping["rank"] = lower_map[candidate]
            break

    for candidate in ["votes", "pts", "points"]:
        if candidate in lower_map:
            mapping["votes"] = lower_map[candidate]
            break

    return mapping if "team" in mapping else {}
