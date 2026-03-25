"""
KenPom ratings collector — ingests historical efficiency metrics.

Sources (in priority order):
  1. CSV upload (manual export from kenpom.com)
  2. API integration (if kenpom.com offers one in future)

Per team per date, we store:
  AdjO, AdjD, AdjEM, Tempo, SoS

These are used to compute:
  KenPom_expected_spread ≈ AdjEM_diff + Home_Court_Adjustment
  spread_dev_* = market_spread - KenPom_expected_spread
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import KenPomRating, Team
from dk_ncaab.etl.normalize import normalize_team_name

log = logging.getLogger(__name__)

# Historical home-court advantage (KenPom convention ≈ 3.5 pts)
HOME_COURT_ADJ = 3.5


def expected_spread(
    home_adj_em: float,
    away_adj_em: float,
    home_court: float = HOME_COURT_ADJ,
) -> float:
    """
    KenPom-implied expected spread (negative = home favored).

    Convention: spread is from HOME perspective.
      Expected margin = AdjEM_home - AdjEM_away + home_court
      Market spread convention: negative = home favored
      So expected_spread = -(AdjEM_home - AdjEM_away + home_court)
    """
    return -(home_adj_em - away_adj_em + home_court)


def spread_deviation(market_spread: float, kenpom_spread: float) -> float:
    """
    How far the market spread deviates from KenPom's expectation.

    spread_dev = market_spread - kenpom_expected_spread

    Positive → market has home as bigger underdog than KenPom expects.
    Negative → market has home as bigger favorite than KenPom expects.
    """
    return market_spread - kenpom_spread


def get_rating_for_event(
    session: Session,
    team_id: int,
    event_date: datetime,
) -> KenPomRating | None:
    """
    Get the most recent KenPom rating for a team on or before the event date.
    This avoids look-ahead bias — we only use data available at game time.
    """
    return (
        session.query(KenPomRating)
        .filter(
            KenPomRating.team_id == team_id,
            KenPomRating.rating_date <= event_date,
        )
        .order_by(KenPomRating.rating_date.desc())
        .first()
    )


def compute_event_kenpom(
    session: Session,
    home_team_id: int,
    away_team_id: int,
    event_date: datetime,
) -> dict | None:
    """
    Compute KenPom-derived metrics for an event.

    Returns dict with:
      home_adj_em, away_adj_em, adj_em_diff,
      home_adj_o, home_adj_d, away_adj_o, away_adj_d,
      home_tempo, away_tempo,
      kenpom_expected_spread
    Or None if ratings unavailable.
    """
    home_r = get_rating_for_event(session, home_team_id, event_date)
    away_r = get_rating_for_event(session, away_team_id, event_date)

    if not home_r or not away_r:
        return None

    kp_spread = expected_spread(home_r.adj_em, away_r.adj_em)

    return {
        "home_adj_o": home_r.adj_o,
        "home_adj_d": home_r.adj_d,
        "home_adj_em": home_r.adj_em,
        "home_tempo": home_r.tempo,
        "home_sos": home_r.sos,
        "away_adj_o": away_r.adj_o,
        "away_adj_d": away_r.adj_d,
        "away_adj_em": away_r.adj_em,
        "away_tempo": away_r.tempo,
        "away_sos": away_r.sos,
        "adj_em_diff": home_r.adj_em - away_r.adj_em,
        "kenpom_expected_spread": kp_spread,
    }


# ── CSV import ──────────────────────────────────────────────────

def import_kenpom_csv(
    csv_path: str | Path,
    rating_date: datetime | None = None,
    session: Session | None = None,
) -> int:
    """
    Import KenPom ratings from a CSV file.

    Expected columns: Team, AdjO, AdjD, AdjEM, Tempo, SoS
    (case-insensitive, flexible matching).

    Args:
        csv_path: Path to the CSV.
        rating_date: Date for these ratings (defaults to today).
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

    if rating_date is None:
        rating_date = datetime.now(timezone.utc)

    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            # Normalize column names
            if reader.fieldnames is None:
                log.error("Empty CSV")
                return 0

            col_map = _map_columns(reader.fieldnames)
            if not col_map:
                log.error("Cannot map CSV columns: %s", reader.fieldnames)
                return 0

            count = 0
            for row in reader:
                team_name = row.get(col_map["team"], "").strip()
                if not team_name:
                    continue

                norm = normalize_team_name(team_name)
                team = session.query(Team).filter_by(normalized_name=norm).first()
                if not team:
                    log.debug("Unknown team in CSV: %s → %s", team_name, norm)
                    continue

                # Check for existing rating on this date
                existing = (
                    session.query(KenPomRating)
                    .filter_by(team_id=team.id, rating_date=rating_date)
                    .first()
                )

                vals = {
                    "adj_o": _safe_float(row.get(col_map.get("adj_o", ""), "")),
                    "adj_d": _safe_float(row.get(col_map.get("adj_d", ""), "")),
                    "adj_em": _safe_float(row.get(col_map.get("adj_em", ""), "")),
                    "tempo": _safe_float(row.get(col_map.get("tempo", ""), "")),
                    "sos": _safe_float(row.get(col_map.get("sos", ""), "")),
                }

                if vals["adj_o"] is None or vals["adj_d"] is None or vals["adj_em"] is None:
                    log.debug("Skipping %s: missing required fields", team_name)
                    continue

                # Default tempo if missing
                if vals["tempo"] is None:
                    vals["tempo"] = 68.0

                if existing:
                    for k, v in vals.items():
                        if v is not None:
                            setattr(existing, k, v)
                else:
                    session.add(KenPomRating(
                        team_id=team.id,
                        rating_date=rating_date,
                        **vals,
                    ))
                count += 1

            session.commit()
            log.info("Imported %d KenPom ratings for %s", count, rating_date.date())
            return count

    finally:
        if own_session:
            session.close()


def _map_columns(fieldnames: list[str]) -> dict[str, str]:
    """Map CSV column names to our internal names (case-insensitive)."""
    mapping = {}
    lower_map = {f.lower().strip(): f for f in fieldnames}

    # Team name column
    for candidate in ["team", "teamname", "team_name", "school"]:
        if candidate in lower_map:
            mapping["team"] = lower_map[candidate]
            break

    # Efficiency metrics
    col_aliases = {
        "adj_o": ["adjo", "adj_o", "adjoe", "adj_oe", "adjoffense"],
        "adj_d": ["adjd", "adj_d", "adjde", "adj_de", "adjdefense"],
        "adj_em": ["adjem", "adj_em", "adjefficiency", "netrtg"],
        "tempo": ["tempo", "adj_tempo", "adjtempo", "pace"],
        "sos": ["sos", "strength_of_schedule", "sosrank", "sos_rank"],
    }

    for our_key, candidates in col_aliases.items():
        for c in candidates:
            if c in lower_map:
                mapping[our_key] = lower_map[c]
                break

    return mapping if "team" in mapping else {}


def _safe_float(val: str) -> float | None:
    """Parse a float, returning None on failure."""
    try:
        return float(val.strip()) if val.strip() else None
    except (ValueError, AttributeError):
        return None
