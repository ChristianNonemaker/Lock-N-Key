"""
FastAPI read-only API for DK NCAAB research UI.

All endpoints are GET-only — no mutations.

Start:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import (
    GameListResponse,
    GameSummary,
    GameDetailSummary,
    GameTimeseries,
    LinesSnapshot,
    TeamOut,
    TeamListResponse,
    TeamGameRow,
    TeamHistoryResponse,
    SnapshotOut,
    TimeseriesPoint,
    SplitsTimeseriesPoint,
    ModelPanelResponse,
    ModelSignal,
    BacktestSummaryResponse,
    BacktestStrategyResult,
    PipelineStatus,
)
from dk_ncaab.db.models import (
    Event, Team, EventResult, OddsQuote, SplitsQuote,
    KenPomRating, APRanking,
)
from dk_ncaab.etl.snapshots import get_snapshot_set
from dk_ncaab.etl.features import build_features

log = logging.getLogger(__name__)

app = FastAPI(
    title="DK NCAAB Research API",
    description="Read-only API for NCAAB positive-EV research system.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── Helpers ─────────────────────────────────────────────────────

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


# ── GET /games ──────────────────────────────────────────────────

@app.get("/games", response_model=GameListResponse)
def list_games(
    date: str | None = Query(None, description="Date YYYY-MM-DD (default today)"),
    team: str | None = Query(None, description="Filter by team name (substring)"),
    status: str | None = Query(None, description="Filter by status"),
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
    db: Session = Depends(get_db),
):
    """List all teams, optionally filtered by name substring."""
    stmt = select(Team).order_by(Team.name)
    if q:
        stmt = stmt.where(Team.name.ilike(f"%{q}%"))
    teams = [TeamOut(id=t.id, name=t.name) for t in db.execute(stmt).scalars()]
    return TeamListResponse(teams=teams)


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
    pre = db.execute(
        select(func.count(OddsQuote.id))
        .join(Event, Event.id == OddsQuote.event_id)
        .where(OddsQuote.collected_at_utc < Event.start_time_utc)
    ).scalar() or 0
    total_quotes = db.query(OddsQuote).count()

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
            select(func.count(Event.id))
            .join(EventResult, EventResult.event_id == Event.id)
            .where(Event.status == "final")
        ).scalar() or 0,
    )
