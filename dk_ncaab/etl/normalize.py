"""
Team-name normalization and event-matching utilities.

Core ideas:
  - normalize_team_name() → deterministic canonical form.
  - resolve_team() → lookup alias table, fall back to fuzzy match.
  - match_event() → pair two team names + tip time → event_id or None.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from dk_ncaab.db.models import Team, TeamAlias, Event

# ── Canonical replacements applied in order ─────────────────────
_REPLACEMENTS: list[tuple[str, str]] = [
    (r"\bst\b", "state"),
    (r"\bmt\b", "mount"),
    (r"\bft\b", "fort"),
    (r"\buniv\b", "university"),
    (r"\bu\b", "university"),
    (r"&", "and"),
    (r"\bso\b", "southern"),
    (r"\bno\b", "northern"),
    (r"\bw\b", "western"),
    (r"\be\b", "eastern"),
    (r"\bse\b", "southeastern"),
    (r"\bsw\b", "southwestern"),
    (r"\bnw\b", "northwestern"),
    (r"\bne\b", "northeastern"),
]

# ── Hard alias map: normalized Action-Network form → DB canonical form ──
# Used as a fallback when normalize_team_name() output doesn't match the
# teams table.  Keys must already be normalized (lowercase, no punctuation).
_HARD_ALIASES: dict[str, str] = {
    # Abbreviations → full names
    "ab christian": "abilene christian",
    "american university": "american",
    "app state": "appalachian state",
    "ar pine bluff": "arkansas pine bluff",
    "ark little rock": "little rock",
    "b cookman": "bethune cookman",
    "boston col": "boston college",
    "bryant university": "bryant",
    "byu": "byu cougars",
    "c arkansas": "central arkansas",
    "c michigan": "central michigan",
    "cal baptist": "california baptist",
    "central conn": "central connecticut",
    "citadel": "the citadel",
    "cs fullerton": "cal state fullerton",
    "cs northridge": "cal state northridge",
    "eastern carolina": "east carolina",
    "eastern ta and m": "east texas a and m lions",
    "eastern texas a and m": "east texas a and m lions",
    "eastern tennessee state": "east tennessee state",
    "fairleigh": "fairleigh dickinson",
    "fgcu": "florida gulf coast",
    "fiu": "florida international",
    "g tech": "georgia tech",
    "ga southern": "georgia southern",
    "hou christian": "houston christian",
    "jax state": "jacksonville state",
    "jmu": "james madison",
    "k state": "kansas state",
    "la tech": "louisiana tech",
    "lakers": "mercyhurst lakers",
    "lbsu": "long beach state",
    "mcneese state": "mcneese",
    "md eastern shore": "maryland eastern shore",
    "middle tenn": "middle tennessee",
    "ms valley state": "mississippi valley state",
    "n arizona": "northern arizona",
    "n colorado": "northern colorado",
    "n illinois": "northern illinois",
    "n mexico state": "new mexico state",
    "nc central": "north carolina central",
    "nc state": "north carolina state",
    "ndsu": "north dakota state",
    "new haven": "new haven chargers",
    "nicholls state": "nicholls",
    "ok state": "oklahoma state",
    "prairie view": "prairie view a and m",
    "s alabama": "south alabama",
    "s carolina": "south carolina",
    "sac state": "sacramento state",
    "sd state": "south dakota state",
    "sfa": "stephen f austin",
    "southeastern missouri": "southeast missouri state",
    "southern university": "southern",
    "state francis pa": "saint francis",
    "tenn state": "tennessee state",
    "tenn tech": "tennessee tech",
    "texas a and m cc": "texas a and m corpus christi",
    "tn martin": "ut martin",
    "uconn": "connecticut",
    "ucsb": "uc santa barbara",
    "ucsd": "uc san diego",
    "ul monroe": "louisiana monroe",
    "umkc": "kansas city",
    "unc": "north carolina",
    "ut grande valley": "ut rio grande valley",
    "va tech": "virginia tech",
    # "wolves" is ambiguous (multiple teams) -- leave unmatched
    "dolphins": "jacksonville",
    "siu edwardsville": "southern illinois edwardsville",
}


def normalize_team_name(raw: str) -> str:
    """
    Lowercase, strip accents/punctuation, collapse whitespace,
    expand common abbreviations.

    >>> normalize_team_name("N.C. St.")
    'north carolina state'
    """
    # Unicode → ASCII
    s = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    s = s.lower().strip()
    # Replace & with 'and' BEFORE stripping punctuation
    s = s.replace("&", " and ")
    # Remove apostrophes (don't leave a space)
    s = s.replace("'", "").replace("\u2019", "")
    # Expand dotted abbreviations: "n.c." → "nc"
    s = re.sub(r"(?<=\b[a-z])\.(?=[a-z]\.?)", "", s)
    # Strip remaining punctuation except spaces
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    # Apply canonical replacements
    for pattern, repl in _REPLACEMENTS:
        s = re.sub(pattern, repl, s)
    # Final collapse
    return re.sub(r"\s+", " ", s).strip()


def resolve_team(
    session: Session,
    raw_name: str,
    source: str,
    league_id: int = 1,
) -> Team | None:
    """
    Look up a team by alias table first, then by normalized_name.
    If found via a new raw_name, register the alias for next time.
    Returns None if no match.
    """
    norm = normalize_team_name(raw_name)

    # 1. Check alias table within the requested league. Also allow curated
    # seed aliases for provider lookups so ESPN mascot names can resolve.
    alias_sources = [source]
    if source != "seed":
        alias_sources.append("seed")
    alias_rows = session.execute(
        select(TeamAlias)
        .join(Team, Team.id == TeamAlias.team_id)
        .where(
            Team.league_id == league_id,
            TeamAlias.source.in_(alias_sources),
            TeamAlias.alias == norm,
        )
    ).scalars().all()

    if len(alias_rows) == 1:
        team = alias_rows[0].team
        if alias_rows[0].source != source:
            session.add(TeamAlias(team_id=team.id, alias=norm, source=source))
            session.flush()
        return team

    # 2. Fall back to normalized_name on teams table
    team = session.execute(
        select(Team).where(
            Team.league_id == league_id,
            Team.normalized_name == norm,
        )
    ).scalar_one_or_none()

    if team:
        # Register alias so future lookups are fast
        session.add(TeamAlias(team_id=team.id, alias=norm, source=source))
        session.flush()
        return team

    # 3. Try hard-alias map (Action Network abbreviations, etc.)
    canonical = _HARD_ALIASES.get(norm)
    if canonical:
        team = session.execute(
            select(Team).where(
                Team.league_id == league_id,
                Team.normalized_name == canonical,
            )
        ).scalar_one_or_none()
        if team:
            # Cache in alias table for next time
            session.add(TeamAlias(team_id=team.id, alias=norm, source=source))
            session.flush()
            return team

    return None


def get_or_create_team(
    session: Session,
    raw_name: str,
    source: str,
    league_id: int = 1,
) -> Team:
    """Resolve team; if not found, create a new team row + alias."""
    team = resolve_team(session, raw_name, source, league_id)
    if team:
        return team

    norm = normalize_team_name(raw_name)
    team = Team(league_id=league_id, name=raw_name, normalized_name=norm)
    session.add(team)
    session.flush()
    session.add(TeamAlias(team_id=team.id, alias=norm, source=source))
    session.flush()
    return team


def match_event(
    session: Session,
    team_a_name: str,
    team_b_name: str,
    approx_start_utc: datetime,
    source: str,
    tolerance_min: int = 15,
    league_id: int = 1,
) -> Event | None:
    """
    Match a (team_a, team_b, ~start_time) tuple to an existing event.
    Order-insensitive: (A vs B) matches (B vs A).
    Returns None if ambiguous or not found.
    """
    team_a = resolve_team(session, team_a_name, source, league_id)
    team_b = resolve_team(session, team_b_name, source, league_id)
    if not team_a or not team_b:
        return None

    ids = {team_a.id, team_b.id}
    window_lo = approx_start_utc - timedelta(minutes=tolerance_min)
    window_hi = approx_start_utc + timedelta(minutes=tolerance_min)

    # Find events matching both teams (home/away in either order) within window
    stmt = (
        select(Event)
        .where(
            Event.league_id == league_id,
            Event.start_time_utc.between(window_lo, window_hi),
            # Either (home=A, away=B) or (home=B, away=A)
            (
                (Event.home_team_id.in_(ids)) & (Event.away_team_id.in_(ids))
            ),
        )
    )
    results = session.execute(stmt).scalars().all()

    # Verify exactly one match and that it contains both teams
    matches = [
        e for e in results if {e.home_team_id, e.away_team_id} == ids
    ]
    return matches[0] if len(matches) == 1 else None


# ── Odds math ───────────────────────────────────────────────────

def american_to_implied(price: int) -> float:
    """
    Convert American odds to raw implied probability (includes vig).

    >>> round(american_to_implied(-110), 4)
    0.5238
    >>> round(american_to_implied(+150), 4)
    0.4
    """
    if price < 0:
        return abs(price) / (abs(price) + 100)
    return 100 / (price + 100)


def remove_vig(prob_a: float, prob_b: float) -> tuple[float, float]:
    """
    Remove vig from a two-sided market by normalizing to sum=1.
    Returns (fair_a, fair_b).
    """
    total = prob_a + prob_b
    if total == 0:
        return 0.5, 0.5
    return prob_a / total, prob_b / total
