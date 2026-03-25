"""
Pydantic response schemas for the read-only API.
"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel


# ── Game browser ────────────────────────────────────────────────

class TeamOut(BaseModel):
    id: int
    name: str


class LinesSnapshot(BaseModel):
    """One set of spread / total / moneyline at a point in time."""
    spread: float | None = None          # e.g. -3.5 (home perspective)
    spread_price: int | None = None      # e.g. -110
    total: float | None = None           # e.g. 145.5
    total_over_price: int | None = None
    total_under_price: int | None = None
    ml_home: int | None = None           # e.g. -150
    ml_away: int | None = None           # e.g. +130


class GameSummary(BaseModel):
    event_id: int
    start_time_utc: datetime
    status: str
    home_team: TeamOut
    away_team: TeamOut
    home_score: int | None = None
    away_score: int | None = None
    # Pre-game odds (OPEN = first snapshot, CLOSE = last before tip)
    open_lines: LinesSnapshot | None = None
    close_lines: LinesSnapshot | None = None


class GameListResponse(BaseModel):
    date: str
    count: int
    games: list[GameSummary]


# ── Team history ────────────────────────────────────────────────

class TeamGameRow(BaseModel):
    """One row in a team's history table."""
    event_id: int
    start_time_utc: datetime
    opponent: TeamOut
    is_home: bool
    status: str
    # Result (final only)
    team_score: int | None = None
    opp_score: int | None = None
    won: bool | None = None
    # Pre-game lines (from this team's perspective)
    open_spread: float | None = None
    close_spread: float | None = None
    open_total: float | None = None
    close_total: float | None = None
    open_ml: int | None = None
    close_ml: int | None = None
    # ATS / result vs line
    spread_result: str | None = None     # "W" / "L" / "P" (push)
    total_result: str | None = None      # "O" / "U" / "P"


class TeamHistoryResponse(BaseModel):
    team: TeamOut
    record: str               # e.g. "18-5"
    ats_record: str            # e.g. "12-10-1"
    ou_record: str             # e.g. "11-12"
    games: list[TeamGameRow]


class TeamListResponse(BaseModel):
    teams: list[TeamOut]


# ── Game detail ─────────────────────────────────────────────────

class SnapshotOut(BaseModel):
    anchor: str
    implied_probability: float | None = None
    line: float | None = None
    price_american: int | None = None
    collected_at_utc: datetime | None = None


class FeatureRow(BaseModel):
    """Flat feature dict — mirrors the dataclass but as JSON."""
    class Config:
        extra = "allow"


class TimeseriesPoint(BaseModel):
    collected_at_utc: datetime
    market: str
    side: str
    price_american: int
    implied_probability: float | None = None
    line: float | None = None
    is_live: bool = False       # True if collected after game start


class SplitsTimeseriesPoint(BaseModel):
    collected_at_utc: datetime
    market: str
    side: str
    bets_pct: float
    handle_pct: float


class GameTimeseries(BaseModel):
    event_id: int
    start_time_utc: datetime
    odds: list[TimeseriesPoint]
    splits: list[SplitsTimeseriesPoint]


class GameDetailSummary(BaseModel):
    event_id: int
    start_time_utc: datetime
    status: str
    home_team: TeamOut
    away_team: TeamOut
    home_score: int | None = None
    away_score: int | None = None
    snapshots: dict[str, list[SnapshotOut]]  # keyed by "{market}_{side}"
    kenpom_expected_spread: float | None = None
    ap_rank_home: int | None = None
    ap_rank_away: int | None = None


# ── Model panel ─────────────────────────────────────────────────

class ModelSignal(BaseModel):
    event_id: int
    market: str
    side: str
    market_implied: float
    model_implied: float
    residual: float
    z_score: float
    model_expected_value: float


class ModelPanelResponse(BaseModel):
    event_id: int
    signals: list[ModelSignal]
    features_used: list[str]
    model_name: str | None = None


# ── Backtest ────────────────────────────────────────────────────

class BacktestStrategyResult(BaseModel):
    strategy: str
    n_bets: int
    mean_clv: float
    median_clv: float
    clv_positive_rate: float
    total_roi: float
    win_rate: float | None = None
    max_drawdown: float
    sharpe_ratio: float | None = None


class BacktestSummaryResponse(BaseModel):
    n_events: int
    strategies: list[BacktestStrategyResult]


# ── Status ──────────────────────────────────────────────────────

class PipelineStatus(BaseModel):
    teams: int
    events_total: int
    events_upcoming: int
    events_final: int
    results: int
    odds_quotes: int
    odds_quotes_pregame: int
    odds_quotes_live: int
    splits_quotes: int
    kenpom_ratings: int
    ap_rankings: int
    trainable_events: int
