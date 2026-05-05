"""
FastAPI read-only API for DK NCAAB research UI.

All endpoints are GET-only — no mutations.

Start:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func, or_, and_
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.orm import Session, selectinload

from api.deps import get_db
from api.schemas import (
    BattingOrderStabilityRow,
    BoardGame,
    BullpenUsageRow,
    BoardLineOption,
    BoardResponse,
    BoardSplitSummary,
    GameListResponse,
    GameSummary,
    GameDetailSummary,
    GameResearchBatchResponse,
    GameResearchResponse,
    GameTimeseries,
    LinesSnapshot,
    PlayerStatRow,
    TeamOut,
    TeamListResponse,
    TeamResearchMetrics,
    StandingsResponse,
    StandingsRow,
    TeamGameRow,
    TeamHistoryResponse,
    SnapshotOut,
    TimeseriesPoint,
    SplitsTimeseriesPoint,
    ModelPanelResponse,
    ModelSignal,
    EntryEvArtifactLatestResponse,
    EnvironmentContext,
    EventOddsHistoryPoint,
    MlbReadinessEvent,
    MlbMarketReadinessResponse,
    MlbEvidenceGrowthLatestResponse,
    MlbReadinessResponse,
    MlbReadinessSummary,
    MlbStarterReadiness,
    MatchupStatRow,
    MarketContextRow,
    PlayerPropInsightRow,
    RecentLineResultPoint,
    RecentPriceResultPoint,
    RecentStatPoint,
    SettledMarketHistoryPoint,
    WhyThisLineFactor,
    PropMarketRegistryResponse,
    PropMarketRegistryRow,
    BacktestSummaryResponse,
    BacktestStrategyResult,
    StarterContext,
    PipelineStatus,
    TeamTrendContext,
    TeamLineEvidenceRow,
    IngestionRunOut,
)
from api.services.line_evidence import build_line_evidence_status_rows
from api.services.line_thesis import build_line_thesis_rows
from api.services.slate_intelligence import build_slate_intelligence
from dk_ncaab.db.models import (
    Event, Team, EventResult, OddsQuote, SplitsQuote, League,
    EventOddsQuote, EventProviderKey, KenPomRating, APRanking, MlbPlayerGameLog,
    MlbEnvironmentSnapshot, MlbEventVenue, MlbProbableStarter, MlbTeamGameLog,
    MlbParkFactor, MlbVenue, Player,
)
from dk_ncaab.collectors.mlb_wind import derive_field_wind
from dk_ncaab.config.props import prop_market_spec, prop_market_specs_for_sport
from dk_ncaab.config.settings import get_settings
from dk_ncaab.config.sports import get_sport, league_key_for_sport, sport_for_league_key, ui_sport_keys
from dk_ncaab.collectors.odds_api import OddsUsageSummary, get_odds_usage_summary
from dk_ncaab.etl.snapshots import get_snapshot_set
from dk_ncaab.etl.features import build_features
from dk_ncaab.etl.normalize import normalize_team_name

log = logging.getLogger(__name__)

_RUNS_FILE = Path("artifacts/state/runs.jsonl")

_SETTINGS = get_settings()

app = FastAPI(
    title="Lock-N-Key Sports Research API",
    description="Read-only API for private multi-sport odds research.",
    version="0.2.0",
    docs_url="/docs" if _SETTINGS.api.enable_docs else None,
    redoc_url="/redoc" if _SETTINGS.api.enable_docs else None,
    openapi_url="/openapi.json" if _SETTINGS.api.enable_docs else None,
)

_BOARD_LINE_COMBOS = [
    ("spread", "home"),
    ("spread", "away"),
    ("total", "over"),
    ("total", "under"),
    ("moneyline", "home"),
    ("moneyline", "away"),
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_SETTINGS.api.allowed_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── Helpers ─────────────────────────────────────────────────────

def _league_key_for_request_sport(sport: str) -> str:
    try:
        spec = get_sport(sport)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unsupported sport: {sport}")
    if not spec.ui_enabled:
        raise HTTPException(status_code=400, detail=f"Sport is not enabled in the UI: {sport}")
    return league_key_for_sport(sport)


def _get_pregame_lines(
    session: Session,
    event_id: int,
    start_time_utc: datetime,
) -> tuple[LinesSnapshot | None, LinesSnapshot | None]:
    """
    Return (open_lines, close_lines) using ONLY pre-game quotes.

    open  = earliest collected_at_utc  WHERE collected_at_utc < start_time_utc
    close = latest  collected_at_utc   WHERE collected_at_utc < start_time_utc

    For upcoming games that haven't started yet, "close" = the most recent snapshot.
    """
    open_d: dict = {}
    close_d: dict = {}

    combos = [
        ("spread", "home",  "spread",         "spread_price"),
        ("spread", "away",  None,             None),
        ("total",  "over",  "total",          "total_over_price"),
        ("total",  "under", None,             "total_under_price"),
        ("moneyline", "home", None,           "ml_home"),
        ("moneyline", "away", None,           "ml_away"),
    ]

    for market, side, line_key, price_key in combos:
        base_filter = [
            OddsQuote.event_id == event_id,
            OddsQuote.book == "draftkings",
            OddsQuote.market == market,
            OddsQuote.side == side,
            OddsQuote.collected_at_utc < start_time_utc,
        ]

        # OPEN = earliest pre-game quote
        stmt_open = (
            select(OddsQuote)
            .where(*base_filter)
            .order_by(OddsQuote.collected_at_utc.asc())
            .limit(1)
        )
        q_open = session.execute(stmt_open).scalar_one_or_none()

        # CLOSE = latest pre-game quote
        stmt_close = (
            select(OddsQuote)
            .where(*base_filter)
            .order_by(OddsQuote.collected_at_utc.desc())
            .limit(1)
        )
        q_close = session.execute(stmt_close).scalar_one_or_none()

        for q, d in [(q_open, open_d), (q_close, close_d)]:
            if q:
                if line_key and q.line is not None:
                    d[line_key] = q.line
                if price_key:
                    d[price_key] = q.price_american

    open_snap = LinesSnapshot(**open_d) if open_d else None
    close_snap = LinesSnapshot(**close_d) if close_d else None
    return open_snap, close_snap


def _team_out(session: Session, team_id: int) -> TeamOut:
    t = session.get(Team, team_id)
    return TeamOut(id=team_id, name=t.name if t else "Unknown")


def _parse_date(date_str: str | None) -> tuple[datetime, datetime]:
    """Parse YYYY-MM-DD into (day_start_utc, day_end_utc) using ET boundaries.

    When a user picks 'Feb 14' they mean midnight-to-midnight Eastern,
    not midnight-to-midnight UTC.
    """
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")

    if date_str:
        naive = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        naive = datetime.now(_ET).replace(hour=0, minute=0, second=0, microsecond=0)
        # already localized below

    # Build midnight-to-midnight in ET, then convert to UTC for the query
    day_start_et = naive.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=_ET)
    day_end_et = day_start_et + timedelta(days=1)
    return day_start_et.astimezone(timezone.utc), day_end_et.astimezone(timezone.utc)


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_minutes(now: datetime, dt: datetime | None) -> int | None:
    dt = _ensure_utc(dt)
    if dt is None:
        return None
    return int((now - dt).total_seconds() // 60)


def _latest_quote(
    session: Session,
    event_id: int,
    market: str,
    side: str,
) -> OddsQuote | None:
    stmt = (
        select(OddsQuote)
        .where(
            OddsQuote.event_id == event_id,
            OddsQuote.book == "draftkings",
            OddsQuote.market == market,
            OddsQuote.side == side,
        )
        .order_by(OddsQuote.collected_at_utc.desc(), OddsQuote.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _open_quote(
    session: Session,
    event_id: int,
    market: str,
    side: str,
    start_time_utc: datetime,
) -> OddsQuote | None:
    stmt = (
        select(OddsQuote)
        .where(
            OddsQuote.event_id == event_id,
            OddsQuote.book == "draftkings",
            OddsQuote.market == market,
            OddsQuote.side == side,
            OddsQuote.collected_at_utc < start_time_utc,
        )
        .order_by(OddsQuote.collected_at_utc.asc(), OddsQuote.id.asc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _event_quote_at_anchor(
    quotes: list[EventOddsQuote],
    start_time_utc: datetime,
    anchor: str,
) -> EventOddsQuote | None:
    start = _ensure_utc(start_time_utc) or datetime.now(timezone.utc)
    if anchor == "OPEN":
        eligible = [quote for quote in quotes if (_ensure_utc(quote.collected_at_utc) or start) < start]
        if not eligible:
            return None
        return min(eligible, key=lambda quote: (_ensure_utc(quote.collected_at_utc), quote.id))

    offset_minutes = {"T60": 60, "T30": 30, "CLOSE": 0}[anchor]
    cutoff = start - timedelta(minutes=offset_minutes)
    eligible = [quote for quote in quotes if (_ensure_utc(quote.collected_at_utc) or start) < cutoff]
    if not eligible:
        return None
    return max(eligible, key=lambda quote: (_ensure_utc(quote.collected_at_utc), quote.id))


def _event_quote_line(quote_by_side: dict[str, EventOddsQuote]) -> float | None:
    quote = quote_by_side.get("over") or quote_by_side.get("under") or quote_by_side.get("yes") or quote_by_side.get("no")
    return float(quote.line) if quote and quote.line is not None else None


def _event_quote_history_points(
    quote_by_side: dict[str, list[EventOddsQuote]],
    *,
    limit: int = 8,
) -> list[EventOddsHistoryPoint]:
    by_time: dict[datetime, dict[str, EventOddsQuote]] = {}
    for side, quotes in quote_by_side.items():
        for quote in quotes:
            collected = _ensure_utc(quote.collected_at_utc)
            if collected is None:
                continue
            by_time.setdefault(collected, {})[side] = quote

    points: list[EventOddsHistoryPoint] = []
    for collected_at in sorted(by_time.keys())[-limit:]:
        side_bucket = by_time[collected_at]
        points.append(
            EventOddsHistoryPoint(
                collected_at_utc=collected_at,
                label=collected_at.strftime("%m/%d %I:%M %p").replace(" 0", " "),
                line=_event_quote_line(side_bucket),
                over_price_american=(
                    side_bucket["over"].price_american if side_bucket.get("over") else None
                ),
                under_price_american=(
                    side_bucket["under"].price_american if side_bucket.get("under") else None
                ),
            )
        )
    return points


def _event_quote_snapshots(
    quote_by_side: dict[str, list[EventOddsQuote]],
    *,
    start_time_utc: datetime,
    now: datetime,
    status: str,
) -> tuple[
    dict[str, EventOddsQuote],
    dict[str, EventOddsQuote],
    dict[str, EventOddsQuote],
    dict[str, EventOddsQuote],
    str | None,
    dict[str, EventOddsQuote],
]:
    latest_by_side = {
        side: max(quotes, key=lambda quote: (_ensure_utc(quote.collected_at_utc), quote.id))
        for side, quotes in quote_by_side.items()
        if quotes
    }
    open_by_side = {
        side: quote
        for side, quotes in quote_by_side.items()
        if (quote := _event_quote_at_anchor(quotes, start_time_utc, "OPEN")) is not None
    }
    t60_by_side = {
        side: quote
        for side, quotes in quote_by_side.items()
        if (quote := _event_quote_at_anchor(quotes, start_time_utc, "T60")) is not None
    }
    t30_by_side = {
        side: quote
        for side, quotes in quote_by_side.items()
        if (quote := _event_quote_at_anchor(quotes, start_time_utc, "T30")) is not None
    }

    start = _ensure_utc(start_time_utc) or now
    best_entry_anchor: str | None
    best_entry_by_side: dict[str, EventOddsQuote]
    if status in {"live", "final", "cancelled", "postponed"} or now >= start - timedelta(minutes=30):
        best_entry_anchor = "T30"
        best_entry_by_side = t30_by_side or t60_by_side or open_by_side
        if not best_entry_by_side and status == "final":
            best_entry_anchor = "OPEN"
    elif now >= start - timedelta(minutes=60):
        best_entry_anchor = "T60"
        best_entry_by_side = t60_by_side or open_by_side
    else:
        best_entry_anchor = "OPEN"
        best_entry_by_side = open_by_side

    if not best_entry_by_side:
        best_entry_anchor = None

    return latest_by_side, open_by_side, t60_by_side, t30_by_side, best_entry_anchor, best_entry_by_side


def _line_label(home: TeamOut, away: TeamOut, market: str, side: str) -> tuple[str, str | None]:
    if market == "spread":
        team = home.name if side == "home" else away.name
        return f"{team} spread", team
    if market == "moneyline":
        team = home.name if side == "home" else away.name
        return f"{team} moneyline", team
    if market == "total":
        return side.title(), None
    return f"{market} {side}", None


def _quote_pair_to_board_line(
    ev: Event,
    home: TeamOut,
    away: TeamOut,
    market: str,
    side: str,
    latest: OddsQuote | None,
    opened: OddsQuote | None,
    t60: OddsQuote | None,
    t30: OddsQuote | None,
    now: datetime,
) -> BoardLineOption | None:
    if not latest:
        return None

    collected = _ensure_utc(latest.collected_at_utc)
    age_min = _age_minutes(now, collected)
    is_live = bool(collected and collected >= _ensure_utc(ev.start_time_utc))
    stale_limit = 15 if ev.status == "live" or is_live else 60
    label, team_name = _line_label(home, away, market, side)
    start = _ensure_utc(ev.start_time_utc) or now
    if ev.status in {"live", "final", "cancelled", "postponed"} or now >= start - timedelta(minutes=30):
        best_entry_anchor = "T30"
        best_entry_quote = t30 or t60 or opened
        if best_entry_quote is None and ev.status == "final":
            best_entry_anchor = "OPEN"
    elif now >= start - timedelta(minutes=60):
        best_entry_anchor = "T60"
        best_entry_quote = t60 or opened
    else:
        best_entry_anchor = "OPEN"
        best_entry_quote = opened

    if best_entry_quote is None:
        best_entry_anchor = None

    return BoardLineOption(
        book=latest.book,
        market=market,
        side=side,
        label=label,
        team_name=team_name,
        line=latest.line,
        price_american=latest.price_american,
        implied_probability=latest.implied_probability,
        collected_at_utc=collected,
        is_live=is_live,
        is_stale=age_min is None or age_min > stale_limit,
        open_line=opened.line if opened else None,
        open_price_american=opened.price_american if opened else None,
        best_entry_anchor=best_entry_anchor,
        best_entry_line=best_entry_quote.line if best_entry_quote else None,
        best_entry_price_american=best_entry_quote.price_american if best_entry_quote else None,
        best_entry_implied_probability=(
            best_entry_quote.implied_probability if best_entry_quote else None
        ),
        best_entry_quote_utc=_ensure_utc(best_entry_quote.collected_at_utc) if best_entry_quote else None,
        price_move_implied_from_open=(
            latest.implied_probability - opened.implied_probability
            if opened
            and latest.implied_probability is not None
            and opened.implied_probability is not None
            else None
        ),
        price_move_american_from_open=(
            latest.price_american - opened.price_american
            if opened
            else None
        ),
        number_move_from_open=(
            latest.line - opened.line
            if opened and latest.line is not None and opened.line is not None
            else None
        ),
    )


def _quote_to_board_line(
    session: Session,
    ev: Event,
    home: TeamOut,
    away: TeamOut,
    market: str,
    side: str,
    now: datetime,
) -> BoardLineOption | None:
    latest = _latest_quote(session, ev.id, market, side)
    opened = _open_quote(session, ev.id, market, side, ev.start_time_utc)
    start = _ensure_utc(ev.start_time_utc) or now
    t60 = (
        session.execute(
            select(OddsQuote)
            .where(
                OddsQuote.event_id == ev.id,
                OddsQuote.book == "draftkings",
                OddsQuote.market == market,
                OddsQuote.side == side,
                OddsQuote.collected_at_utc < start - timedelta(minutes=60),
            )
            .order_by(OddsQuote.collected_at_utc.desc(), OddsQuote.id.desc())
            .limit(1)
        ).scalar_one_or_none()
    )
    t30 = (
        session.execute(
            select(OddsQuote)
            .where(
                OddsQuote.event_id == ev.id,
                OddsQuote.book == "draftkings",
                OddsQuote.market == market,
                OddsQuote.side == side,
                OddsQuote.collected_at_utc < start - timedelta(minutes=30),
            )
            .order_by(OddsQuote.collected_at_utc.desc(), OddsQuote.id.desc())
            .limit(1)
        ).scalar_one_or_none()
    )
    return _quote_pair_to_board_line(
        ev,
        home,
        away,
        market,
        side,
        latest,
        opened,
        t60,
        t30,
        now,
    )


def _latest_split_summary(
    session: Session,
    event_id: int,
    market: str,
    side: str,
) -> BoardSplitSummary | None:
    stmt = (
        select(SplitsQuote)
        .where(
            SplitsQuote.event_id == event_id,
            SplitsQuote.market == market,
            SplitsQuote.side == side,
        )
        .order_by(SplitsQuote.collected_at_utc.desc(), SplitsQuote.id.desc())
        .limit(1)
    )
    row = session.execute(stmt).scalar_one_or_none()
    if not row:
        return None
    return BoardSplitSummary(
        market=market,
        side=side,
        bets_pct=row.bets_pct,
        handle_pct=row.handle_pct,
        collected_at_utc=_ensure_utc(row.collected_at_utc),
    )


def _prefetch_board_context(
    session: Session,
    events: list[Event],
) -> tuple[
    dict[int, TeamOut],
    dict[tuple[int, str, str], OddsQuote],
    dict[tuple[int, str, str], OddsQuote],
    dict[tuple[int, str, str], OddsQuote],
    dict[tuple[int, str, str], OddsQuote],
    dict[tuple[int, str, str], SplitsQuote],
]:
    """Fetch board teams, odds, and splits in batches for a compact payload."""
    if not events:
        return {}, {}, {}, {}, {}, {}

    event_by_id = {ev.id: ev for ev in events}
    event_ids = list(event_by_id)
    team_ids = sorted({ev.home_team_id for ev in events} | {ev.away_team_id for ev in events})

    teams = {
        team.id: TeamOut(id=team.id, name=team.name)
        for team in session.execute(select(Team).where(Team.id.in_(team_ids))).scalars()
    }
    for team_id in team_ids:
        teams.setdefault(team_id, TeamOut(id=team_id, name="Unknown"))

    latest_quotes: dict[tuple[int, str, str], OddsQuote] = {}
    open_quotes: dict[tuple[int, str, str], OddsQuote] = {}
    t60_quotes: dict[tuple[int, str, str], OddsQuote] = {}
    t30_quotes: dict[tuple[int, str, str], OddsQuote] = {}
    quote_stmt = (
        select(OddsQuote)
        .where(OddsQuote.event_id.in_(event_ids))
        .where(OddsQuote.book == "draftkings")
        .order_by(
            OddsQuote.event_id.asc(),
            OddsQuote.market.asc(),
            OddsQuote.side.asc(),
            OddsQuote.collected_at_utc.asc(),
            OddsQuote.id.asc(),
        )
    )
    for quote in session.execute(quote_stmt).scalars():
        key = (quote.event_id, quote.market, quote.side)
        if (quote.market, quote.side) not in _BOARD_LINE_COMBOS:
            continue

        previous_latest = latest_quotes.get(key)
        if (
            previous_latest is None
            or _ensure_utc(quote.collected_at_utc) > _ensure_utc(previous_latest.collected_at_utc)
            or (
                _ensure_utc(quote.collected_at_utc) == _ensure_utc(previous_latest.collected_at_utc)
                and quote.id > previous_latest.id
            )
        ):
            latest_quotes[key] = quote

        event = event_by_id.get(quote.event_id)
        if not event:
            continue
        collected = _ensure_utc(quote.collected_at_utc)
        start = _ensure_utc(event.start_time_utc)
        if not collected or not start or collected >= start:
            continue
        if key not in open_quotes:
            open_quotes[key] = quote
        if collected < start - timedelta(minutes=60):
            t60_quotes[key] = quote
        if collected < start - timedelta(minutes=30):
            t30_quotes[key] = quote

    latest_splits: dict[tuple[int, str, str], SplitsQuote] = {}
    split_stmt = (
        select(SplitsQuote)
        .where(SplitsQuote.event_id.in_(event_ids))
        .order_by(
            SplitsQuote.event_id.asc(),
            SplitsQuote.market.asc(),
            SplitsQuote.side.asc(),
            SplitsQuote.collected_at_utc.asc(),
            SplitsQuote.id.asc(),
        )
    )
    for split in session.execute(split_stmt).scalars():
        key = (split.event_id, split.market, split.side)
        if (split.market, split.side) not in _BOARD_LINE_COMBOS:
            continue
        previous = latest_splits.get(key)
        if (
            previous is None
            or _ensure_utc(split.collected_at_utc) > _ensure_utc(previous.collected_at_utc)
            or (
                _ensure_utc(split.collected_at_utc) == _ensure_utc(previous.collected_at_utc)
                and split.id > previous.id
            )
        ):
            latest_splits[key] = split

    return teams, latest_quotes, open_quotes, t60_quotes, t30_quotes, latest_splits


def _split_to_board_summary(split: SplitsQuote | None) -> BoardSplitSummary | None:
    if not split:
        return None
    return BoardSplitSummary(
        market=split.market,
        side=split.side,
        bets_pct=split.bets_pct,
        handle_pct=split.handle_pct,
        collected_at_utc=_ensure_utc(split.collected_at_utc),
    )


def _build_board_game(session: Session, ev: Event, now: datetime) -> BoardGame:
    teams, latest_quotes, open_quotes, t60_quotes, t30_quotes, latest_splits = _prefetch_board_context(session, [ev])
    return _build_board_game_from_prefetch(
        ev,
        now,
        teams,
        latest_quotes,
        open_quotes,
        t60_quotes,
        t30_quotes,
        latest_splits,
    )


def _build_board_game_from_prefetch(
    ev: Event,
    now: datetime,
    teams: dict[int, TeamOut],
    latest_quotes: dict[tuple[int, str, str], OddsQuote],
    open_quotes: dict[tuple[int, str, str], OddsQuote],
    t60_quotes: dict[tuple[int, str, str], OddsQuote],
    t30_quotes: dict[tuple[int, str, str], OddsQuote],
    latest_splits: dict[tuple[int, str, str], SplitsQuote],
    oof_rows_by_market: dict[str, int] | None = None,
) -> BoardGame:
    home = teams.get(ev.home_team_id, TeamOut(id=ev.home_team_id, name="Unknown"))
    away = teams.get(ev.away_team_id, TeamOut(id=ev.away_team_id, name="Unknown"))
    league_key = ev.league.key if ev.league else "unknown"
    try:
        sport = sport_for_league_key(league_key)
    except ValueError:
        sport = league_key

    lines = [
        line
        for market, side in _BOARD_LINE_COMBOS
        if (
            line := _quote_pair_to_board_line(
                ev,
                home,
                away,
                market,
                side,
                latest_quotes.get((ev.id, market, side)),
                open_quotes.get((ev.id, market, side)),
                t60_quotes.get((ev.id, market, side)),
                t30_quotes.get((ev.id, market, side)),
                now,
            )
        )
    ]
    splits = [
        split
        for market, side in _BOARD_LINE_COMBOS
        if (split := _split_to_board_summary(latest_splits.get((ev.id, market, side))))
    ]

    latest_quote_utc = max(
        (line.collected_at_utc for line in lines if line.collected_at_utc is not None),
        default=None,
    )
    age_min = _age_minutes(now, latest_quote_utc)
    stale_limit = 15 if ev.status == "live" else 60
    flags: list[str] = []
    if not lines:
        flags.append("No odds yet")
    if age_min is None or age_min > stale_limit:
        flags.append("Odds stale")
    if not splits:
        flags.append("No public splits")
    if ev.status in {"upcoming", "live"} and ev.result is None:
        flags.append("No final result")

    return BoardGame(
        event_id=ev.id,
        sport=sport,
        league_key=league_key,
        start_time_utc=ev.start_time_utc,
        status=ev.status,
        home_team=home,
        away_team=away,
        home_score=ev.result.home_score if ev.result else None,
        away_score=ev.result.away_score if ev.result else None,
        latest_quote_utc=latest_quote_utc,
        odds_age_min=age_min,
        odds_stale=age_min is None or age_min > stale_limit,
        lines=lines,
        split_summary=splits,
        markets_available=sorted({line.market for line in lines}),
        flags=flags,
        slate_intelligence=build_slate_intelligence(
            start_time_utc=ev.start_time_utc,
            status=ev.status,
            odds_age_min=age_min,
            odds_stale=age_min is None or age_min > stale_limit,
            lines=lines,
            split_summary=splits,
            flags=flags,
            oof_rows_by_market=oof_rows_by_market,
            now=now,
        ),
    )


def _latest_oof_rows_by_market() -> dict[str, int]:
    try:
        from dk_ncaab.analysis.oof_entry_ev import read_latest_entry_ev

        payload = read_latest_entry_ev() or {}
    except Exception:
        log.exception("Failed to read latest OOF entry-EV artifact for board intelligence")
        return {}
    return {
        str(market): int(count or 0)
        for market, count in (payload.get("rows_predicted_by_market") or {}).items()
    }


def _build_snapshot_map(session: Session, event_id: int) -> dict[str, list[SnapshotOut]]:
    combos = [
        ("moneyline", "home"), ("moneyline", "away"),
        ("spread", "home"), ("spread", "away"),
        ("total", "over"), ("total", "under"),
    ]
    snapshots: dict[str, list[SnapshotOut]] = {}
    for market, side in combos:
        key = f"{market}_{side}"
        ss = get_snapshot_set(session, event_id, market, side)
        snaps = []
        for anchor_name, snap in [
            ("OPEN", ss.OPEN), ("T60", ss.T60), ("T30", ss.T30), ("CLOSE", ss.CLOSE),
        ]:
            if snap:
                snaps.append(SnapshotOut(
                    anchor=anchor_name,
                    implied_probability=snap.implied_probability,
                    line=snap.line,
                    price_american=snap.price_american,
                    collected_at_utc=_ensure_utc(snap.collected_at_utc),
                ))
        snapshots[key] = snaps
    return snapshots


def _avg_numeric(values: list[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 3)


def _derived_implied_team_total(total: float | None, team_spread: float | None) -> float | None:
    if total is None or team_spread is None:
        return None
    return round((float(total) - float(team_spread)) / 2.0, 3)


def _summarize_result_record(values: list[str | None], *, over_key: str = "O", under_key: str = "U") -> str | None:
    clean = [value for value in values if value in {over_key, under_key, "P"}]
    if not clean:
        return None
    over_count = sum(1 for value in clean if value == over_key)
    under_count = sum(1 for value in clean if value == under_key)
    push_count = sum(1 for value in clean if value == "P")
    return f"{over_count}-{under_count}-{push_count}"


def _summarize_win_loss_record(values: list[bool | None]) -> str | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    wins = sum(1 for value in clean if value)
    losses = sum(1 for value in clean if not value)
    return f"{wins}-{losses}"


def _american_to_implied(price_american: int | None) -> float | None:
    if price_american is None:
        return None
    price = int(price_american)
    if price < 0:
        return round(abs(price) / (abs(price) + 100.0), 3)
    return round(100.0 / (price + 100.0), 3)


def _result_against_line(
    value: float | None,
    line: float | None,
    *,
    over_key: str = "O",
    under_key: str = "U",
) -> tuple[str | None, float | None]:
    if value is None or line is None:
        return None, None
    margin = round(float(value) - float(line), 3)
    if margin > 0:
        return over_key, margin
    if margin < 0:
        return under_key, margin
    return "P", margin


def _recent_line_result_summary(
    values: list[tuple[datetime | None, str, float]],
    line: float | None,
) -> tuple[list[RecentLineResultPoint], str | None, float | None]:
    if line is None:
        return [], None, None

    points: list[RecentLineResultPoint] = []
    margins: list[float] = []
    outcomes: list[str] = []
    for game_date_utc, label, value in values:
        result, margin = _result_against_line(value, line)
        if result is None or margin is None:
            continue
        points.append(
            RecentLineResultPoint(
                game_date_utc=_ensure_utc(game_date_utc),
                label=label,
                value=round(float(value), 3),
                line=round(float(line), 3),
                margin_vs_line=margin,
                result=result,
            )
        )
        margins.append(margin)
        outcomes.append(result)

    return points, _summarize_result_record(outcomes), _avg_numeric(margins)


def _market_line_result_summary(
    values: list[tuple[datetime | None, str, float, float | None]],
) -> tuple[list[RecentLineResultPoint], str | None, float | None]:
    points: list[RecentLineResultPoint] = []
    margins: list[float] = []
    outcomes: list[str] = []
    for game_date_utc, label, value, line in values:
        result, margin = _result_against_line(value, line)
        if result is None or margin is None or line is None:
            continue
        points.append(
            RecentLineResultPoint(
                game_date_utc=_ensure_utc(game_date_utc),
                label=label,
                value=round(float(value), 3),
                line=round(float(line), 3),
                margin_vs_line=margin,
                result=result,
            )
        )
        margins.append(margin)
        outcomes.append(result)
    return points, _summarize_result_record(outcomes), _avg_numeric(margins)


def _settled_market_history_summary(
    values: list[
        tuple[
            int | None,
            datetime | None,
            str,
            str | None,
            float,
            dict[str, EventOddsQuote],
        ]
    ],
) -> tuple[list[SettledMarketHistoryPoint], str | None, float | None]:
    points: list[SettledMarketHistoryPoint] = []
    margins: list[float] = []
    outcomes: list[str] = []
    for event_id, game_date_utc, label, opponent_name, value, quote_by_side in values:
        line = _event_quote_line(quote_by_side)
        result, margin = _result_against_line(value, line)
        if result is None or margin is None or line is None:
            continue
        points.append(
            SettledMarketHistoryPoint(
                event_id=event_id,
                game_date_utc=_ensure_utc(game_date_utc),
                label=label,
                opponent_name=opponent_name,
                value=round(float(value), 3),
                line=round(float(line), 3),
                over_price_american=quote_by_side.get("over").price_american if quote_by_side.get("over") else None,
                under_price_american=quote_by_side.get("under").price_american if quote_by_side.get("under") else None,
                margin_vs_line=margin,
                result=result,
            )
        )
        margins.append(margin)
        outcomes.append(result)
    return points, _summarize_result_record(outcomes), _avg_numeric(margins)


def _latest_event_quote_snapshots_by_event(
    quotes: list[EventOddsQuote],
    event_start_by_id: dict[int, datetime],
) -> dict[int, dict[str, EventOddsQuote]]:
    buckets: dict[int, dict[str, EventOddsQuote]] = {}
    for quote in quotes:
        start = _ensure_utc(event_start_by_id.get(quote.event_id))
        collected = _ensure_utc(quote.collected_at_utc)
        if start is None or collected is None or collected >= start:
            continue
        bucket = buckets.setdefault(quote.event_id, {})
        existing = bucket.get(quote.side)
        if existing is None:
            bucket[quote.side] = quote
            continue
        existing_collected = _ensure_utc(existing.collected_at_utc)
        if existing_collected is None or collected > existing_collected or (
            collected == existing_collected and quote.id > existing.id
        ):
            bucket[quote.side] = quote
    return buckets


def _recent_team_game_total_samples(
    team_metrics: dict[str, TeamResearchMetrics],
    *,
    limit: int = 6,
) -> list[tuple[datetime | None, str, float, float | None]]:
    rows: list[tuple[datetime | None, str, float, float | None]] = []
    for side in ["away", "home"]:
        metrics = team_metrics.get(side)
        if metrics is None:
            continue
        team_tag = metrics.team.name.split()[0]
        for game in metrics.recent_games:
            if game.status != "final" or game.game_total is None:
                continue
            game_date = _ensure_utc(game.start_time_utc)
            rows.append(
                (
                    game_date,
                    f"{team_tag} {game_date.strftime('%m/%d') if game_date else '?'}",
                    float(game.game_total),
                    float(game.close_total) if game.close_total is not None else None,
                )
            )
    rows.sort(
        key=lambda row: row[0] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return rows[:limit]


def _recent_moneyline_price_summary(
    recent_games: list[TeamGameRow],
    *,
    limit: int = 5,
) -> tuple[list[RecentPriceResultPoint], str | None, float | None, int | None, float | None]:
    sample = [
        row
        for row in recent_games
        if row.status == "final" and row.close_ml is not None and row.won is not None
    ][:limit]
    if not sample:
        return [], None, None, None, None

    points: list[RecentPriceResultPoint] = []
    prices: list[float] = []
    implieds: list[float] = []
    wins: list[bool] = []
    for row in sample:
        game_date = _ensure_utc(row.start_time_utc)
        implied = _american_to_implied(row.close_ml)
        points.append(
            RecentPriceResultPoint(
                game_date_utc=game_date,
                label=game_date.strftime("%m/%d") if game_date else "?",
                price_american=int(row.close_ml),
                implied_probability=implied,
                result="W" if row.won else "L",
            )
        )
        prices.append(float(row.close_ml))
        if implied is not None:
            implieds.append(implied)
        wins.append(bool(row.won))

    avg_price = int(round(sum(prices) / len(prices))) if prices else None
    avg_implied = _avg_numeric(implieds)
    win_rate = round(sum(1 for won in wins if won) / len(wins), 3) if wins else None
    return points, _summarize_win_loss_record(wins), win_rate, avg_price, avg_implied


def _team_game_row_from_event(session: Session, event: Event, team_id: int) -> TeamGameRow:
    is_home = event.home_team_id == team_id
    opp_id = event.away_team_id if is_home else event.home_team_id
    opponent = _team_out(session, opp_id)
    open_lines, close_lines = _get_pregame_lines(session, event.id, event.start_time_utc)

    team_score = opp_score = None
    won = None
    if event.result:
        team_score = event.result.home_score if is_home else event.result.away_score
        opp_score = event.result.away_score if is_home else event.result.home_score
        won = (
            team_score > opp_score
            if team_score is not None and opp_score is not None
            else None
        )

    open_spread = close_spread = None
    open_total = close_total = None
    open_ml = close_ml = None
    if open_lines:
        open_spread = (
            open_lines.spread if is_home
            else (-open_lines.spread if open_lines.spread is not None else None)
        )
        open_total = open_lines.total
        open_ml = open_lines.ml_home if is_home else open_lines.ml_away
    if close_lines:
        close_spread = (
            close_lines.spread if is_home
            else (-close_lines.spread if close_lines.spread is not None else None)
        )
        close_total = close_lines.total
        close_ml = close_lines.ml_home if is_home else close_lines.ml_away

    game_total = None
    open_implied_team_total = _derived_implied_team_total(open_total, open_spread)
    close_implied_team_total = _derived_implied_team_total(close_total, close_spread)
    open_implied_opp_total = (
        round(float(open_total) - float(open_implied_team_total), 3)
        if open_total is not None and open_implied_team_total is not None
        else None
    )
    close_implied_opp_total = (
        round(float(close_total) - float(close_implied_team_total), 3)
        if close_total is not None and close_implied_team_total is not None
        else None
    )

    spread_result = total_result = team_total_result = None
    team_runs_vs_close_implied = None
    opponent_runs_vs_close_implied = None
    game_total_vs_close_total = None
    if event.status == "final" and team_score is not None and opp_score is not None:
        margin = team_score - opp_score
        game_total = float(team_score + opp_score)
        if close_spread is not None:
            ats_margin = margin + close_spread
            if ats_margin > 0:
                spread_result = "W"
            elif ats_margin < 0:
                spread_result = "L"
            else:
                spread_result = "P"
        if close_total is not None:
            game_total_vs_close_total = round(game_total - float(close_total), 3)
            if game_total > close_total:
                total_result = "O"
            elif game_total < close_total:
                total_result = "U"
            else:
                total_result = "P"
        if close_implied_team_total is not None:
            team_runs_vs_close_implied = round(float(team_score) - close_implied_team_total, 3)
            if float(team_score) > close_implied_team_total:
                team_total_result = "O"
            elif float(team_score) < close_implied_team_total:
                team_total_result = "U"
            else:
                team_total_result = "P"
        if close_implied_opp_total is not None:
            opponent_runs_vs_close_implied = round(float(opp_score) - close_implied_opp_total, 3)

    return TeamGameRow(
        event_id=event.id,
        start_time_utc=event.start_time_utc,
        opponent=opponent,
        is_home=is_home,
        status=event.status,
        team_score=team_score,
        opp_score=opp_score,
        won=won,
        open_spread=open_spread,
        close_spread=close_spread,
        open_total=open_total,
        close_total=close_total,
        open_ml=open_ml,
        close_ml=close_ml,
        game_total=game_total,
        open_implied_team_total=open_implied_team_total,
        close_implied_team_total=close_implied_team_total,
        open_implied_opponent_total=open_implied_opp_total,
        close_implied_opponent_total=close_implied_opp_total,
        team_runs_vs_close_implied=team_runs_vs_close_implied,
        opponent_runs_vs_close_implied=opponent_runs_vs_close_implied,
        game_total_vs_close_total=game_total_vs_close_total,
        spread_result=spread_result,
        total_result=total_result,
        team_total_result=team_total_result,
    )


def _build_team_history_rows(
    session: Session,
    team_id: int,
    limit: int = 8,
    *,
    as_of: datetime | None = None,
) -> list[TeamGameRow]:
    query = (
        select(Event)
        .where(or_(Event.home_team_id == team_id, Event.away_team_id == team_id))
        .order_by(Event.start_time_utc.desc())
        .limit(limit)
    )
    if as_of is not None:
        query = query.where(Event.start_time_utc < as_of)
    events = list(session.execute(query).scalars())
    return [_team_game_row_from_event(session, event, team_id) for event in events]


def _team_research_metrics(
    session: Session,
    team_id: int,
    limit: int = 8,
    *,
    as_of: datetime | None = None,
) -> TeamResearchMetrics:
    team = _team_out(session, team_id)
    rows = _build_team_history_rows(session, team_id, limit=limit, as_of=as_of)
    wins = sum(1 for row in rows if row.status == "final" and row.won is True)
    losses = sum(1 for row in rows if row.status == "final" and row.won is False)
    ats_w = sum(1 for row in rows if row.spread_result == "W")
    ats_l = sum(1 for row in rows if row.spread_result == "L")
    ats_p = sum(1 for row in rows if row.spread_result == "P")
    ou_o = sum(1 for row in rows if row.total_result == "O")
    ou_u = sum(1 for row in rows if row.total_result == "U")
    ou_p = sum(1 for row in rows if row.total_result == "P")

    return TeamResearchMetrics(
        team=team,
        record=f"{wins}-{losses}",
        ats_record=f"{ats_w}-{ats_l}-{ats_p}",
        ou_record=f"{ou_o}-{ou_u}-{ou_p}",
        recent_games=rows,
    )


def _market_context(
    board_game: BoardGame,
    team_metrics: dict[str, TeamResearchMetrics] | None = None,
) -> list[MarketContextRow]:
    split_by_key = {
        (split.market, split.side): split
        for split in board_game.split_summary
    }
    team_metrics = team_metrics or {}
    total_samples = (
        _recent_team_game_total_samples(team_metrics)
        if board_game.sport == "baseball_mlb"
        else []
    )
    rows: list[MarketContextRow] = []
    for line in board_game.lines:
        split = split_by_key.get((line.market, line.side))
        notes: list[str] = []
        if line.is_stale:
            notes.append("stale_odds")
        if line.is_live:
            notes.append("live_price")
        if split and split.bets_pct is not None and split.bets_pct >= 60:
            notes.append("public_bets_lean")
        if split and split.handle_pct is not None and split.bets_pct is not None:
            if abs(split.handle_pct - split.bets_pct) >= 15:
                notes.append("handle_bet_split")
        if (
            split
            and split.bets_pct is not None
            and split.bets_pct >= 60
            and line.price_move_implied_from_open is not None
            and line.price_move_implied_from_open < 0
        ):
            notes.append("possible_reverse_line_move")
        if line.number_move_from_open not in (None, 0):
            notes.append("number_move")
        elif line.price_move_american_from_open not in (None, 0):
            notes.append("price_pressure")
        if line.best_entry_anchor is None:
            notes.append("anchor_missing")

        record_vs_current_line_last_n = None
        avg_margin_vs_current_line_last_n = None
        recent_results_vs_current_line: list[RecentLineResultPoint] = []
        record_vs_market_line_last_n = None
        avg_margin_vs_market_line_last_n = None
        recent_results_vs_market_lines: list[RecentLineResultPoint] = []
        recent_record_last_n = None
        recent_win_rate_last_n = None
        avg_market_price_american_last_n = None
        avg_market_implied_probability_last_n = None
        current_implied_delta_vs_avg_last_n = None
        recent_results_vs_market_prices: list[RecentPriceResultPoint] = []

        if board_game.sport == "baseball_mlb" and line.market == "total" and line.side == "over":
            (
                recent_results_vs_current_line,
                record_vs_current_line_last_n,
                avg_margin_vs_current_line_last_n,
            ) = _recent_line_result_summary(
                [(game_date, label, value) for game_date, label, value, _ in total_samples],
                float(line.line) if line.line is not None else None,
            )
            (
                recent_results_vs_market_lines,
                record_vs_market_line_last_n,
                avg_margin_vs_market_line_last_n,
            ) = _market_line_result_summary(total_samples)
        elif board_game.sport == "baseball_mlb" and line.market == "moneyline" and line.side in {"away", "home"}:
            metrics = team_metrics.get(line.side)
            if metrics is not None:
                (
                    recent_results_vs_market_prices,
                    recent_record_last_n,
                    recent_win_rate_last_n,
                    avg_market_price_american_last_n,
                    avg_market_implied_probability_last_n,
                ) = _recent_moneyline_price_summary(metrics.recent_games)
                current_implied = (
                    float(line.implied_probability)
                    if line.implied_probability is not None
                    else _american_to_implied(line.price_american)
                )
                if current_implied is not None and avg_market_implied_probability_last_n is not None:
                    current_implied_delta_vs_avg_last_n = round(
                        current_implied - avg_market_implied_probability_last_n,
                        3,
                    )

        rows.append(
            MarketContextRow(
                market=line.market,
                side=line.side,
                selection=line.label,
                book=line.book,
                current_line=line.line,
                current_price_american=line.price_american,
                open_line=line.open_line,
                open_price_american=line.open_price_american,
                best_entry_anchor=line.best_entry_anchor,
                best_entry_line=line.best_entry_line,
                best_entry_price_american=line.best_entry_price_american,
                best_entry_quote_utc=line.best_entry_quote_utc,
                implied_probability=line.implied_probability,
                price_move_implied_from_open=line.price_move_implied_from_open,
                price_move_american_from_open=line.price_move_american_from_open,
                number_move_from_open=line.number_move_from_open,
                latest_quote_utc=line.collected_at_utc,
                is_live=line.is_live,
                is_stale=line.is_stale,
                bets_pct=split.bets_pct if split else None,
                handle_pct=split.handle_pct if split else None,
                record_vs_current_line_last_n=record_vs_current_line_last_n,
                avg_margin_vs_current_line_last_n=avg_margin_vs_current_line_last_n,
                recent_results_vs_current_line=recent_results_vs_current_line,
                record_vs_market_line_last_n=record_vs_market_line_last_n,
                avg_margin_vs_market_line_last_n=avg_margin_vs_market_line_last_n,
                recent_results_vs_market_lines=recent_results_vs_market_lines,
                recent_record_last_n=recent_record_last_n,
                recent_win_rate_last_n=recent_win_rate_last_n,
                avg_market_price_american_last_n=avg_market_price_american_last_n,
                avg_market_implied_probability_last_n=avg_market_implied_probability_last_n,
                current_implied_delta_vs_avg_last_n=current_implied_delta_vs_avg_last_n,
                recent_results_vs_market_prices=recent_results_vs_market_prices,
                signal_notes=notes,
            )
        )
    return rows


def _mlb_research_as_of(session: Session, event_id: int, start_time_utc: datetime) -> datetime:
    return _earliest_pregame_quote_time(session, event_id, start_time_utc) or (
        _ensure_utc(start_time_utc) or datetime.now(timezone.utc)
    )


def _avg(values: list[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 3)


def _ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(float(numerator) / float(denominator), 3)


def _slugging_rate(
    at_bats: int | None,
    hits: int | None,
    doubles: int | None,
    triples: int | None,
    home_runs: int | None,
) -> float | None:
    if at_bats in (None, 0) or hits is None:
        return None
    doubles = doubles or 0
    triples = triples or 0
    home_runs = home_runs or 0
    singles = max(int(hits) - doubles - triples - home_runs, 0)
    total_bases = singles + (2 * doubles) + (3 * triples) + (4 * home_runs)
    return round(total_bases / at_bats, 3)


def _mlb_team_trend(
    session: Session,
    team_id: int,
    event_start: datetime,
    as_of: datetime,
) -> TeamTrendContext:
    team = _team_out(session, team_id)
    logs = list(
        session.execute(
            select(MlbTeamGameLog)
            .where(MlbTeamGameLog.team_id == team_id)
            .where(MlbTeamGameLog.game_date_utc < as_of)
            .order_by(MlbTeamGameLog.game_date_utc.desc(), MlbTeamGameLog.id.desc())
            .limit(10)
        ).scalars()
    )
    if not logs:
        return TeamTrendContext(
            team=team,
            note="No prior MLB team logs available before the market snapshot.",
        )

    l5 = logs[:5]
    l3 = logs[:3]
    wins = [
        1
        for log_row in logs
        if log_row.runs_for is not None
        and log_row.runs_against is not None
        and log_row.runs_for > log_row.runs_against
    ]
    games_with_scores = [
        log_row
        for log_row in logs
        if log_row.runs_for is not None and log_row.runs_against is not None
    ]
    last_game = _ensure_utc(logs[0].game_date_utc)
    start = _ensure_utc(event_start)
    rest_days = None
    if last_game and start:
        rest_days = max(0, (start.date() - last_game.date()).days)

    runs_for = _avg([log_row.runs_for for log_row in l5])
    runs_against = _avg([log_row.runs_against for log_row in l5])
    hits_l5 = _avg([log_row.hits for log_row in l5])
    home_runs_l5 = _avg([log_row.home_runs for log_row in l5])
    walks_l5 = _avg([log_row.base_on_balls for log_row in l5])
    strikeouts_l5 = _avg([log_row.strike_outs for log_row in l5])
    steals_l5 = _avg([log_row.stolen_bases for log_row in l5])
    run_diff = None
    if runs_for is not None and runs_against is not None:
        run_diff = round(runs_for - runs_against, 3)
    total_at_bats_l5 = sum(log_row.at_bats or 0 for log_row in l5)
    total_hits_l5 = sum(log_row.hits or 0 for log_row in l5)
    batting_avg_l5 = _ratio(total_hits_l5, total_at_bats_l5)
    slugging_l5 = _slugging_rate(
        total_at_bats_l5,
        total_hits_l5,
        sum(log_row.doubles or 0 for log_row in l5),
        sum(log_row.triples or 0 for log_row in l5),
        sum(log_row.home_runs or 0 for log_row in l5),
    )

    return TeamTrendContext(
        team=team,
        games=len(logs),
        win_pct=round(len(wins) / len(games_with_scores), 3) if games_with_scores else None,
        avg_runs_for_l5=runs_for,
        avg_runs_against_l5=runs_against,
        run_diff_l5=run_diff,
        avg_hits_l5=hits_l5,
        avg_home_runs_l5=home_runs_l5,
        avg_walks_l5=walks_l5,
        avg_strikeouts_l5=strikeouts_l5,
        avg_steals_l5=steals_l5,
        batting_avg_l5=batting_avg_l5,
        slugging_l5=slugging_l5,
        avg_bullpen_outs_l3=_avg([log_row.bullpen_outs for log_row in l3]),
        rest_days=rest_days,
        last_game_utc=last_game,
    )


def _starter_context(
    session: Session,
    event_id: int,
    team_id: int,
    as_of: datetime,
) -> StarterContext:
    team = _team_out(session, team_id)
    row = session.execute(
        select(MlbProbableStarter, Player)
        .join(Player, Player.id == MlbProbableStarter.player_id)
        .where(MlbProbableStarter.event_id == event_id)
        .where(MlbProbableStarter.team_id == team_id)
        .order_by(MlbProbableStarter.collected_at_utc.desc())
    ).first()
    if row is None:
        return StarterContext(team=team, note="No probable starter found in local MLB schedule data.")

    starter, player = row
    event_start_utc = _ensure_utc(
        session.execute(select(Event.start_time_utc).where(Event.id == event_id)).scalar_one_or_none()
    )
    logs = list(
        session.execute(
            select(MlbPlayerGameLog)
            .where(MlbPlayerGameLog.player_id == starter.player_id)
            .where(MlbPlayerGameLog.pitching_started.is_(True))
            .where(MlbPlayerGameLog.game_date_utc < as_of)
            .order_by(MlbPlayerGameLog.game_date_utc.desc(), MlbPlayerGameLog.id.desc())
            .limit(3)
        ).scalars()
    )
    outs = sum(log_row.innings_pitched_outs or 0 for log_row in logs)
    earned_runs = sum(log_row.earned_runs or 0 for log_row in logs)
    hits = sum(log_row.pitching_hits or 0 for log_row in logs)
    walks = sum(log_row.pitching_base_on_balls or 0 for log_row in logs)
    strikeouts = sum(log_row.pitching_strike_outs or 0 for log_row in logs)
    pitches = _avg([log_row.pitches_thrown for log_row in logs])
    last_start_utc = _ensure_utc(logs[0].game_date_utc) if logs else None
    days_rest = None
    if last_start_utc:
        rest_basis = event_start_utc or as_of
        days_rest = max(0, (rest_basis.date() - last_start_utc.date()).days)

    era = whip = avg_ip = None
    if outs > 0:
        innings = outs / 3
        era = round((earned_runs * 9) / innings, 3)
        whip = round((hits + walks) / innings, 3)
        avg_ip = round(innings / len(logs), 3) if logs else None

    note = None if logs else "No prior local starts found before the market snapshot."
    return StarterContext(
        team=team,
        player_id=starter.player_id,
        player_name=player.full_name,
        primary_position=player.primary_position,
        prior_starts=len(logs),
        days_rest=days_rest,
        era_l3=era,
        whip_l3=whip,
        k_bb_l3=strikeouts - walks if logs else None,
        avg_ip_l3=avg_ip,
        avg_pitches_l3=pitches,
        avg_home_runs_allowed_l3=_avg([log_row.pitching_home_runs for log_row in logs]),
        last_start_utc=last_start_utc,
        note=note,
    )


def _mlb_player_stat_rows(
    session: Session,
    home_team_id: int,
    away_team_id: int,
    as_of: datetime,
    starter_context: dict[str, StarterContext],
) -> list[PlayerStatRow]:
    rows: list[PlayerStatRow] = []
    for side, team_id in [("away", away_team_id), ("home", home_team_id)]:
        starter = starter_context.get(side) or StarterContext(team=_team_out(session, team_id))
        if starter.player_id is None:
            rows.append(
                PlayerStatRow(
                    player_name="Probable starter pending",
                    team_name=starter.team.name,
                    role="probable_starter",
                    note=starter.note,
                )
            )
            continue

        logs = list(
            session.execute(
                select(MlbPlayerGameLog)
                .where(MlbPlayerGameLog.player_id == starter.player_id)
                .where(MlbPlayerGameLog.pitching_started.is_(True))
                .where(MlbPlayerGameLog.game_date_utc < as_of)
                .order_by(MlbPlayerGameLog.game_date_utc.desc(), MlbPlayerGameLog.id.desc())
                .limit(3)
            ).scalars()
        )
        last_games = []
        for log_row in logs:
            outs = log_row.innings_pitched_outs or 0
            innings = round(outs / 3, 2) if outs else None
            last_games.append(
                {
                    "date": (_ensure_utc(log_row.game_date_utc).date().isoformat()
                             if _ensure_utc(log_row.game_date_utc) else None),
                    "ip": innings,
                    "er": log_row.earned_runs,
                    "h": log_row.pitching_hits,
                    "bb": log_row.pitching_base_on_balls,
                    "k": log_row.pitching_strike_outs,
                    "pitches": log_row.pitches_thrown,
                }
            )
        rows.append(
            PlayerStatRow(
                player_name=starter.player_name or "Unknown starter",
                team_name=starter.team.name,
                role="probable_starter",
                position=starter.primary_position or "SP",
                games=len(logs),
                strike_outs=sum(log_row.pitching_strike_outs or 0 for log_row in logs),
                last_games=last_games,
                note=starter.note,
            )
        )

    for team_id in [away_team_id, home_team_id]:
        team_name = _team_out(session, team_id).name
        hitter_logs = list(
            session.execute(
                select(MlbPlayerGameLog, Player)
                .join(Player, Player.id == MlbPlayerGameLog.player_id)
                .where(MlbPlayerGameLog.team_id == team_id)
                .where(MlbPlayerGameLog.game_date_utc < as_of)
                .where(
                    or_(
                        MlbPlayerGameLog.at_bats.is_not(None),
                        MlbPlayerGameLog.base_on_balls.is_not(None),
                        MlbPlayerGameLog.hits.is_not(None),
                    )
                )
                .order_by(MlbPlayerGameLog.game_date_utc.desc(), MlbPlayerGameLog.id.desc())
            ).all()
        )
        grouped: dict[int, tuple[Player, list[MlbPlayerGameLog]]] = {}
        for log_row, player in hitter_logs:
            player_bucket = grouped.get(player.id)
            if player_bucket is None:
                grouped[player.id] = (player, [log_row])
                continue
            player_logs = player_bucket[1]
            if len(player_logs) < 5:
                player_logs.append(log_row)

        hitter_rows: list[PlayerStatRow] = []
        for player, logs in grouped.values():
            if not logs:
                continue
            at_bats = sum(log_row.at_bats or 0 for log_row in logs)
            walks = sum(log_row.base_on_balls or 0 for log_row in logs)
            hits = sum(log_row.hits or 0 for log_row in logs)
            home_runs = sum(log_row.home_runs or 0 for log_row in logs)
            rbi = sum(log_row.rbi or 0 for log_row in logs)
            strike_outs = sum(log_row.strike_outs or 0 for log_row in logs)
            stolen_bases = sum(log_row.stolen_bases or 0 for log_row in logs)
            batting_avg = _ratio(hits, at_bats)
            slugging = _slugging_rate(
                at_bats,
                hits,
                sum(log_row.doubles or 0 for log_row in logs),
                sum(log_row.triples or 0 for log_row in logs),
                home_runs,
            )
            obp_proxy = _ratio(hits + walks, at_bats + walks)
            ops_proxy = (
                round((obp_proxy or 0.0) + (slugging or 0.0), 3)
                if obp_proxy is not None or slugging is not None
                else None
            )
            if (
                (at_bats + walks) == 0
                and home_runs == 0
                and rbi == 0
                and stolen_bases == 0
            ):
                continue
            hitter_rows.append(
                PlayerStatRow(
                    player_name=player.full_name,
                    team_name=team_name,
                    role="recent_hitter",
                    position=player.primary_position,
                    games=len(logs),
                    at_bats=at_bats,
                    hits=hits,
                    home_runs=home_runs,
                    rbi=rbi,
                    base_on_balls=walks,
                    strike_outs=strike_outs,
                    stolen_bases=stolen_bases,
                    batting_avg=batting_avg,
                    slugging=slugging,
                    obp_proxy=obp_proxy,
                    ops_proxy=ops_proxy,
                    note="Recent hitter form from local boxscores; lineup not confirmed.",
                )
            )
        hitter_rows.sort(
            key=lambda row: (
                float(row.ops_proxy or 0.0),
                int(row.home_runs or 0),
                int(row.hits or 0),
            ),
            reverse=True,
        )
        rows.extend(hitter_rows[:3])
    return rows


def _mlb_matchup_snapshot(
    team_trends: dict[str, TeamTrendContext],
    starter_context: dict[str, StarterContext],
) -> list[MatchupStatRow]:
    away_team = team_trends.get("away")
    home_team = team_trends.get("home")
    away_starter = starter_context.get("away")
    home_starter = starter_context.get("home")
    if away_team is None and home_team is None and away_starter is None and home_starter is None:
        return []

    rows = [
        MatchupStatRow(
            category="Offense",
            metric="Runs L5",
            away_value=away_team.avg_runs_for_l5 if away_team else None,
            home_value=home_team.avg_runs_for_l5 if home_team else None,
        ),
        MatchupStatRow(
            category="Offense",
            metric="Hits L5",
            away_value=away_team.avg_hits_l5 if away_team else None,
            home_value=home_team.avg_hits_l5 if home_team else None,
        ),
        MatchupStatRow(
            category="Offense",
            metric="HR L5",
            away_value=away_team.avg_home_runs_l5 if away_team else None,
            home_value=home_team.avg_home_runs_l5 if home_team else None,
        ),
        MatchupStatRow(
            category="Offense",
            metric="AVG L5",
            away_value=away_team.batting_avg_l5 if away_team else None,
            home_value=home_team.batting_avg_l5 if home_team else None,
        ),
        MatchupStatRow(
            category="Offense",
            metric="SLG L5",
            away_value=away_team.slugging_l5 if away_team else None,
            home_value=home_team.slugging_l5 if home_team else None,
        ),
        MatchupStatRow(
            category="Discipline",
            metric="Walks L5",
            away_value=away_team.avg_walks_l5 if away_team else None,
            home_value=home_team.avg_walks_l5 if home_team else None,
        ),
        MatchupStatRow(
            category="Discipline",
            metric="Strikeouts L5",
            away_value=away_team.avg_strikeouts_l5 if away_team else None,
            home_value=home_team.avg_strikeouts_l5 if home_team else None,
        ),
        MatchupStatRow(
            category="Bullpen",
            metric="Bullpen Outs L3",
            away_value=away_team.avg_bullpen_outs_l3 if away_team else None,
            home_value=home_team.avg_bullpen_outs_l3 if home_team else None,
            note="Lower recent bullpen outs usually means a fresher relief group.",
        ),
        MatchupStatRow(
            category="Schedule",
            metric="Rest Days",
            away_value=away_team.rest_days if away_team else None,
            home_value=home_team.rest_days if home_team else None,
        ),
        MatchupStatRow(
            category="Starter",
            metric="ERA L3",
            away_value=away_starter.era_l3 if away_starter else None,
            home_value=home_starter.era_l3 if home_starter else None,
            note="Lower is better.",
        ),
        MatchupStatRow(
            category="Starter",
            metric="WHIP L3",
            away_value=away_starter.whip_l3 if away_starter else None,
            home_value=home_starter.whip_l3 if home_starter else None,
            note="Lower is better.",
        ),
        MatchupStatRow(
            category="Starter",
            metric="K-BB L3",
            away_value=away_starter.k_bb_l3 if away_starter else None,
            home_value=home_starter.k_bb_l3 if home_starter else None,
        ),
        MatchupStatRow(
            category="Starter",
            metric="Avg IP L3",
            away_value=away_starter.avg_ip_l3 if away_starter else None,
            home_value=home_starter.avg_ip_l3 if home_starter else None,
        ),
        MatchupStatRow(
            category="Starter",
            metric="Pitches L3",
            away_value=away_starter.avg_pitches_l3 if away_starter else None,
            home_value=home_starter.avg_pitches_l3 if home_starter else None,
        ),
        MatchupStatRow(
            category="Starter",
            metric="HR Allowed L3",
            away_value=away_starter.avg_home_runs_allowed_l3 if away_starter else None,
            home_value=home_starter.avg_home_runs_allowed_l3 if home_starter else None,
            note="Lower is better.",
        ),
        MatchupStatRow(
            category="Starter",
            metric="Days Rest",
            away_value=away_starter.days_rest if away_starter else None,
            home_value=home_starter.days_rest if home_starter else None,
        ),
    ]
    return [
        row
        for row in rows
        if row.away_value is not None or row.home_value is not None
    ]


def _mlb_bullpen_usage_rows(
    session: Session,
    home_team_id: int,
    away_team_id: int,
    as_of: datetime,
) -> list[BullpenUsageRow]:
    lookback_start = as_of - timedelta(days=3)
    raw_rows = list(
        session.execute(
            select(MlbPlayerGameLog, Player, Team)
            .join(Player, Player.id == MlbPlayerGameLog.player_id)
            .join(Team, Team.id == MlbPlayerGameLog.team_id)
            .where(MlbPlayerGameLog.team_id.in_([away_team_id, home_team_id]))
            .where(MlbPlayerGameLog.game_date_utc >= lookback_start)
            .where(MlbPlayerGameLog.game_date_utc < as_of)
            .where(MlbPlayerGameLog.pitching_started.is_(False))
            .where(MlbPlayerGameLog.innings_pitched_outs.is_not(None))
            .order_by(MlbPlayerGameLog.game_date_utc.desc(), MlbPlayerGameLog.id.desc())
        ).all()
    )
    grouped: dict[tuple[int, int], dict] = {}
    for log_row, player, team in raw_rows:
        key = (team.id, player.id)
        bucket = grouped.setdefault(
            key,
            {
                "team_name": team.name,
                "pitcher_name": player.full_name,
                "outings_last_3d": 0,
                "outs_last_3d": 0,
                "pitches_last_3d": 0,
                "strikeouts_last_3d": 0,
                "walks_last_3d": 0,
                "earned_runs_last_3d": 0,
                "home_runs_allowed_last_3d": 0,
                "last_appearance_utc": None,
            },
        )
        bucket["outings_last_3d"] += 1
        bucket["outs_last_3d"] += log_row.innings_pitched_outs or 0
        bucket["pitches_last_3d"] += log_row.pitches_thrown or 0
        bucket["strikeouts_last_3d"] += log_row.pitching_strike_outs or 0
        bucket["walks_last_3d"] += log_row.pitching_base_on_balls or 0
        bucket["earned_runs_last_3d"] += log_row.earned_runs or 0
        bucket["home_runs_allowed_last_3d"] += log_row.pitching_home_runs or 0
        appeared = _ensure_utc(log_row.game_date_utc)
        if bucket["last_appearance_utc"] is None or (
            appeared is not None and appeared > bucket["last_appearance_utc"]
        ):
            bucket["last_appearance_utc"] = appeared

    rows = [
        BullpenUsageRow(
            **bucket,
            note="Recent relief workload from local boxscores; not a depth-chart feed.",
        )
        for bucket in grouped.values()
        if bucket["outings_last_3d"] > 0
    ]
    rows.sort(
        key=lambda row: (
            row.team_name,
            -(row.pitches_last_3d or 0),
            -(row.outs_last_3d or 0),
            row.pitcher_name,
        )
    )
    limited: list[BullpenUsageRow] = []
    counts_by_team: dict[str, int] = {}
    for row in rows:
        counts_by_team.setdefault(row.team_name, 0)
        if counts_by_team[row.team_name] >= 3:
            continue
        limited.append(row)
        counts_by_team[row.team_name] += 1
    return limited


def _mlb_batting_order_stability_rows(
    session: Session,
    home_team_id: int,
    away_team_id: int,
    as_of: datetime,
) -> list[BattingOrderStabilityRow]:
    rows: list[BattingOrderStabilityRow] = []
    for team_id in [away_team_id, home_team_id]:
        team = _team_out(session, team_id)
        recent_team_events = [
            log_row.event_id
            for log_row in session.execute(
                select(MlbTeamGameLog)
                .where(MlbTeamGameLog.team_id == team_id)
                .where(MlbTeamGameLog.game_date_utc < as_of)
                .order_by(MlbTeamGameLog.game_date_utc.desc(), MlbTeamGameLog.id.desc())
                .limit(5)
            ).scalars()
        ]
        if not recent_team_events:
            continue

        player_logs = list(
            session.execute(
                select(MlbPlayerGameLog, Player)
                .join(Player, Player.id == MlbPlayerGameLog.player_id)
                .where(MlbPlayerGameLog.team_id == team_id)
                .where(MlbPlayerGameLog.event_id.in_(recent_team_events))
                .where(MlbPlayerGameLog.batting_started.is_(True))
                .where(MlbPlayerGameLog.batting_order.is_not(None))
                .order_by(MlbPlayerGameLog.game_date_utc.desc(), MlbPlayerGameLog.id.desc())
            ).all()
        )
        grouped: dict[int, dict] = {}
        for log_row, player in player_logs:
            bucket = grouped.setdefault(
                player.id,
                {
                    "player_name": player.full_name,
                    "orders": [],
                    "last_batting_order": None,
                    "last_started_utc": None,
                },
            )
            order = log_row.batting_order
            if order is not None:
                bucket["orders"].append(int(order))
            appeared = _ensure_utc(log_row.game_date_utc)
            if bucket["last_started_utc"] is None or (
                appeared is not None and appeared > bucket["last_started_utc"]
            ):
                bucket["last_started_utc"] = appeared
                bucket["last_batting_order"] = order

        team_rows: list[BattingOrderStabilityRow] = []
        for bucket in grouped.values():
            orders = bucket["orders"]
            if not orders:
                continue
            starts_last_5 = len(orders)
            slots_used = len(set(orders))
            avg_order = round(sum(orders) / len(orders), 2)
            if starts_last_5 >= 4 and slots_used == 1:
                stability = "locked in"
            elif starts_last_5 >= 3 and slots_used <= 2:
                stability = "mostly stable"
            else:
                stability = "rotating"
            team_rows.append(
                BattingOrderStabilityRow(
                    team_name=team.name,
                    player_name=bucket["player_name"],
                    starts_last_5=starts_last_5,
                    avg_batting_order=avg_order,
                    slots_used=slots_used,
                    last_batting_order=bucket["last_batting_order"],
                    last_started_utc=bucket["last_started_utc"],
                    stability_label=stability,
                    note="Based on batting starts in the last 5 local team games.",
                )
            )
        team_rows.sort(
            key=lambda row: (
                -(row.starts_last_5 or 0),
                row.avg_batting_order or 99,
                row.player_name,
            )
        )
        rows.extend(team_rows[:5])
    return rows


def _board_line(board_game: BoardGame, market: str, side: str) -> BoardLineOption | None:
    for line in board_game.lines:
        if line.market == market and line.side == side:
            return line
    return None


def _team_line_evidence_rows(
    session: Session,
    event_id: int,
    ev: Event,
    board_game: BoardGame,
    team_metrics: dict[str, TeamResearchMetrics],
) -> list[TeamLineEvidenceRow]:
    total_line = _board_line(board_game, "total", "over") or _board_line(board_game, "total", "under")
    team_total_quotes = list(
        session.execute(
            select(EventOddsQuote)
            .where(EventOddsQuote.event_id == event_id)
            .where(EventOddsQuote.book == "draftkings")
            .where(EventOddsQuote.market_key == "team_totals")
            .where(EventOddsQuote.entity_type == "team")
            .order_by(EventOddsQuote.collected_at_utc.desc(), EventOddsQuote.id.desc())
        ).scalars()
    )
    latest_team_total_by_side: dict[tuple[int | None, str, str], EventOddsQuote] = {}
    for quote in team_total_quotes:
        normalized_name = normalize_team_name(quote.participant_name or "")
        key = (quote.team_id, normalized_name, quote.side)
        if key not in latest_team_total_by_side:
            latest_team_total_by_side[key] = quote

    team_total_buckets: dict[tuple[int | None, str], dict[str, EventOddsQuote]] = {}
    for quote in latest_team_total_by_side.values():
        normalized_name = normalize_team_name(quote.participant_name or "")
        bucket_key = (quote.team_id, normalized_name)
        team_total_buckets.setdefault(bucket_key, {})[quote.side] = quote

    full_team_total_buckets: dict[tuple[int | None, str], dict[str, list[EventOddsQuote]]] = {}
    for quote in team_total_quotes:
        normalized_name = normalize_team_name(quote.participant_name or "")
        bucket_key = (quote.team_id, normalized_name)
        full_team_total_buckets.setdefault(bucket_key, {}).setdefault(quote.side, []).append(quote)

    def _team_total_market_bucket(team: TeamOut) -> dict[str, EventOddsQuote]:
        normalized_name = normalize_team_name(team.name)
        for key, bucket in team_total_buckets.items():
            if key[0] == team.id or key[1] == normalized_name:
                return bucket
        return {}

    def _team_total_history_bucket(team: TeamOut) -> dict[str, list[EventOddsQuote]]:
        normalized_name = normalize_team_name(team.name)
        for key, bucket in full_team_total_buckets.items():
            if key[0] == team.id or key[1] == normalized_name:
                return bucket
        return {}

    rows: list[TeamLineEvidenceRow] = []
    for side, team in (("away", board_game.away_team), ("home", board_game.home_team)):
        spread_line = _board_line(board_game, "spread", side)
        metrics = team_metrics.get(side)
        recent_games = metrics.recent_games if metrics else []
        sample = [
            row
            for row in recent_games
            if row.status == "final"
            and row.team_score is not None
            and row.close_implied_team_total is not None
        ][:5]
        team_total_history_bucket = _team_total_history_bucket(team)
        (
            latest_by_side,
            open_by_side,
            _t60_by_side,
            _t30_by_side,
            best_entry_anchor,
            best_entry_by_side,
        ) = _event_quote_snapshots(
            team_total_history_bucket,
            start_time_utc=ev.start_time_utc,
            now=datetime.now(timezone.utc),
            status=ev.status,
        ) if team_total_history_bucket else ({}, {}, {}, {}, None, {})
        team_total_quote = latest_by_side.get("over") or latest_by_side.get("under")
        open_line = _event_quote_line(open_by_side)
        best_entry_line = _event_quote_line(best_entry_by_side)
        current_line = _event_quote_line(latest_by_side)
        current_team_total = (
            current_line
            if current_line is not None
            else _derived_implied_team_total(
                total_line.line if total_line else None,
                spread_line.line if spread_line else None,
            )
        )
        current_line_results, current_line_record, avg_margin_vs_current_line = _recent_line_result_summary(
            [
                (
                    _ensure_utc(row.start_time_utc),
                    _ensure_utc(row.start_time_utc).strftime("%m/%d"),
                    float(row.team_score),
                )
                for row in recent_games
                if row.status == "final" and row.team_score is not None
            ][:5],
            current_team_total,
        )
        event_start_by_id = {
            row.event_id: row.start_time_utc
            for row in recent_games
            if row.event_id is not None
        }
        historical_market_quotes = list(
            session.execute(
                select(EventOddsQuote)
                .where(EventOddsQuote.book == "draftkings")
                .where(EventOddsQuote.market_key == "team_totals")
                .where(EventOddsQuote.team_id == team.id)
                .where(EventOddsQuote.event_id.in_(list(event_start_by_id)))
                .order_by(EventOddsQuote.collected_at_utc.desc(), EventOddsQuote.id.desc())
            ).scalars()
        ) if event_start_by_id else []
        historical_market_by_event = _latest_event_quote_snapshots_by_event(
            historical_market_quotes,
            event_start_by_id,
        )
        settled_market_history, market_line_record, avg_margin_vs_market_line = _settled_market_history_summary(
            [
                (
                    row.event_id,
                    _ensure_utc(row.start_time_utc),
                    _ensure_utc(row.start_time_utc).strftime("%m/%d"),
                    row.opponent.name if row.opponent else None,
                    float(row.team_score),
                    historical_market_by_event.get(row.event_id, {}),
                )
                for row in recent_games
                if row.status == "final" and row.team_score is not None
            ][:5]
        )
        market_line_results = [
            RecentLineResultPoint(
                game_date_utc=point.game_date_utc,
                label=point.label,
                value=point.value,
                line=point.line,
                margin_vs_line=point.margin_vs_line,
                result=point.result,
            )
            for point in settled_market_history
        ]
        rows.append(
            TeamLineEvidenceRow(
                team_name=team.name,
                line_source=(
                    "draftkings_team_total_market"
                    if current_line is not None
                    else "derived_implied_team_total_from_spread_total"
                ),
                current_team_total=current_team_total,
                open_team_total=(
                    open_line
                    if open_line is not None
                    else _derived_implied_team_total(
                        total_line.open_line if total_line else None,
                        spread_line.open_line if spread_line else None,
                    )
                ),
                best_entry_team_total=(
                    best_entry_line
                    if best_entry_line is not None
                    else _derived_implied_team_total(
                        total_line.best_entry_line if total_line else None,
                        spread_line.best_entry_line if spread_line else None,
                    )
                ),
                best_entry_anchor=best_entry_anchor or (total_line.best_entry_anchor if total_line else None),
                open_over_price_american=open_by_side.get("over").price_american if open_by_side.get("over") else None,
                current_over_price_american=latest_by_side.get("over").price_american if latest_by_side.get("over") else None,
                open_under_price_american=open_by_side.get("under").price_american if open_by_side.get("under") else None,
                current_under_price_american=latest_by_side.get("under").price_american if latest_by_side.get("under") else None,
                over_price_move_american_from_open=(
                    latest_by_side["over"].price_american - open_by_side["over"].price_american
                    if latest_by_side.get("over") and open_by_side.get("over")
                    else None
                ),
                under_price_move_american_from_open=(
                    latest_by_side["under"].price_american - open_by_side["under"].price_american
                    if latest_by_side.get("under") and open_by_side.get("under")
                    else None
                ),
                number_move_from_open=(
                    current_line - open_line
                    if current_line is not None and open_line is not None
                    else None
                ),
                latest_quote_utc=_ensure_utc(team_total_quote.collected_at_utc) if team_total_quote else None,
                history_points=_event_quote_history_points(team_total_history_bucket),
                games_sampled=len(sample),
                posted_line_games_sampled=len(market_line_results),
                avg_runs_last_n=_avg_numeric([row.team_score for row in sample]),
                avg_runs_allowed_last_n=_avg_numeric([row.opp_score for row in sample]),
                avg_close_implied_team_total_last_n=_avg_numeric(
                    [row.close_implied_team_total for row in sample]
                ),
                avg_runs_vs_close_implied_last_n=_avg_numeric(
                    [row.team_runs_vs_close_implied for row in sample]
                ),
                avg_allowed_vs_close_implied_last_n=_avg_numeric(
                    [row.opponent_runs_vs_close_implied for row in sample]
                ),
                avg_game_total_vs_close_total_last_n=_avg_numeric(
                    [row.game_total_vs_close_total for row in sample]
                ),
                team_total_record_last_n=_summarize_result_record(
                    [row.team_total_result for row in sample]
                ),
                game_total_record_last_n=_summarize_result_record(
                    [row.total_result for row in sample]
                ),
                record_vs_current_line_last_n=current_line_record,
                avg_margin_vs_current_line_last_n=avg_margin_vs_current_line,
                recent_results_vs_current_line=current_line_results,
                record_vs_market_line_last_n=market_line_record,
                avg_margin_vs_market_line_last_n=avg_margin_vs_market_line,
                recent_results_vs_market_lines=market_line_results,
                settled_market_history=settled_market_history,
                note=(
                    "Current team total uses the DraftKings team-total market when available; "
                    "open and best-entry team totals remain derived from DraftKings spread plus "
                    "game total until true team-total history is deep enough."
                    if current_line is not None
                    else "Current and anchor team totals are derived from DraftKings spread plus "
                    "game total until true sportsbook team-total history is deep enough."
                ),
            )
        )
    return rows


def _mlb_player_market_value(log_row: MlbPlayerGameLog, stat_key: str) -> float | None:
    if stat_key == "pitching_strike_outs":
        return float(log_row.pitching_strike_outs) if log_row.pitching_strike_outs is not None else None
    if stat_key == "hits":
        return float(log_row.hits) if log_row.hits is not None else None
    if stat_key == "total_bases":
        if log_row.hits is None:
            return None
        doubles = log_row.doubles or 0
        triples = log_row.triples or 0
        home_runs = log_row.home_runs or 0
        singles = max(int(log_row.hits) - doubles - triples - home_runs, 0)
        return float(singles + (2 * doubles) + (3 * triples) + (4 * home_runs))
    return None


def _recent_player_team_name(session: Session, player_id: int, as_of: datetime) -> str | None:
    row = session.execute(
        select(MlbPlayerGameLog.team_id)
        .where(MlbPlayerGameLog.player_id == player_id)
        .where(MlbPlayerGameLog.game_date_utc < as_of)
        .order_by(MlbPlayerGameLog.game_date_utc.desc(), MlbPlayerGameLog.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return _team_out(session, row).name if row is not None else None


def _player_prop_context_note(
    *,
    market_key: str,
    team_name: str | None,
    board_game: BoardGame,
    team_trends: dict[str, TeamTrendContext],
    starter_context: dict[str, StarterContext],
    environment_context: EnvironmentContext | None,
) -> str | None:
    side = None
    if team_name == board_game.home_team.name:
        side = "home"
    elif team_name == board_game.away_team.name:
        side = "away"

    if market_key == "pitcher_strikeouts" and side:
        opponent_side = "away" if side == "home" else "home"
        opponent = team_trends.get(opponent_side)
        starter = starter_context.get(side)
        bits: list[str] = []
        if opponent and opponent.avg_strikeouts_l5 is not None:
            bits.append(f"{opponent.team.name} strikeouts L5 {opponent.avg_strikeouts_l5:.1f}")
        if starter and starter.days_rest is not None:
            bits.append(f"{starter.days_rest}d rest")
        return "; ".join(bits) or None

    if side:
        opponent_side = "away" if side == "home" else "home"
        opposing_starter = starter_context.get(opponent_side)
        bits: list[str] = []
        if opposing_starter and opposing_starter.player_name:
            bits.append(f"vs {opposing_starter.player_name}")
        if environment_context and environment_context.field_wind_label:
            bits.append(environment_context.field_wind_label)
        if environment_context and environment_context.venue_name:
            bits.append(environment_context.venue_name)
        return "; ".join(bits) or None
    return None


def _player_prop_insights(
    session: Session,
    ev: Event,
    as_of: datetime,
    board_game: BoardGame,
    team_trends: dict[str, TeamTrendContext],
    starter_context: dict[str, StarterContext],
    environment_context: EnvironmentContext | None,
) -> list[PlayerPropInsightRow]:
    if board_game.sport != "baseball_mlb":
        return []

    quotes = list(
        session.execute(
            select(EventOddsQuote)
            .where(EventOddsQuote.event_id == ev.id)
            .where(EventOddsQuote.book == "draftkings")
            .where(EventOddsQuote.entity_type == "player")
            .order_by(EventOddsQuote.collected_at_utc.desc(), EventOddsQuote.id.desc())
        ).scalars()
    )
    if not quotes:
        return []

    buckets: dict[tuple[str, str, int | None], dict[str, list[EventOddsQuote]]] = {}
    for quote in quotes:
        key = (quote.market_key, quote.participant_name, quote.player_id)
        buckets.setdefault(key, {}).setdefault(quote.side, []).append(quote)

    insights: list[PlayerPropInsightRow] = []
    for (_, participant_name, player_id), side_history in buckets.items():
        (
            latest_by_side,
            open_by_side,
            _t60_by_side,
            _t30_by_side,
            best_entry_anchor,
            best_entry_by_side,
        ) = _event_quote_snapshots(
            side_history,
            start_time_utc=ev.start_time_utc,
            now=datetime.now(timezone.utc),
            status=ev.status,
        )
        representative = latest_by_side.get("over") or latest_by_side.get("under")
        if representative is None:
            continue
        spec = prop_market_spec(board_game.sport, representative.provider_market_key)
        if spec is None:
            continue
        over_quote = latest_by_side.get("over")
        under_quote = latest_by_side.get("under")
        current_line = _event_quote_line(latest_by_side)
        open_line = _event_quote_line(open_by_side)
        best_entry_line = _event_quote_line(best_entry_by_side)
        latest_quote_utc = _ensure_utc(
            max(quote.collected_at_utc for quote in latest_by_side.values())
        )

        if player_id is None:
            insights.append(
                PlayerPropInsightRow(
                    market_key=representative.market_key,
                    market_label=spec.label,
                    player_name=participant_name,
                    line_source="the_odds_api_event_odds",
                    open_line=open_line,
                    current_line=current_line,
                    open_over_price_american=open_by_side.get("over").price_american if open_by_side.get("over") else None,
                    over_price_american=over_quote.price_american if over_quote else None,
                    open_under_price_american=open_by_side.get("under").price_american if open_by_side.get("under") else None,
                    under_price_american=under_quote.price_american if under_quote else None,
                    best_entry_anchor=best_entry_anchor,
                    best_entry_line=best_entry_line,
                    best_entry_over_price_american=(
                        best_entry_by_side.get("over").price_american if best_entry_by_side.get("over") else None
                    ),
                    best_entry_under_price_american=(
                        best_entry_by_side.get("under").price_american if best_entry_by_side.get("under") else None
                    ),
                    number_move_from_open=(
                        current_line - open_line
                        if current_line is not None and open_line is not None
                        else None
                    ),
                    over_price_move_american_from_open=(
                        over_quote.price_american - open_by_side["over"].price_american
                        if over_quote and open_by_side.get("over")
                        else None
                    ),
                    under_price_move_american_from_open=(
                        under_quote.price_american - open_by_side["under"].price_american
                        if under_quote and open_by_side.get("under")
                        else None
                    ),
                    latest_quote_utc=latest_quote_utc,
                    history_points=_event_quote_history_points(side_history),
                    note="Player could not be matched to local MLB logs yet.",
                )
            )
            continue

        logs = list(
            session.execute(
                select(MlbPlayerGameLog)
                .where(MlbPlayerGameLog.player_id == player_id)
                .where(MlbPlayerGameLog.game_date_utc < as_of)
                .order_by(MlbPlayerGameLog.game_date_utc.desc(), MlbPlayerGameLog.id.desc())
                .limit(5)
            ).scalars()
        )
        recent_results: list[RecentStatPoint] = []
        result_inputs: list[tuple[int, datetime | None, str, float]] = []
        values: list[float] = []
        for log_row in logs:
            value = _mlb_player_market_value(log_row, spec.stat_key)
            if value is None:
                continue
            values.append(value)
            game_date = _ensure_utc(log_row.game_date_utc)
            label = game_date.strftime("%m/%d")
            recent_results.append(
                RecentStatPoint(
                    game_date_utc=game_date,
                    label=label,
                    value=round(value, 3),
                )
            )
            if log_row.event_id is not None:
                result_inputs.append((log_row.event_id, game_date, label, round(value, 3)))
        team_name = _recent_player_team_name(session, player_id, as_of)
        over_rate = None
        under_rate = None
        if current_line is not None and values:
            over_rate = round(
                sum(1 for value in values if value > float(current_line)) / len(values),
                3,
            )
            under_rate = round(
                sum(1 for value in values if value < float(current_line)) / len(values),
                3,
            )
        current_line_results, current_line_record, avg_margin_vs_current_line = _recent_line_result_summary(
            [
                (
                    point.game_date_utc,
                    point.label,
                    point.value,
                )
                for point in recent_results
            ],
            current_line,
        )
        event_start_by_id = {
            log_row.event_id: log_row.game_date_utc
            for log_row in logs
            if log_row.event_id is not None
        }
        historical_market_quotes = list(
            session.execute(
                select(EventOddsQuote)
                .where(EventOddsQuote.book == "draftkings")
                .where(EventOddsQuote.market_key == representative.market_key)
                .where(EventOddsQuote.player_id == player_id)
                .where(EventOddsQuote.event_id.in_(list(event_start_by_id)))
                .order_by(EventOddsQuote.collected_at_utc.desc(), EventOddsQuote.id.desc())
            ).scalars()
        ) if event_start_by_id else []
        historical_market_by_event = _latest_event_quote_snapshots_by_event(
            historical_market_quotes,
            event_start_by_id,
        )
        settled_market_history, market_line_record, avg_margin_vs_market_line = _settled_market_history_summary(
            [
                (
                    event_id,
                    game_date_utc,
                    label,
                    None,
                    value,
                    historical_market_by_event.get(event_id, {}),
                )
                for event_id, game_date_utc, label, value in result_inputs
            ]
        )
        market_line_results = [
            RecentLineResultPoint(
                game_date_utc=point.game_date_utc,
                label=point.label,
                value=point.value,
                line=point.line,
                margin_vs_line=point.margin_vs_line,
                result=point.result,
            )
            for point in settled_market_history
        ]

        insights.append(
            PlayerPropInsightRow(
                market_key=representative.market_key,
                market_label=spec.label,
                player_name=participant_name,
                team_name=team_name,
                line_source="the_odds_api_event_odds",
                open_line=open_line,
                current_line=current_line,
                open_over_price_american=open_by_side.get("over").price_american if open_by_side.get("over") else None,
                over_price_american=over_quote.price_american if over_quote else None,
                open_under_price_american=open_by_side.get("under").price_american if open_by_side.get("under") else None,
                under_price_american=under_quote.price_american if under_quote else None,
                best_entry_anchor=best_entry_anchor,
                best_entry_line=best_entry_line,
                best_entry_over_price_american=(
                    best_entry_by_side.get("over").price_american if best_entry_by_side.get("over") else None
                ),
                best_entry_under_price_american=(
                    best_entry_by_side.get("under").price_american if best_entry_by_side.get("under") else None
                ),
                number_move_from_open=(
                    current_line - open_line
                    if current_line is not None and open_line is not None
                    else None
                ),
                over_price_move_american_from_open=(
                    over_quote.price_american - open_by_side["over"].price_american
                    if over_quote and open_by_side.get("over")
                    else None
                ),
                under_price_move_american_from_open=(
                    under_quote.price_american - open_by_side["under"].price_american
                    if under_quote and open_by_side.get("under")
                    else None
                ),
                latest_quote_utc=latest_quote_utc,
                games_sampled=len(values),
                posted_line_games_sampled=len(market_line_results),
                avg_last_n=_avg_numeric(values),
                hit_rate_over_last_n=over_rate,
                hit_rate_under_last_n=under_rate,
                last_values=[round(value, 3) for value in values],
                recent_results=recent_results,
                history_points=_event_quote_history_points(side_history),
                record_vs_current_line_last_n=current_line_record,
                avg_margin_vs_current_line_last_n=avg_margin_vs_current_line,
                recent_results_vs_current_line=current_line_results,
                record_vs_market_line_last_n=market_line_record,
                avg_margin_vs_market_line_last_n=avg_margin_vs_market_line,
                recent_results_vs_market_lines=market_line_results,
                settled_market_history=settled_market_history,
                context_note=_player_prop_context_note(
                    market_key=representative.market_key,
                    team_name=team_name,
                    board_game=board_game,
                    team_trends=team_trends,
                    starter_context=starter_context,
                    environment_context=environment_context,
                ),
                note=(
                    None
                    if values
                    else "No prior local player logs were available before the market snapshot."
                ),
            )
        )

    return sorted(
        insights,
        key=lambda row: (
            0 if row.games_sampled else 1,
            0 if row.current_line is not None else 1,
            -max(row.hit_rate_over_last_n or 0.0, row.hit_rate_under_last_n or 0.0),
            row.player_name,
        ),
    )[:12]


def _environment_context_for_event(session: Session, ev: Event, sport: str) -> EnvironmentContext:
    if sport == "baseball_mlb":
        venue_row = session.execute(
            select(MlbEventVenue, MlbVenue)
            .join(MlbVenue, MlbVenue.id == MlbEventVenue.venue_id)
            .where(MlbEventVenue.event_id == ev.id)
        ).first()
        venue = venue_row[1] if venue_row else None
        park_factor = None
        if venue:
            park_factor = session.execute(
                select(MlbParkFactor)
                .where(MlbParkFactor.venue_id == venue.id)
                .order_by(
                    MlbParkFactor.season.desc(),
                    MlbParkFactor.rolling_years.desc(),
                    MlbParkFactor.imported_at_utc.desc(),
                )
                .limit(1)
            ).scalar_one_or_none()
        snapshot = session.execute(
            select(MlbEnvironmentSnapshot)
            .where(MlbEnvironmentSnapshot.event_id == ev.id)
            .order_by(
                MlbEnvironmentSnapshot.collected_at_utc.desc(),
                MlbEnvironmentSnapshot.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        if snapshot:
            derived_wind = derive_field_wind(
                wind_direction=snapshot.wind_direction,
                wind_mph=snapshot.wind_mph,
                center_field_orientation_deg=venue.orientation_deg if venue else None,
                roof_type=venue.roof_type if venue else None,
                weather_exposure_rule=venue.weather_exposure_rule if venue else None,
                wind_reliable_flag=venue.wind_reliable_flag if venue else None,
            )
            note = snapshot.notes
            if venue and venue.park_factor_runs is None and venue.park_factor_hr is None:
                note = "; ".join(
                    part
                    for part in [
                        note,
                        "Park-factor source pending review.",
                    ]
                    if part
                )
            return EnvironmentContext(
                provider=snapshot.provider,
                available=True,
                venue_name=venue.name if venue else None,
                roof_type=venue.roof_type if venue else None,
                park_factor_runs=park_factor.runs_factor if park_factor else (venue.park_factor_runs if venue else None),
                park_factor_hr=park_factor.hr_factor if park_factor else (venue.park_factor_hr if venue else None),
                park_factor_source=park_factor.source if park_factor else None,
                park_factor_season=park_factor.season if park_factor else None,
                park_factor_rolling_years=park_factor.rolling_years if park_factor else None,
                temperature_f=snapshot.temperature_f,
                wind_mph=snapshot.wind_mph,
                wind_direction=snapshot.wind_direction,
                wind_from_degrees=(
                    snapshot.wind_from_degrees
                    if snapshot.wind_from_degrees is not None
                    else derived_wind.wind_from_degrees
                ),
                wind_to_center_alignment=(
                    snapshot.wind_to_center_alignment
                    if snapshot.wind_to_center_alignment is not None
                    else derived_wind.wind_to_center_alignment
                ),
                wind_out_mph=(
                    snapshot.wind_out_mph
                    if snapshot.wind_out_mph is not None
                    else derived_wind.wind_out_mph
                ),
                wind_in_mph=(
                    snapshot.wind_in_mph
                    if snapshot.wind_in_mph is not None
                    else derived_wind.wind_in_mph
                ),
                crosswind_mph=(
                    snapshot.crosswind_mph
                    if snapshot.crosswind_mph is not None
                    else derived_wind.crosswind_mph
                ),
                field_wind_label=snapshot.field_wind_label or derived_wind.field_wind_label,
                precipitation_chance=snapshot.precipitation_chance,
                conditions=snapshot.conditions,
                forecast_for_utc=_ensure_utc(snapshot.forecast_for_utc),
                collected_at_utc=_ensure_utc(snapshot.collected_at_utc),
                note=note,
            )
        if venue:
            missing = []
            if venue.latitude is None or venue.longitude is None:
                missing.append("venue coordinates pending")
            missing.append("weather snapshot pending")
            if venue.park_factor_runs is None and venue.park_factor_hr is None:
                missing.append("park-factor source pending review")
            return EnvironmentContext(
                provider="nws_api",
                available=False,
                venue_name=venue.name,
                roof_type=venue.roof_type,
                park_factor_runs=park_factor.runs_factor if park_factor else venue.park_factor_runs,
                park_factor_hr=park_factor.hr_factor if park_factor else venue.park_factor_hr,
                park_factor_source=park_factor.source if park_factor else None,
                park_factor_season=park_factor.season if park_factor else None,
                park_factor_rolling_years=park_factor.rolling_years if park_factor else None,
                note=", ".join(missing) + ".",
            )
        return EnvironmentContext(
            provider="nws_api",
            available=False,
            note="MLB venue mapping is missing; run collect-mlb-stats for this slate first.",
        )
    return EnvironmentContext(
        provider=None,
        available=False,
        note="Environment context is not wired for this sport yet.",
    )


def _lean_label(lean: str | None, home_team: TeamOut, away_team: TeamOut) -> str:
    if lean == "home":
        return home_team.name
    if lean == "away":
        return away_team.name
    if lean == "over":
        return "Over"
    if lean == "under":
        return "Under"
    if lean == "neutral":
        return "Neutral"
    return lean or "Neutral"


def _market_factor(
    market_context: list[MarketContextRow],
) -> WhyThisLineFactor | None:
    if not market_context:
        return None

    def factor_score(row: MarketContextRow) -> float:
        score = 0.0
        if row.number_move_from_open is not None:
            score = max(score, abs(float(row.number_move_from_open)) * 10.0)
        if row.price_move_american_from_open is not None:
            score = max(score, abs(float(row.price_move_american_from_open)) / 10.0)
        if row.handle_pct is not None and row.bets_pct is not None:
            score = max(score, abs(float(row.handle_pct) - float(row.bets_pct)) / 5.0)
        return score

    row = max(market_context, key=factor_score)
    score = round(factor_score(row), 3)
    move_bits: list[str] = []
    if row.number_move_from_open not in (None, 0):
        move_bits.append(f"number {row.number_move_from_open:+g}")
    if row.price_move_american_from_open not in (None, 0):
        move_bits.append(f"price {row.price_move_american_from_open:+d}")
    if row.handle_pct is not None and row.bets_pct is not None:
        move_bits.append(
            f"handle {row.handle_pct:.1f}% vs bets {row.bets_pct:.1f}%"
        )
    if row.best_entry_anchor:
        best_entry_line = (
            f"{row.best_entry_line:g} " if row.best_entry_line is not None else ""
        )
        best_entry_price = (
            f"{row.best_entry_price_american:+d}" if row.best_entry_price_american is not None else ""
        )
        move_bits.append(
            f"best entry {row.best_entry_anchor} {best_entry_line}{best_entry_price}".strip()
        )
    market_focus = "total" if row.market == "total" else "side"
    headline = f"Market pressure is strongest on {row.selection}."
    if "possible_reverse_line_move" in row.signal_notes:
        headline = f"Market pressure on {row.selection} includes possible reverse movement."
    detail = ", ".join(bit for bit in move_bits if bit) or "Current market context is mostly flat."
    return WhyThisLineFactor(
        factor="Market Pressure",
        market_focus=market_focus,
        lean=row.side if row.side in {"home", "away", "over", "under"} else None,
        score=score,
        headline=headline,
        detail=detail,
    )


def _starter_factor(
    starter_context: dict[str, StarterContext],
    home_team: TeamOut,
    away_team: TeamOut,
) -> WhyThisLineFactor | None:
    home = starter_context.get("home")
    away = starter_context.get("away")
    if home is None or away is None or home.player_id is None or away.player_id is None:
        return None

    home_adv = 0.0
    used = False
    if home.era_l3 is not None and away.era_l3 is not None:
        home_adv += away.era_l3 - home.era_l3
        used = True
    if home.whip_l3 is not None and away.whip_l3 is not None:
        home_adv += 0.75 * (away.whip_l3 - home.whip_l3)
        used = True
    if home.k_bb_l3 is not None and away.k_bb_l3 is not None:
        home_adv += 0.12 * (home.k_bb_l3 - away.k_bb_l3)
        used = True
    if home.avg_ip_l3 is not None and away.avg_ip_l3 is not None:
        home_adv += 0.1 * (home.avg_ip_l3 - away.avg_ip_l3)
        used = True
    if not used:
        return None

    lean = "neutral"
    if home_adv > 0.25:
        lean = "home"
    elif home_adv < -0.25:
        lean = "away"

    headline = (
        "Starting pitching looks balanced."
        if lean == "neutral"
        else f"Starting pitching leans {_lean_label(lean, home_team, away_team)}."
    )
    detail = (
        f"{home_team.name}: {home.player_name or '-'} "
        f"(ERA L3 {home.era_l3 if home.era_l3 is not None else '-'}, "
        f"WHIP L3 {home.whip_l3 if home.whip_l3 is not None else '-'}, "
        f"K-BB L3 {home.k_bb_l3 if home.k_bb_l3 is not None else '-'}) | "
        f"{away_team.name}: {away.player_name or '-'} "
        f"(ERA L3 {away.era_l3 if away.era_l3 is not None else '-'}, "
        f"WHIP L3 {away.whip_l3 if away.whip_l3 is not None else '-'}, "
        f"K-BB L3 {away.k_bb_l3 if away.k_bb_l3 is not None else '-'})"
    )
    return WhyThisLineFactor(
        factor="Starting Pitching",
        market_focus="side",
        lean=lean,
        score=round(abs(home_adv), 3),
        headline=headline,
        detail=detail,
    )


def _team_form_factor(
    team_trends: dict[str, TeamTrendContext],
    home_team: TeamOut,
    away_team: TeamOut,
) -> WhyThisLineFactor | None:
    home = team_trends.get("home")
    away = team_trends.get("away")
    if home is None or away is None:
        return None

    home_adv = 0.0
    used = False
    if home.run_diff_l5 is not None and away.run_diff_l5 is not None:
        home_adv += home.run_diff_l5 - away.run_diff_l5
        used = True
    if home.rest_days is not None and away.rest_days is not None:
        home_adv += 0.2 * (home.rest_days - away.rest_days)
        used = True
    if home.avg_bullpen_outs_l3 is not None and away.avg_bullpen_outs_l3 is not None:
        home_adv += 0.08 * (away.avg_bullpen_outs_l3 - home.avg_bullpen_outs_l3)
        used = True
    if not used:
        return None

    lean = "neutral"
    if home_adv > 0.35:
        lean = "home"
    elif home_adv < -0.35:
        lean = "away"

    headline = (
        "Recent team form is fairly balanced."
        if lean == "neutral"
        else f"Team form and bullpen context lean {_lean_label(lean, home_team, away_team)}."
    )
    detail = (
        f"Run diff L5: {home_team.name} "
        f"{home.run_diff_l5 if home.run_diff_l5 is not None else '-'} vs "
        f"{away.run_diff_l5 if away.run_diff_l5 is not None else '-'} {away_team.name}; "
        f"bullpen outs L3: {home.avg_bullpen_outs_l3 if home.avg_bullpen_outs_l3 is not None else '-'} vs "
        f"{away.avg_bullpen_outs_l3 if away.avg_bullpen_outs_l3 is not None else '-'}; "
        f"rest: {home.rest_days if home.rest_days is not None else '-'}d vs "
        f"{away.rest_days if away.rest_days is not None else '-'}d."
    )
    return WhyThisLineFactor(
        factor="Team Form",
        market_focus="side",
        lean=lean,
        score=round(abs(home_adv), 3),
        headline=headline,
        detail=detail,
    )


def _run_environment_factor(
    environment_context: EnvironmentContext | None,
) -> WhyThisLineFactor | None:
    env = environment_context
    if env is None:
        return None

    total_adv = 0.0
    used = False
    detail_bits: list[str] = []
    if env.park_factor_runs is not None:
        total_adv += (env.park_factor_runs - 100.0) / 6.0
        detail_bits.append(f"park runs {env.park_factor_runs:.1f}")
        used = True
    if env.park_factor_hr is not None:
        total_adv += (env.park_factor_hr - 100.0) / 12.0
        detail_bits.append(f"park HR {env.park_factor_hr:.1f}")
        used = True
    if env.wind_out_mph is not None and env.wind_out_mph > 0:
        total_adv += env.wind_out_mph / 6.0
        detail_bits.append(f"wind out {env.wind_out_mph:.1f} mph")
        used = True
    if env.wind_in_mph is not None and env.wind_in_mph > 0:
        total_adv -= env.wind_in_mph / 6.0
        detail_bits.append(f"wind in {env.wind_in_mph:.1f} mph")
        used = True
    if env.temperature_f is not None:
        total_adv += max(min(env.temperature_f - 72.0, 18.0), -18.0) / 18.0 * 0.75
        detail_bits.append(f"temp {env.temperature_f:.0f} F")
        used = True
    if env.field_wind_label and env.field_wind_label not in {"orientation pending"}:
        detail_bits.append(env.field_wind_label)
    if env.venue_name:
        detail_bits.append(env.venue_name)
    if not used:
        return None

    lean = "neutral"
    if total_adv > 0.35:
        lean = "over"
    elif total_adv < -0.35:
        lean = "under"
    headline = (
        "Run environment looks fairly neutral."
        if lean == "neutral"
        else f"Run environment leans {_lean_label(lean, TeamOut(id=0, name=''), TeamOut(id=0, name=''))}."
    )
    return WhyThisLineFactor(
        factor="Run Environment",
        market_focus="total",
        lean=lean,
        score=round(abs(total_adv), 3),
        headline=headline,
        detail=", ".join(detail_bits),
    )


def _why_this_line_factors(
    board_game: BoardGame,
    market_context: list[MarketContextRow],
    team_trends: dict[str, TeamTrendContext],
    starter_context: dict[str, StarterContext],
    environment_context: EnvironmentContext | None,
) -> list[WhyThisLineFactor]:
    factors: list[WhyThisLineFactor] = []
    market_factor = _market_factor(market_context)
    if market_factor is not None:
        factors.append(market_factor)

    if board_game.sport == "baseball_mlb":
        for factor in (
            _starter_factor(starter_context, board_game.home_team, board_game.away_team),
            _team_form_factor(team_trends, board_game.home_team, board_game.away_team),
            _run_environment_factor(environment_context),
        ):
            if factor is not None:
                factors.append(factor)

    return sorted(
        factors,
        key=lambda factor: float(factor.score or 0.0),
        reverse=True,
    )[:5]


def _event_research_response(session: Session, ev: Event, now: datetime) -> GameResearchResponse:
    board_game = _build_board_game(session, ev, now)
    snapshots = _build_snapshot_map(session, ev.id)
    as_of = _mlb_research_as_of(session, ev.id, ev.start_time_utc)
    team_metrics = {
        "home": _team_research_metrics(session, ev.home_team_id, as_of=as_of),
        "away": _team_research_metrics(session, ev.away_team_id, as_of=as_of),
    }
    features: list[dict] = []
    for market, side in _BOARD_LINE_COMBOS:
        try:
            row = build_features(session, ev.id, market, side).to_dict()
            if row.get("start_time_utc"):
                row["start_time_utc"] = row["start_time_utc"].isoformat()
            features.append(row)
        except Exception as exc:
            log.warning("Research feature build error %s/%s: %s", market, side, exc)

    warnings = list(board_game.flags)
    market_context = _market_context(board_game, team_metrics)
    team_trends: dict[str, TeamTrendContext] = {}
    starter_context: dict[str, StarterContext] = {}
    player_stats: list[PlayerStatRow]
    data_gaps: list[str] = []
    matchup_snapshot: list[MatchupStatRow] = []
    bullpen_usage: list[BullpenUsageRow] = []
    batting_order_stability: list[BattingOrderStabilityRow] = []
    player_prop_insights: list[PlayerPropInsightRow] = []
    player_props_note = ""
    environment_context = _environment_context_for_event(session, ev, board_game.sport)
    if environment_context.note and not environment_context.available:
        data_gaps.append("weather_wind_pending")
    if (
        board_game.sport == "baseball_mlb"
        and environment_context.available
        and environment_context.field_wind_label == "orientation pending"
    ):
        data_gaps.append("field_wind_orientation_pending")
    if (
        board_game.sport == "baseball_mlb"
        and environment_context.park_factor_runs is None
        and environment_context.park_factor_hr is None
    ):
        data_gaps.append("park_factor_source_pending")

    if board_game.sport == "baseball_mlb":
        team_trends = {
            "home": _mlb_team_trend(session, ev.home_team_id, ev.start_time_utc, as_of),
            "away": _mlb_team_trend(session, ev.away_team_id, ev.start_time_utc, as_of),
        }
        starter_context = {
            "home": _starter_context(session, ev.id, ev.home_team_id, as_of),
            "away": _starter_context(session, ev.id, ev.away_team_id, as_of),
        }
        player_stats = _mlb_player_stat_rows(
            session,
            ev.home_team_id,
            ev.away_team_id,
            as_of,
            starter_context,
        )
        bullpen_usage = _mlb_bullpen_usage_rows(
            session,
            ev.home_team_id,
            ev.away_team_id,
            as_of,
        )
        batting_order_stability = _mlb_batting_order_stability_rows(
            session,
            ev.home_team_id,
            ev.away_team_id,
            as_of,
        )
        player_stats_note = (
            "MLB player context shows probable starter recent starts and recent hitter form "
            "from local boxscores. Hitter rows are not lineup-confirmed."
        )
        player_prop_insights = _player_prop_insights(
            session,
            ev,
            as_of,
            board_game,
            team_trends,
            starter_context,
            environment_context,
        )
        player_props_note = (
            "Current player-line comparisons use bounded event-specific Odds API markets. "
            "When no current props are stored, the board falls back to descriptive recent averages only."
        )
        matchup_snapshot = _mlb_matchup_snapshot(team_trends, starter_context)
        if any(ctx.games == 0 for ctx in team_trends.values()):
            data_gaps.append("missing_prior_team_logs")
        if any(ctx.player_id is None for ctx in starter_context.values()):
            data_gaps.append("missing_probable_starter")
        elif any(ctx.prior_starts == 0 for ctx in starter_context.values()):
            data_gaps.append("missing_prior_starter_logs")
        if not player_prop_insights:
            data_gaps.append("player_props_quotes_missing")
    else:
        player_stats_note = (
            "Player stat tables are not wired to a free provider yet for this sport."
        )
        player_props_note = "Player prop markets are not wired for this sport yet."
        player_stats = [
            PlayerStatRow(
                player_name="Player stats source pending",
                note=player_stats_note,
            )
        ]
        data_gaps.append("player_stats_provider_pending")

    if not board_game.split_summary:
        data_gaps.append("public_splits_missing")
    if not board_game.lines:
        data_gaps.append("odds_missing")
    warnings.extend(data_gaps)
    why_this_line = _why_this_line_factors(
        board_game,
        market_context,
        team_trends,
        starter_context,
        environment_context,
    )
    team_line_evidence = _team_line_evidence_rows(session, ev.id, ev, board_game, team_metrics)
    line_evidence_status = build_line_evidence_status_rows(
        session,
        board_game=board_game,
        market_context=market_context,
        team_line_evidence=team_line_evidence,
        player_prop_insights=player_prop_insights,
    )
    line_thesis = build_line_thesis_rows(
        market_context=market_context,
        team_line_evidence=team_line_evidence,
        player_prop_insights=player_prop_insights,
        line_evidence_status=line_evidence_status,
        why_this_line=why_this_line,
    )

    return GameResearchResponse(
        event_id=ev.id,
        sport=board_game.sport,
        league_key=board_game.league_key,
        start_time_utc=ev.start_time_utc,
        status=ev.status,
        home_team=board_game.home_team,
        away_team=board_game.away_team,
        home_score=board_game.home_score,
        away_score=board_game.away_score,
        latest_quote_utc=board_game.latest_quote_utc,
        odds_age_min=board_game.odds_age_min,
        odds_stale=board_game.odds_stale,
        lines=board_game.lines,
        split_summary=board_game.split_summary,
        snapshots=snapshots,
        features=features,
        team_metrics=team_metrics,
        team_line_evidence=team_line_evidence,
        market_context=market_context,
        line_evidence_status=line_evidence_status,
        line_thesis=line_thesis,
        team_trends=team_trends,
        starter_context=starter_context,
        environment_context=environment_context,
        why_this_line=why_this_line,
        matchup_snapshot=matchup_snapshot,
        bullpen_usage=bullpen_usage,
        batting_order_stability=batting_order_stability,
        data_gaps=sorted(set(data_gaps)),
        player_stats=player_stats,
        player_stats_note=player_stats_note,
        player_prop_insights=player_prop_insights,
        player_props_note=player_props_note,
        warnings=sorted(set(warnings)),
    )


@app.get("/board", response_model=BoardResponse)
def sportsbook_board(
    sport: str = Query("basketball_ncaab", description="Configured sport key"),
    mode: str = Query("today", description="live, today, or upcoming"),
    date: str | None = Query(None, description="Date YYYY-MM-DD for today mode"),
    limit: int = Query(80, ge=1, le=250),
    db: Session = Depends(get_db),
):
    """
    Compact sportsbook-style board for the main dashboard.

    This endpoint intentionally returns enough row data for the UI to render
    without calling multiple detail endpoints for every game.
    """
    now = datetime.now(timezone.utc)
    league_key = _league_key_for_request_sport(sport)

    mode = mode.lower().strip()
    if mode not in {"live", "today", "upcoming"}:
        raise HTTPException(status_code=400, detail="mode must be live, today, or upcoming")

    stmt = (
        select(Event)
        .options(selectinload(Event.league), selectinload(Event.result))
        .join(League, League.id == Event.league_id)
        .where(League.key == league_key)
        .order_by(Event.start_time_utc.asc())
        .limit(limit)
    )

    if mode == "live":
        stmt = stmt.where(Event.status == "live")
    elif mode == "today":
        day_start, day_end = _parse_date(date)
        stmt = stmt.where(Event.start_time_utc >= day_start, Event.start_time_utc < day_end)
    else:
        day_start, _ = _parse_date(date)
        start = max(now, day_start if date else now)
        end = start + timedelta(days=7)
        stmt = stmt.where(Event.start_time_utc >= start, Event.start_time_utc < end)

    try:
        events = list(db.execute(stmt).scalars())
    except SAOperationalError as exc:
        log.warning("Board query failed; database may be uninitialized: %s", exc)
        return BoardResponse(
            generated_at_utc=now,
            sport=sport,
            mode=mode,
            date=date,
            count=0,
            games=[],
            configured_sports=ui_sport_keys(),
            warnings=[
                "Database is not initialized or migrations have not run.",
                "Run `alembic upgrade head` before using the board.",
            ],
        )

    teams, latest_quotes, open_quotes, t60_quotes, t30_quotes, latest_splits = _prefetch_board_context(db, events)
    oof_rows_by_market = _latest_oof_rows_by_market()
    games = [
        _build_board_game_from_prefetch(
            ev,
            now,
            teams,
            latest_quotes,
            open_quotes,
            t60_quotes,
            t30_quotes,
            latest_splits,
            oof_rows_by_market=oof_rows_by_market,
        )
        for ev in events
    ]
    warnings: list[str] = []
    if not games:
        warnings.append("No games found for this sport and filter.")
    if any(game.odds_stale for game in games):
        warnings.append("Some games have stale or missing odds. Check collector status before acting.")
    if sport != "basketball_ncaab":
        warnings.append("Multi-sport lines are available only where collectors have matching data.")

    return BoardResponse(
        generated_at_utc=now,
        sport=sport,
        mode=mode,
        date=date,
        count=len(games),
        games=games,
        configured_sports=ui_sport_keys(),
        warnings=warnings,
    )


@app.get("/events/{event_id}/research", response_model=GameResearchResponse)
def event_research(event_id: int, db: Session = Depends(get_db)):
    """
    Expanded game research payload for a sportsbook-style detail drawer.
    """
    now = datetime.now(timezone.utc)
    ev = db.get(Event, event_id)
    if not ev:
        raise HTTPException(404, "Event not found")

    return _event_research_response(db, ev, now)


@app.get("/events/research", response_model=GameResearchBatchResponse)
def event_research_batch(
    event_ids: str = Query(..., description="Comma-separated event IDs"),
    db: Session = Depends(get_db),
):
    """Batch research payloads for expanded rows or slip refreshes."""
    now = datetime.now(timezone.utc)
    ids: list[int] = []
    for raw in event_ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ids.append(int(raw))
        except ValueError:
            raise HTTPException(status_code=400, detail="event_ids must be comma-separated ints")

    if not ids:
        raise HTTPException(status_code=400, detail="event_ids cannot be empty")
    if len(ids) > 20:
        raise HTTPException(status_code=400, detail="Batch research is limited to 20 events")

    events = list(
        db.execute(
            select(Event)
            .options(selectinload(Event.league), selectinload(Event.result))
            .where(Event.id.in_(ids))
        ).scalars()
    )
    event_by_id = {ev.id: ev for ev in events}
    missing = [event_id for event_id in ids if event_id not in event_by_id]
    payloads = [_event_research_response(db, event_by_id[event_id], now) for event_id in ids if event_id in event_by_id]

    warnings = [f"Event not found: {event_id}" for event_id in missing]
    return GameResearchBatchResponse(
        generated_at_utc=now,
        count=len(payloads),
        events=payloads,
        warnings=warnings,
    )


# ── GET /games ──────────────────────────────────────────────────

@app.get("/registry/props", response_model=PropMarketRegistryResponse)
def prop_market_registry(
    sport: str = Query("baseball_mlb", description="Configured sport key"),
):
    _league_key_for_request_sport(sport)
    rows = [
        PropMarketRegistryRow(
            sport_key=spec.sport_key,
            provider=spec.provider,
            provider_market_key=spec.provider_market_key,
            market_key=spec.market_key,
            label=spec.label,
            entity_type=spec.entity_type,
            selection_type=spec.selection_type,
            stat_key=spec.stat_key,
            ui_enabled=spec.ui_enabled,
            collection_enabled=spec.collection_enabled,
            notes=spec.notes,
        )
        for spec in prop_market_specs_for_sport(sport)
    ]
    return PropMarketRegistryResponse(sport=sport, count=len(rows), rows=rows)


def _count_pregame_quotes(db: Session, event_id: int, start_time_utc: datetime) -> int:
    return int(
        db.execute(
            select(func.count(OddsQuote.id))
            .where(OddsQuote.event_id == event_id)
            .where(OddsQuote.book == "draftkings")
            .where(OddsQuote.collected_at_utc < start_time_utc)
        ).scalar()
        or 0
    )


def _earliest_pregame_quote_time(
    db: Session,
    event_id: int,
    start_time_utc: datetime,
) -> datetime | None:
    return _ensure_utc(
        db.execute(
            select(func.min(OddsQuote.collected_at_utc))
            .where(OddsQuote.event_id == event_id)
            .where(OddsQuote.book == "draftkings")
            .where(OddsQuote.collected_at_utc < start_time_utc)
        ).scalar_one_or_none()
    )


def _count_prior_mlb_team_logs(db: Session, team_id: int, as_of: datetime) -> int:
    return int(
        db.execute(
            select(func.count(MlbTeamGameLog.id))
            .where(MlbTeamGameLog.team_id == team_id)
            .where(MlbTeamGameLog.game_date_utc < as_of)
        ).scalar()
        or 0
    )


def _mlb_starter_readiness(
    db: Session,
    event_id: int,
    team_id: int,
    as_of: datetime,
) -> MlbStarterReadiness | None:
    row = db.execute(
        select(MlbProbableStarter, Player)
        .join(Player, Player.id == MlbProbableStarter.player_id)
        .where(MlbProbableStarter.event_id == event_id)
        .where(MlbProbableStarter.team_id == team_id)
        .order_by(MlbProbableStarter.collected_at_utc.desc())
    ).first()
    if row is None:
        return None

    starter, player = row
    prior_starts = int(
        db.execute(
            select(func.count(MlbPlayerGameLog.id))
            .where(MlbPlayerGameLog.player_id == starter.player_id)
            .where(MlbPlayerGameLog.pitching_started.is_(True))
            .where(MlbPlayerGameLog.game_date_utc < as_of)
        ).scalar()
        or 0
    )
    return MlbStarterReadiness(
        team_id=team_id,
        player_id=starter.player_id,
        player_name=player.full_name,
        prior_starts=prior_starts,
    )


@app.get("/analysis/mlb/readiness", response_model=MlbReadinessResponse)
def mlb_readiness(
    sport: str = Query("baseball_mlb", description="Configured MLB sport key"),
    days_back: int = Query(1, ge=0, le=30),
    days_forward: int = Query(7, ge=1, le=30),
    limit: int = Query(80, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    Read-only MLB data readiness diagnostics for entry-time EV work.

    This only inspects local tables. It does not call provider APIs or spend odds quota.
    """
    now = datetime.now(timezone.utc)
    if sport != "baseball_mlb":
        raise HTTPException(status_code=400, detail="MLB readiness currently supports baseball_mlb only")
    league_key = _league_key_for_request_sport(sport)
    window_start = now - timedelta(days=days_back)
    window_end = now + timedelta(days=days_forward)

    events = list(
        db.execute(
            select(Event)
            .options(
                selectinload(Event.home_team),
                selectinload(Event.away_team),
                selectinload(Event.result),
            )
            .join(League, League.id == Event.league_id)
            .where(League.key == league_key)
            .where(Event.start_time_utc >= window_start)
            .where(Event.start_time_utc < window_end)
            .order_by(Event.start_time_utc.asc())
            .limit(limit)
        ).scalars()
    )

    event_ids = [ev.id for ev in events]
    provider_key_event_ids: set[int] = set()
    if event_ids:
        provider_key_event_ids = {
            int(event_id)
            for event_id in db.execute(
                select(EventProviderKey.event_id)
                .where(EventProviderKey.provider == "mlb_stats_api")
                .where(EventProviderKey.event_id.in_(event_ids))
            ).scalars()
        }

    readiness_rows: list[MlbReadinessEvent] = []
    for ev in events:
        start_time = _ensure_utc(ev.start_time_utc) or now
        pregame_quote_count = _count_pregame_quotes(db, ev.id, ev.start_time_utc)
        as_of = _earliest_pregame_quote_time(db, ev.id, ev.start_time_utc) or start_time
        home_team_logs = _count_prior_mlb_team_logs(db, ev.home_team_id, as_of)
        away_team_logs = _count_prior_mlb_team_logs(db, ev.away_team_id, as_of)
        home_starter = _mlb_starter_readiness(db, ev.id, ev.home_team_id, as_of)
        away_starter = _mlb_starter_readiness(db, ev.id, ev.away_team_id, as_of)

        has_provider_key = ev.id in provider_key_event_ids
        has_pregame_odds = pregame_quote_count > 0
        both_probable_starters = home_starter is not None and away_starter is not None
        both_team_history = home_team_logs > 0 and away_team_logs > 0
        both_starter_history = (
            home_starter is not None
            and away_starter is not None
            and home_starter.prior_starts > 0
            and away_starter.prior_starts > 0
        )
        ready_after_settlement = (
            ev.status in {"upcoming", "live"}
            and ev.result is None
            and has_pregame_odds
            and has_provider_key
            and both_team_history
            and both_probable_starters
            and both_starter_history
        )

        gaps: list[str] = []
        if not has_provider_key:
            gaps.append("missing_mlb_provider_key")
        if not has_pregame_odds:
            gaps.append("missing_pregame_odds")
        if not both_team_history:
            gaps.append("missing_prior_team_logs")
        if not both_probable_starters:
            gaps.append("missing_probable_starters")
        elif not both_starter_history:
            gaps.append("missing_prior_starter_logs")
        if ev.status == "final" and ev.result is None:
            gaps.append("missing_result_row")
        if ev.status in {"upcoming", "live"} and ev.result is None and has_pregame_odds:
            gaps.append("awaiting_settlement")

        readiness_rows.append(
            MlbReadinessEvent(
                event_id=ev.id,
                start_time_utc=start_time,
                status=ev.status,
                home_team=TeamOut(id=ev.home_team_id, name=ev.home_team.name),
                away_team=TeamOut(id=ev.away_team_id, name=ev.away_team.name),
                has_provider_key=has_provider_key,
                pregame_quote_count=pregame_quote_count,
                has_pregame_odds=has_pregame_odds,
                home_team_logs_prior=home_team_logs,
                away_team_logs_prior=away_team_logs,
                home_starter=home_starter,
                away_starter=away_starter,
                both_probable_starters=both_probable_starters,
                both_team_history=both_team_history,
                both_starter_history=both_starter_history,
                ready_after_settlement=ready_after_settlement,
                gaps=gaps,
            )
        )

    settled_quoted_events = sum(
        1
        for row in readiness_rows
        if row.status == "final" and row.has_pregame_odds
    )
    settled_trainable_events = sum(
        1
        for row in readiness_rows
        if row.status == "final"
        and row.has_pregame_odds
        and row.has_provider_key
        and row.both_team_history
        and row.both_probable_starters
        and row.both_starter_history
    )
    pending_pregame_events = sum(
        1
        for row in readiness_rows
        if row.status in {"upcoming", "live"} and row.has_pregame_odds
    )
    warnings: list[str] = []
    if not readiness_rows:
        warnings.append("No MLB events found in the readiness window.")
    if settled_trainable_events == 0:
        warnings.append("No settled MLB events with pregame odds are modelable yet.")

    summary = MlbReadinessSummary(
        sport=sport,
        league_key=league_key,
        window_start_utc=window_start,
        window_end_utc=window_end,
        visible_events=len(readiness_rows),
        events_with_provider_key=sum(1 for row in readiness_rows if row.has_provider_key),
        events_with_pregame_odds=sum(1 for row in readiness_rows if row.has_pregame_odds),
        events_with_both_probable_starters=sum(
            1 for row in readiness_rows if row.both_probable_starters
        ),
        events_with_both_team_history=sum(1 for row in readiness_rows if row.both_team_history),
        events_with_both_starter_history=sum(
            1 for row in readiness_rows if row.both_starter_history
        ),
        pending_pregame_events=pending_pregame_events,
        settled_quoted_events=settled_quoted_events,
        settled_trainable_events=settled_trainable_events,
        ready_after_settlement_events=sum(
            1 for row in readiness_rows if row.ready_after_settlement
        ),
    )
    return MlbReadinessResponse(
        generated_at_utc=now,
        summary=summary,
        events=readiness_rows,
        warnings=warnings,
    )


@app.get("/analysis/mlb/market-readiness", response_model=MlbMarketReadinessResponse)
def mlb_market_readiness(
    sport: str = Query("baseball_mlb", description="Configured MLB sport key"),
    days_back: int = Query(30, ge=0, le=365),
    days_forward: int = Query(7, ge=1, le=60),
    db: Session = Depends(get_db),
):
    """
    Read-only MLB market readiness by sportsbook market.

    This only inspects local tables and the latest local strict OOF artifact. It does not call
    provider APIs or spend odds quota.
    """
    if sport != "baseball_mlb":
        raise HTTPException(
            status_code=400,
            detail="MLB market readiness currently supports baseball_mlb only",
        )
    league_key = _league_key_for_request_sport(sport)
    from dataclasses import asdict

    from dk_ncaab.analysis.mlb_market_readiness import build_mlb_market_readiness

    result = build_mlb_market_readiness(
        db,
        sport=sport,
        league_key=league_key,
        days_back=days_back,
        days_forward=days_forward,
    )
    return MlbMarketReadinessResponse(**asdict(result))


@app.get("/analysis/mlb/evidence-growth/latest", response_model=MlbEvidenceGrowthLatestResponse)
def mlb_evidence_growth_latest():
    """Read the latest local MLB evidence growth snapshot without provider calls."""
    from dk_ncaab.analysis.mlb_evidence_growth import read_latest_mlb_evidence_growth

    latest = read_latest_mlb_evidence_growth()
    if not latest:
        return MlbEvidenceGrowthLatestResponse(
            available=False,
            warnings=["No MLB evidence growth snapshot has been written yet."],
        )
    return MlbEvidenceGrowthLatestResponse(available=True, **latest)


@app.get("/games", response_model=GameListResponse)
def list_games(
    date: str | None = Query(None, description="Date YYYY-MM-DD (default today)"),
    team: str | None = Query(None, description="Filter by team name (substring)"),
    status: str | None = Query(None, description="Filter by status"),
    sport: str | None = Query(None, description="Filter by configured sport key"),
    db: Session = Depends(get_db),
):
    """
    List games for a date with pre-game open / close lines.
    """
    day_start, day_end = _parse_date(date)

    q = select(Event).where(
        Event.start_time_utc >= day_start,
        Event.start_time_utc < day_end,
    ).order_by(Event.start_time_utc)

    if sport:
        league_key = _league_key_for_request_sport(sport)
        q = q.join(League, League.id == Event.league_id).where(League.key == league_key)

    if status:
        q = q.where(Event.status == status)

    events = list(db.execute(q).scalars())

    # Optional team filter
    if team:
        team_lower = team.lower()
        filtered = []
        for ev in events:
            ht = db.get(Team, ev.home_team_id)
            at = db.get(Team, ev.away_team_id)
            if (ht and team_lower in ht.name.lower()) or (at and team_lower in at.name.lower()):
                filtered.append(ev)
        events = filtered

    games: list[GameSummary] = []
    for ev in events:
        home = _team_out(db, ev.home_team_id)
        away = _team_out(db, ev.away_team_id)
        score_h = ev.result.home_score if ev.result else None
        score_a = ev.result.away_score if ev.result else None

        open_lines, close_lines = _get_pregame_lines(db, ev.id, ev.start_time_utc)

        games.append(GameSummary(
            event_id=ev.id,
            start_time_utc=ev.start_time_utc,
            status=ev.status,
            home_team=home,
            away_team=away,
            home_score=score_h,
            away_score=score_a,
            open_lines=open_lines,
            close_lines=close_lines,
        ))

    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
    display_date = day_start.astimezone(_ET).strftime("%Y-%m-%d")

    return GameListResponse(
        date=display_date,
        count=len(games),
        games=games,
    )


# ── GET /teams ──────────────────────────────────────────────────

@app.get("/teams", response_model=TeamListResponse)
def list_teams(
    q: str | None = Query(None, description="Search by team name"),
    sport: str | None = Query(None, description="Filter by configured sport key"),
    db: Session = Depends(get_db),
):
    """List all teams, optionally filtered by name substring."""
    stmt = select(Team).order_by(Team.name)
    if sport:
        league_key = _league_key_for_request_sport(sport)
        stmt = stmt.join(League, League.id == Team.league_id).where(League.key == league_key)
    if q:
        stmt = stmt.where(Team.name.ilike(f"%{q}%"))
    teams = [TeamOut(id=t.id, name=t.name) for t in db.execute(stmt).scalars()]
    return TeamListResponse(teams=teams)


@app.get("/standings", response_model=StandingsResponse)
def standings(
    sport: str = Query("basketball_ncaab", description="Sport key for standings"),
    db: Session = Depends(get_db),
):
    league_key = _league_key_for_request_sport(sport)

    teams = db.execute(
        select(Team)
        .join(League, League.id == Team.league_id)
        .where(League.key == league_key)
        .order_by(Team.name)
    ).scalars().all()

    rows: list[StandingsRow] = []
    for t in teams:
        finals = db.execute(
            select(Event)
            .where(
                and_(
                    or_(Event.home_team_id == t.id, Event.away_team_id == t.id),
                    Event.status == "final",
                )
            )
            .order_by(Event.start_time_utc.desc())
        ).scalars().all()

        wins = losses = 0
        ats_w = ats_l = ats_p = 0
        ou_o = ou_u = ou_p = 0

        for ev in finals:
            is_home = ev.home_team_id == t.id
            if not ev.result:
                continue

            team_score = ev.result.home_score if is_home else ev.result.away_score
            opp_score = ev.result.away_score if is_home else ev.result.home_score
            if team_score > opp_score:
                wins += 1
            else:
                losses += 1

            _, close_lines = _get_pregame_lines(db, ev.id, ev.start_time_utc)
            if close_lines and close_lines.spread is not None:
                team_spread = close_lines.spread if is_home else -close_lines.spread
                ats_margin = (team_score - opp_score) + team_spread
                if ats_margin > 0:
                    ats_w += 1
                elif ats_margin < 0:
                    ats_l += 1
                else:
                    ats_p += 1

            if close_lines and close_lines.total is not None:
                gtotal = team_score + opp_score
                if gtotal > close_lines.total:
                    ou_o += 1
                elif gtotal < close_lines.total:
                    ou_u += 1
                else:
                    ou_p += 1

        games = wins + losses
        win_pct = round((wins / games), 3) if games > 0 else 0.0
        rows.append(
            StandingsRow(
                team_id=t.id,
                team_name=t.name,
                wins=wins,
                losses=losses,
                win_pct=win_pct,
                ats_wins=ats_w,
                ats_losses=ats_l,
                ats_pushes=ats_p,
                ou_overs=ou_o,
                ou_unders=ou_u,
                ou_pushes=ou_p,
            )
        )

    rows.sort(key=lambda r: (r.wins, r.win_pct), reverse=True)
    return StandingsResponse(sport=sport, count=len(rows), rows=rows)


# ── GET /teams/{team_id}/history ────────────────────────────────

@app.get("/teams/{team_id}/history", response_model=TeamHistoryResponse)
def team_history(team_id: int, db: Session = Depends(get_db)):
    """
    Full game history for a team: record, ATS, O/U, per-game lines & results.
    """
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")

    rows = _build_team_history_rows(db, team_id, limit=500)
    wins = sum(1 for row in rows if row.status == "final" and row.won is True)
    losses = sum(1 for row in rows if row.status == "final" and row.won is False)
    ats_w = sum(1 for row in rows if row.spread_result == "W")
    ats_l = sum(1 for row in rows if row.spread_result == "L")
    ats_p = sum(1 for row in rows if row.spread_result == "P")
    ou_o = sum(1 for row in rows if row.total_result == "O")
    ou_u = sum(1 for row in rows if row.total_result == "U")
    ou_p = sum(1 for row in rows if row.total_result == "P")

    record = f"{wins}-{losses}"
    ats_record = f"{ats_w}-{ats_l}-{ats_p}"
    ou_record = f"{ou_o}-{ou_u}-{ou_p}"

    return TeamHistoryResponse(
        team=TeamOut(id=team.id, name=team.name),
        record=record,
        ats_record=ats_record,
        ou_record=ou_record,
        games=rows,
    )


# ── GET /game/{event_id}/summary ────────────────────────────────

@app.get("/game/{event_id}/summary", response_model=GameDetailSummary)
def game_summary(event_id: int, db: Session = Depends(get_db)):
    """
    Full game detail with snapshots across all markets (§15.2).
    """
    ev = db.get(Event, event_id)
    if not ev:
        raise HTTPException(404, "Event not found")

    home = _team_out(db, ev.home_team_id)
    away = _team_out(db, ev.away_team_id)

    # Build snapshots for all market/side combos
    combos = [
        ("moneyline", "home"), ("moneyline", "away"),
        ("spread", "home"), ("spread", "away"),
        ("total", "over"), ("total", "under"),
    ]
    snapshots: dict[str, list[SnapshotOut]] = {}
    for market, side in combos:
        key = f"{market}_{side}"
        ss = get_snapshot_set(db, event_id, market, side)
        snaps = []
        for anchor_name, snap in [
            ("OPEN", ss.OPEN), ("T60", ss.T60), ("T30", ss.T30), ("CLOSE", ss.CLOSE),
        ]:
            if snap:
                snaps.append(SnapshotOut(
                    anchor=anchor_name,
                    implied_probability=snap.implied_probability,
                    line=snap.line,
                    price_american=snap.price_american,
                    collected_at_utc=snap.collected_at_utc,
                ))
        snapshots[key] = snaps

    # KenPom + AP from spread/home features
    kp_spread = None
    ap_home = None
    ap_away = None
    try:
        fr = build_features(db, event_id, "spread", "home")
        kp_spread = fr.kenpom_expected_spread
        ap_home = fr.ap_rank_home
        ap_away = fr.ap_rank_away
    except Exception:
        pass

    return GameDetailSummary(
        event_id=ev.id,
        start_time_utc=ev.start_time_utc,
        status=ev.status,
        home_team=home,
        away_team=away,
        home_score=ev.result.home_score if ev.result else None,
        away_score=ev.result.away_score if ev.result else None,
        snapshots=snapshots,
        kenpom_expected_spread=kp_spread,
        ap_rank_home=ap_home,
        ap_rank_away=ap_away,
    )


# ── GET /game/{event_id}/timeseries ─────────────────────────────

@app.get("/game/{event_id}/timeseries", response_model=GameTimeseries)
def game_timeseries(event_id: int, db: Session = Depends(get_db)):
    """
    Full raw price + splits time series for charting (§15.2).
    Each point is flagged is_live if collected after game start.
    """
    ev = db.get(Event, event_id)
    if not ev:
        raise HTTPException(404, "Event not found")

    start = ev.start_time_utc

    # Odds timeseries
    stmt = (
        select(OddsQuote)
        .where(OddsQuote.event_id == event_id)
        .order_by(OddsQuote.collected_at_utc)
    )
    odds_rows = []
    for q in db.execute(stmt).scalars():
        is_live = q.collected_at_utc >= start
        odds_rows.append(TimeseriesPoint(
            collected_at_utc=q.collected_at_utc,
            market=q.market,
            side=q.side,
            price_american=q.price_american,
            implied_probability=q.implied_probability,
            line=q.line,
            is_live=is_live,
        ))

    # Splits timeseries
    stmt2 = (
        select(SplitsQuote)
        .where(SplitsQuote.event_id == event_id)
        .order_by(SplitsQuote.collected_at_utc)
    )
    splits_rows = [
        SplitsTimeseriesPoint(
            collected_at_utc=s.collected_at_utc,
            market=s.market,
            side=s.side,
            bets_pct=s.bets_pct,
            handle_pct=s.handle_pct,
        )
        for s in db.execute(stmt2).scalars()
    ]

    return GameTimeseries(
        event_id=event_id,
        start_time_utc=ev.start_time_utc,
        odds=odds_rows,
        splits=splits_rows,
    )


# ── GET /game/{event_id}/features ───────────────────────────────

@app.get("/game/{event_id}/features")
def game_features(event_id: int, db: Session = Depends(get_db)):
    """
    All engineered features for an event — 6 rows (market × side).
    """
    ev = db.get(Event, event_id)
    if not ev:
        raise HTTPException(404, "Event not found")

    combos = [
        ("moneyline", "home"), ("moneyline", "away"),
        ("spread", "home"), ("spread", "away"),
        ("total", "over"), ("total", "under"),
    ]
    rows = []
    for market, side in combos:
        try:
            fr = build_features(db, event_id, market, side)
            d = fr.to_dict()
            if d.get("start_time_utc"):
                d["start_time_utc"] = d["start_time_utc"].isoformat()
            rows.append(d)
        except Exception as e:
            log.warning("Feature build error %s/%s: %s", market, side, e)

    return {"event_id": event_id, "features": rows}


# ── GET /game/{event_id}/model ──────────────────────────────────

@app.get("/game/{event_id}/model", response_model=ModelPanelResponse)
def game_model(event_id: int, db: Session = Depends(get_db)):
    """
    Run the latest trained model on this event and return signals (§15.3).
    """
    import pandas as pd

    ev = db.get(Event, event_id)
    if not ev:
        raise HTTPException(404, "Event not found")

    # Try to find a trained model
    try:
        from dk_ncaab.analysis.model_store import get_latest_model, load_model
        from dk_ncaab.analysis.models_outcome import detect_mispricings
    except ImportError:
        return ModelPanelResponse(event_id=event_id, signals=[], features_used=[])

    model_path = get_latest_model("lgbm_close") or get_latest_model("ridge_close")
    if not model_path:
        return ModelPanelResponse(event_id=event_id, signals=[], features_used=[])

    bundle = load_model(model_path)
    model = bundle["model"]
    feats = bundle["features"]
    scaler = bundle.get("scaler")

    combos = [
        ("moneyline", "home"), ("moneyline", "away"),
        ("spread", "home"), ("spread", "away"),
        ("total", "over"), ("total", "under"),
    ]
    rows = []
    for market, side in combos:
        try:
            fr = build_features(db, event_id, market, side)
            rows.append(fr.to_dict())
        except Exception:
            pass

    if not rows:
        return ModelPanelResponse(
            event_id=event_id, signals=[], features_used=feats,
            model_name=model_path.stem,
        )

    df = pd.DataFrame(rows)
    cols = [c for c in feats if c in df.columns]
    scoreable = df.dropna(subset=cols)

    if scoreable.empty:
        return ModelPanelResponse(
            event_id=event_id, signals=[], features_used=feats,
            model_name=model_path.stem,
        )

    X = scoreable[cols]
    if scaler:
        X = pd.DataFrame(scaler.transform(X), columns=cols, index=X.index)
    predicted_close = pd.Series(model.predict(X), index=scoreable.index)

    mispricings = detect_mispricings(scoreable, predicted_close, z_threshold=1.0)

    signals = [
        ModelSignal(
            event_id=s.event_id,
            market=s.market,
            side=s.side,
            market_implied=s.market_implied,
            model_implied=s.model_implied,
            residual=s.residual,
            z_score=s.z_score,
            model_expected_value=s.model_expected_value,
        )
        for s in mispricings
    ]

    return ModelPanelResponse(
        event_id=event_id,
        signals=signals,
        features_used=feats,
        model_name=model_path.stem,
    )


# ── GET /backtest/summary ──────────────────────────────────────

@app.get("/analysis/entry-ev/latest", response_model=EntryEvArtifactLatestResponse)
def latest_entry_ev_artifact():
    """Return the latest strict OOF entry-EV artifact manifest, if present."""
    import pandas as pd

    from dk_ncaab.analysis.oof_entry_ev import read_latest_entry_ev

    payload = read_latest_entry_ev()
    if not payload:
        return EntryEvArtifactLatestResponse(
            available=False,
            warnings=["No OOF entry-EV artifact found. Run `python -m dk_ncaab oof-entry-ev`."],
        )
    recommendations: list[dict[str, object]] = []
    predictions_path = payload.get("predictions_path")
    if predictions_path and Path(str(predictions_path)).exists():
        try:
            predictions = pd.read_parquet(predictions_path)
            if "recommended" in predictions.columns:
                recommended = predictions[predictions["recommended"].fillna(False)].copy()
                if not recommended.empty and "model_ev_units" in recommended.columns:
                    recommended = recommended.sort_values("model_ev_units", ascending=False)
                keep_cols = [
                    "event_id",
                    "start_time_utc",
                    "market",
                    "side",
                    "participant_name",
                    "participant_entity_type",
                    "entry_line",
                    "entry_price_american",
                    "oof_win_prob",
                    "break_even_prob",
                    "model_ev_units",
                    "settlement_status",
                    "actual_profit_units_1u",
                ]
                rec_frame = recommended[[col for col in keep_cols if col in recommended.columns]].head(10)
                rec_frame = rec_frame.where(pd.notna(rec_frame), None)
                recommendations = json.loads(
                    rec_frame.to_json(orient="records", date_format="iso")
                )
        except Exception:
            payload.setdefault("warnings", []).append("Could not read latest prediction rows.")
    payload["recommendations"] = recommendations
    if "promotion_status" not in payload:
        min_oof_rows = 100
        min_settled_events = 30
        min_posted_line_samples = 10
        promotion_gaps: list[str] = []
        if int(payload.get("rows_predicted") or 0) < min_oof_rows:
            promotion_gaps.append("oof_sample_below_gate")
        if int(payload.get("events_modelable") or 0) < min_settled_events:
            promotion_gaps.append("settled_events_below_gate")
        if int(payload.get("recommended_count") or 0) <= 0:
            promotion_gaps.append("no_recommended_rows")
        if float(payload.get("recommended_roi") or 0.0) <= 0:
            promotion_gaps.append("non_positive_recommended_roi")
        payload["promotion_status"] = "sample_sensitive" if promotion_gaps else "promotable"
        payload["promotion_gaps"] = promotion_gaps
        payload["min_oof_rows"] = min_oof_rows
        payload["min_settled_events"] = min_settled_events
        payload["min_posted_line_samples"] = min_posted_line_samples
    return EntryEvArtifactLatestResponse(available=True, **payload)


@app.get("/backtest/summary", response_model=BacktestSummaryResponse)
def backtest_summary(db: Session = Depends(get_db)):
    """
    Run the full backtest suite and return results (§15.4).
    """
    from dk_ncaab.analysis.dataset_build import build_dataset
    from dk_ncaab.analysis.backtest import run_backtest_suite

    df = build_dataset(session=db)
    if df.empty:
        return BacktestSummaryResponse(n_events=0, strategies=[])

    results = run_backtest_suite(df)
    strategies = [
        BacktestStrategyResult(
            strategy=r.strategy,
            n_bets=r.n_bets,
            mean_clv=r.mean_clv,
            median_clv=r.median_clv,
            clv_positive_rate=r.clv_positive_rate,
            total_roi=r.total_roi,
            win_rate=r.win_rate,
            max_drawdown=r.max_drawdown,
            sharpe_ratio=r.sharpe_ratio,
        )
        for r in results
    ]

    n_events = db.execute(
        select(func.count(Event.id)).where(Event.status == "final")
    ).scalar() or 0

    return BacktestSummaryResponse(n_events=n_events, strategies=strategies)


# ── GET /status ─────────────────────────────────────────────────

@app.get("/status", response_model=PipelineStatus)
def pipeline_status(db: Session = Depends(get_db)):
    """Pipeline health check with pre-game vs live quote breakdown."""
    now = datetime.now(timezone.utc)
    pre = db.execute(
        select(func.count(OddsQuote.id))
        .join(Event, Event.id == OddsQuote.event_id)
        .where(OddsQuote.book == "draftkings")
        .where(OddsQuote.collected_at_utc < Event.start_time_utc)
    ).scalar() or 0
    total_quotes = db.query(OddsQuote).count()
    latest_odds_quote_utc = db.execute(
        select(func.max(OddsQuote.collected_at_utc))
    ).scalar_one_or_none()

    odds_data_age_min: int | None = None
    if latest_odds_quote_utc is not None:
        if latest_odds_quote_utc.tzinfo is None:
            latest_odds_quote_utc = latest_odds_quote_utc.replace(tzinfo=timezone.utc)
        odds_data_age_min = int((now - latest_odds_quote_utc).total_seconds() // 60)

    cfg = get_settings().odds_api
    configured_sports = cfg.active_sports()
    try:
        odds_usage = get_odds_usage_summary(
            db,
            monthly_budget=cfg.monthly_request_budget,
            reserve_requests=cfg.reserve_requests,
            now=now,
        )
    except SAOperationalError as exc:
        log.warning("Odds API usage query failed; migration may be pending: %s", exc)
        odds_usage = OddsUsageSummary(
            monthly_budget=cfg.monthly_request_budget,
            reserve_requests=cfg.reserve_requests,
            recorded_requests_month=0,
            requests_used=0,
            requests_remaining=cfg.monthly_request_budget,
            last_request_utc=None,
            requests_by_sport={},
        )

    quote_rows = db.execute(
        select(League.key, func.count(OddsQuote.id))
        .join(Event, Event.league_id == League.id)
        .join(OddsQuote, OddsQuote.event_id == Event.id)
        .group_by(League.key)
    ).all()
    odds_quotes_by_league = {key: int(count) for key, count in quote_rows}

    runs = _read_recent_runs(limit=300)
    last_run_status = runs[0].status if runs else None
    last_run_completed_utc = runs[0].completed_at_utc if runs else None
    cutoff = now - timedelta(hours=24)
    failed_runs_24h = sum(1 for r in runs if r.status != "success" and r.completed_at_utc >= cutoff)
    settled_events_with_pregame_odds = db.execute(
        select(func.count(func.distinct(Event.id)))
        .join(EventResult, EventResult.event_id == Event.id)
        .join(OddsQuote, OddsQuote.event_id == Event.id)
        .where(Event.status == "final")
        .where(OddsQuote.book == "draftkings")
        .where(OddsQuote.collected_at_utc < Event.start_time_utc)
    ).scalar() or 0
    strict_entry_ev_events_modelable = 0
    strict_entry_ev_rows_predicted = 0
    try:
        from dk_ncaab.analysis.oof_entry_ev import read_latest_entry_ev

        if artifact := read_latest_entry_ev():
            strict_entry_ev_events_modelable = int(artifact.get("events_modelable", 0) or 0)
            strict_entry_ev_rows_predicted = int(artifact.get("rows_predicted", 0) or 0)
    except Exception:
        log.exception("Failed to read latest strict entry-EV artifact for status")

    return PipelineStatus(
        teams=db.query(Team).count(),
        events_total=db.query(Event).count(),
        events_upcoming=db.query(Event).filter_by(status="upcoming").count(),
        events_final=db.query(Event).filter_by(status="final").count(),
        results=db.query(EventResult).count(),
        odds_quotes=total_quotes,
        odds_quotes_pregame=pre,
        odds_quotes_live=total_quotes - pre,
        splits_quotes=db.query(SplitsQuote).count(),
        kenpom_ratings=db.query(KenPomRating).count(),
        ap_rankings=db.query(APRanking).count(),
        trainable_events=settled_events_with_pregame_odds,
        settled_events_with_pregame_odds=settled_events_with_pregame_odds,
        strict_entry_ev_events_modelable=strict_entry_ev_events_modelable,
        strict_entry_ev_rows_predicted=strict_entry_ev_rows_predicted,
        configured_sports=configured_sports,
        odds_api_monthly_budget=odds_usage.monthly_budget,
        odds_api_reserve_requests=odds_usage.reserve_requests,
        odds_api_requests_recorded_month=odds_usage.recorded_requests_month,
        odds_api_requests_used=odds_usage.requests_used,
        odds_api_requests_remaining=odds_usage.requests_remaining,
        odds_api_last_request_utc=odds_usage.last_request_utc,
        odds_api_requests_by_sport=odds_usage.requests_by_sport,
        latest_odds_quote_utc=latest_odds_quote_utc,
        odds_data_age_min=odds_data_age_min,
        odds_stale=(odds_data_age_min is None) or (odds_data_age_min > 15),
        last_run_status=last_run_status,
        last_run_completed_utc=last_run_completed_utc,
        failed_runs_24h=failed_runs_24h,
        odds_quotes_by_league=odds_quotes_by_league,
    )


@app.get("/runs", response_model=list[IngestionRunOut])
def runs(limit: int = Query(default=20, ge=1, le=200)):
    return _read_recent_runs(limit=limit)


def _read_recent_runs(limit: int) -> list[IngestionRunOut]:
    if not _RUNS_FILE.exists():
        return []

    rows: list[IngestionRunOut] = []
    with _RUNS_FILE.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
            rows.append(IngestionRunOut(**payload))
            if len(rows) >= limit:
                break
        except Exception:
            continue

    return rows
