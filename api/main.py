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
    BoardGame,
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
    MlbReadinessEvent,
    MlbReadinessResponse,
    MlbReadinessSummary,
    MlbStarterReadiness,
    BacktestSummaryResponse,
    BacktestStrategyResult,
    PipelineStatus,
    IngestionRunOut,
)
from dk_ncaab.db.models import (
    Event, Team, EventResult, OddsQuote, SplitsQuote, League,
    EventProviderKey, KenPomRating, APRanking, MlbPlayerGameLog,
    MlbProbableStarter, MlbTeamGameLog, Player,
)
from dk_ncaab.config.settings import get_settings
from dk_ncaab.config.sports import get_sport, league_key_for_sport, sport_for_league_key, ui_sport_keys
from dk_ncaab.collectors.odds_api import OddsUsageSummary, get_odds_usage_summary
from dk_ncaab.etl.snapshots import get_snapshot_set
from dk_ncaab.etl.features import build_features

log = logging.getLogger(__name__)

_RUNS_FILE = Path("artifacts/state/runs.jsonl")

app = FastAPI(
    title="Lock-N-Key Sports Research API",
    description="Read-only API for private multi-sport odds research.",
    version="0.2.0",
)

_SETTINGS = get_settings()

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
            OddsQuote.market == market,
            OddsQuote.side == side,
            OddsQuote.collected_at_utc < start_time_utc,
        )
        .order_by(OddsQuote.collected_at_utc.asc(), OddsQuote.id.asc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


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
    now: datetime,
) -> BoardLineOption | None:
    if not latest:
        return None

    collected = _ensure_utc(latest.collected_at_utc)
    age_min = _age_minutes(now, collected)
    is_live = bool(collected and collected >= _ensure_utc(ev.start_time_utc))
    stale_limit = 15 if ev.status == "live" or is_live else 60
    label, team_name = _line_label(home, away, market, side)

    return BoardLineOption(
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
        implied_move_from_open=(
            latest.implied_probability - opened.implied_probability
            if opened
            and latest.implied_probability is not None
            and opened.implied_probability is not None
            else None
        ),
        line_move_from_open=(
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
    return _quote_pair_to_board_line(ev, home, away, market, side, latest, opened, now)


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
    dict[tuple[int, str, str], SplitsQuote],
]:
    """Fetch board teams, odds, and splits in batches for a compact payload."""
    if not events:
        return {}, {}, {}, {}

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
    quote_stmt = (
        select(OddsQuote)
        .where(OddsQuote.event_id.in_(event_ids))
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
        if collected and start and collected < start and key not in open_quotes:
            open_quotes[key] = quote

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

    return teams, latest_quotes, open_quotes, latest_splits


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
    teams, latest_quotes, open_quotes, latest_splits = _prefetch_board_context(session, [ev])
    return _build_board_game_from_prefetch(
        ev,
        now,
        teams,
        latest_quotes,
        open_quotes,
        latest_splits,
    )


def _build_board_game_from_prefetch(
    ev: Event,
    now: datetime,
    teams: dict[int, TeamOut],
    latest_quotes: dict[tuple[int, str, str], OddsQuote],
    open_quotes: dict[tuple[int, str, str], OddsQuote],
    latest_splits: dict[tuple[int, str, str], SplitsQuote],
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
    )


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


def _team_research_metrics(session: Session, team_id: int, limit: int = 8) -> TeamResearchMetrics:
    team = _team_out(session, team_id)
    stmt = (
        select(Event)
        .where(or_(Event.home_team_id == team_id, Event.away_team_id == team_id))
        .order_by(Event.start_time_utc.desc())
        .limit(limit)
    )
    events = list(session.execute(stmt).scalars())
    wins = losses = 0
    ats_w = ats_l = ats_p = 0
    ou_o = ou_u = ou_p = 0
    rows: list[TeamGameRow] = []

    for ev in events:
        is_home = ev.home_team_id == team_id
        opp_id = ev.away_team_id if is_home else ev.home_team_id
        opponent = _team_out(session, opp_id)
        open_lines, close_lines = _get_pregame_lines(session, ev.id, ev.start_time_utc)

        team_score = opp_score = None
        won = None
        if ev.result:
            team_score = ev.result.home_score if is_home else ev.result.away_score
            opp_score = ev.result.away_score if is_home else ev.result.home_score
            won = team_score > opp_score
            if ev.status == "final":
                if won:
                    wins += 1
                else:
                    losses += 1

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

        spread_result = total_result = None
        if ev.status == "final" and team_score is not None and opp_score is not None:
            margin = team_score - opp_score
            if close_spread is not None:
                ats_margin = margin + close_spread
                if ats_margin > 0:
                    spread_result = "W"
                    ats_w += 1
                elif ats_margin < 0:
                    spread_result = "L"
                    ats_l += 1
                else:
                    spread_result = "P"
                    ats_p += 1
            if close_total is not None:
                game_total = team_score + opp_score
                if game_total > close_total:
                    total_result = "O"
                    ou_o += 1
                elif game_total < close_total:
                    total_result = "U"
                    ou_u += 1
                else:
                    total_result = "P"
                    ou_p += 1

        rows.append(TeamGameRow(
            event_id=ev.id,
            start_time_utc=ev.start_time_utc,
            opponent=opponent,
            is_home=is_home,
            status=ev.status,
            team_score=team_score,
            opp_score=opp_score,
            won=won,
            open_spread=open_spread,
            close_spread=close_spread,
            open_total=open_total,
            close_total=close_total,
            open_ml=open_ml,
            close_ml=close_ml,
            spread_result=spread_result,
            total_result=total_result,
        ))

    return TeamResearchMetrics(
        team=team,
        record=f"{wins}-{losses}",
        ats_record=f"{ats_w}-{ats_l}-{ats_p}",
        ou_record=f"{ou_o}-{ou_u}-{ou_p}",
        recent_games=rows,
    )


def _event_research_response(session: Session, ev: Event, now: datetime) -> GameResearchResponse:
    board_game = _build_board_game(session, ev, now)
    snapshots = _build_snapshot_map(session, ev.id)
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
    player_stats_note = (
        "Player stat tables are not wired to a free provider yet. "
        "Use this space for NBA/MLB player logs once a source is added."
    )
    warnings.append(player_stats_note)

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
        team_metrics={
            "home": _team_research_metrics(session, ev.home_team_id),
            "away": _team_research_metrics(session, ev.away_team_id),
        },
        player_stats=[
            PlayerStatRow(
                player_name="Player stats source pending",
                note=player_stats_note,
            )
        ],
        player_stats_note=player_stats_note,
        warnings=warnings,
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

    teams, latest_quotes, open_quotes, latest_splits = _prefetch_board_context(db, events)
    games = [
        _build_board_game_from_prefetch(ev, now, teams, latest_quotes, open_quotes, latest_splits)
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
    """Batch research payloads for expanded rows or watchlist refreshes."""
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

def _count_pregame_quotes(db: Session, event_id: int, start_time_utc: datetime) -> int:
    return int(
        db.execute(
            select(func.count(OddsQuote.id))
            .where(OddsQuote.event_id == event_id)
            .where(OddsQuote.collected_at_utc <= start_time_utc)
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
            .where(OddsQuote.collected_at_utc <= start_time_utc)
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

    settled_trainable_events = int(
        db.execute(
            select(func.count(func.distinct(Event.id)))
            .join(League, League.id == Event.league_id)
            .join(EventResult, EventResult.event_id == Event.id)
            .join(OddsQuote, OddsQuote.event_id == Event.id)
            .where(League.key == league_key)
            .where(Event.status == "final")
            .where(OddsQuote.collected_at_utc <= Event.start_time_utc)
        ).scalar()
        or 0
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

    stmt = (
        select(Event)
        .where(or_(Event.home_team_id == team_id, Event.away_team_id == team_id))
        .order_by(Event.start_time_utc.desc())
    )
    events = list(db.execute(stmt).scalars())

    wins, losses = 0, 0
    ats_w, ats_l, ats_p = 0, 0, 0
    ou_o, ou_u, ou_p = 0, 0, 0

    rows: list[TeamGameRow] = []
    for ev in events:
        is_home = ev.home_team_id == team_id
        opp_id = ev.away_team_id if is_home else ev.home_team_id
        opponent = _team_out(db, opp_id)

        team_score = opp_score = None
        won = None
        if ev.result:
            if is_home:
                team_score = ev.result.home_score
                opp_score = ev.result.away_score
            else:
                team_score = ev.result.away_score
                opp_score = ev.result.home_score
            if team_score is not None and opp_score is not None:
                won = team_score > opp_score
                if ev.status == "final":
                    if won:
                        wins += 1
                    else:
                        losses += 1

        # Get pre-game lines
        open_lines, close_lines = _get_pregame_lines(db, ev.id, ev.start_time_utc)

        # Compute from team's perspective
        open_spread = close_spread = None
        open_total = close_total = None
        open_ml = close_ml = None

        if open_lines:
            if open_lines.spread is not None:
                # spread is stored from home perspective; flip for away
                open_spread = open_lines.spread if is_home else (-open_lines.spread if open_lines.spread else None)
            open_total = open_lines.total
            open_ml = open_lines.ml_home if is_home else open_lines.ml_away

        if close_lines:
            if close_lines.spread is not None:
                close_spread = close_lines.spread if is_home else (-close_lines.spread if close_lines.spread else None)
            close_total = close_lines.total
            close_ml = close_lines.ml_home if is_home else close_lines.ml_away

        # ATS result
        spread_result = None
        total_result = None
        if ev.status == "final" and team_score is not None and opp_score is not None:
            margin = team_score - opp_score
            if close_spread is not None:
                ats_margin = margin + close_spread  # spread is negative for favorites
                if ats_margin > 0:
                    spread_result = "W"
                    ats_w += 1
                elif ats_margin < 0:
                    spread_result = "L"
                    ats_l += 1
                else:
                    spread_result = "P"
                    ats_p += 1

            if close_total is not None:
                game_total = team_score + opp_score
                if game_total > close_total:
                    total_result = "O"
                    ou_o += 1
                elif game_total < close_total:
                    total_result = "U"
                    ou_u += 1
                else:
                    total_result = "P"
                    ou_p += 1

        rows.append(TeamGameRow(
            event_id=ev.id,
            start_time_utc=ev.start_time_utc,
            opponent=opponent,
            is_home=is_home,
            status=ev.status,
            team_score=team_score,
            opp_score=opp_score,
            won=won,
            open_spread=open_spread,
            close_spread=close_spread,
            open_total=open_total,
            close_total=close_total,
            open_ml=open_ml,
            close_ml=close_ml,
            spread_result=spread_result,
            total_result=total_result,
        ))

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
    from dk_ncaab.analysis.oof_entry_ev import read_latest_entry_ev

    payload = read_latest_entry_ev()
    if not payload:
        return EntryEvArtifactLatestResponse(
            available=False,
            warnings=["No OOF entry-EV artifact found. Run `python -m dk_ncaab oof-entry-ev`."],
        )
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
        trainable_events=db.execute(
            select(func.count(func.distinct(Event.id)))
            .join(EventResult, EventResult.event_id == Event.id)
            .join(OddsQuote, OddsQuote.event_id == Event.id)
            .where(Event.status == "final")
            .where(OddsQuote.collected_at_utc <= Event.start_time_utc)
        ).scalar() or 0,
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
