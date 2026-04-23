"""
Pydantic response schemas for the read-only API.
"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


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


class BoardLineOption(BaseModel):
    market: str
    side: str
    label: str
    team_name: str | None = None
    line: float | None = None
    price_american: int | None = None
    implied_probability: float | None = None
    collected_at_utc: datetime | None = None
    is_live: bool = False
    is_stale: bool = True
    open_line: float | None = None
    open_price_american: int | None = None
    implied_move_from_open: float | None = None
    line_move_from_open: float | None = None


class BoardSplitSummary(BaseModel):
    market: str
    side: str
    bets_pct: float | None = None
    handle_pct: float | None = None
    collected_at_utc: datetime | None = None


class BoardGame(BaseModel):
    event_id: int
    sport: str
    league_key: str
    start_time_utc: datetime
    status: str
    home_team: TeamOut
    away_team: TeamOut
    home_score: int | None = None
    away_score: int | None = None
    latest_quote_utc: datetime | None = None
    odds_age_min: int | None = None
    odds_stale: bool = True
    lines: list[BoardLineOption]
    split_summary: list[BoardSplitSummary]
    markets_available: list[str]
    flags: list[str]


class BoardResponse(BaseModel):
    generated_at_utc: datetime
    sport: str
    mode: str
    date: str | None
    count: int
    games: list[BoardGame]
    configured_sports: list[str]
    warnings: list[str]


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


class StandingsRow(BaseModel):
    team_id: int
    team_name: str
    wins: int
    losses: int
    win_pct: float
    ats_wins: int
    ats_losses: int
    ats_pushes: int
    ou_overs: int
    ou_unders: int
    ou_pushes: int


class StandingsResponse(BaseModel):
    sport: str
    count: int
    rows: list[StandingsRow]


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


class TeamResearchMetrics(BaseModel):
    team: TeamOut
    record: str
    ats_record: str
    ou_record: str
    recent_games: list[TeamGameRow]


class PlayerStatRow(BaseModel):
    player_name: str
    team_name: str | None = None
    position: str | None = None
    last_games: list[dict] = Field(default_factory=list)
    note: str | None = None


class GameResearchResponse(BaseModel):
    event_id: int
    sport: str
    league_key: str
    start_time_utc: datetime
    status: str
    home_team: TeamOut
    away_team: TeamOut
    home_score: int | None = None
    away_score: int | None = None
    latest_quote_utc: datetime | None = None
    odds_age_min: int | None = None
    odds_stale: bool = True
    lines: list[BoardLineOption]
    split_summary: list[BoardSplitSummary]
    snapshots: dict[str, list[SnapshotOut]]
    features: list[dict]
    team_metrics: dict[str, TeamResearchMetrics]
    player_stats: list[PlayerStatRow]
    player_stats_note: str
    warnings: list[str]


class GameResearchBatchResponse(BaseModel):
    generated_at_utc: datetime
    count: int
    events: list[GameResearchResponse]
    warnings: list[str] = Field(default_factory=list)


# ── Model panel ─────────────────────────────────────────────────

class MlbStarterReadiness(BaseModel):
    team_id: int
    player_id: int | None = None
    player_name: str | None = None
    prior_starts: int = 0


class MlbReadinessEvent(BaseModel):
    event_id: int
    start_time_utc: datetime
    status: str
    home_team: TeamOut
    away_team: TeamOut
    has_provider_key: bool
    pregame_quote_count: int
    has_pregame_odds: bool
    home_team_logs_prior: int
    away_team_logs_prior: int
    home_starter: MlbStarterReadiness | None = None
    away_starter: MlbStarterReadiness | None = None
    both_probable_starters: bool
    both_team_history: bool
    both_starter_history: bool
    ready_after_settlement: bool
    gaps: list[str] = Field(default_factory=list)


class MlbReadinessSummary(BaseModel):
    sport: str
    league_key: str
    window_start_utc: datetime
    window_end_utc: datetime
    visible_events: int
    events_with_provider_key: int
    events_with_pregame_odds: int
    events_with_both_probable_starters: int
    events_with_both_team_history: int
    events_with_both_starter_history: int
    pending_pregame_events: int
    settled_trainable_events: int
    ready_after_settlement_events: int


class MlbReadinessResponse(BaseModel):
    generated_at_utc: datetime
    summary: MlbReadinessSummary
    events: list[MlbReadinessEvent]
    warnings: list[str] = Field(default_factory=list)


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


class OofAnchorSummary(BaseModel):
    anchor: str
    rows_input: int
    rows_with_prediction: int
    feature_count: int
    artifact_path: str | None = None
    n_bets: int
    total_roi: float
    mean_clv: float
    settlement_by_sport_market: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class OofArtifactSummaryResponse(BaseModel):
    available: bool
    generated_at_utc: str | None = None
    source: str | None = None
    dataset_path: str | None = None
    rows: int = 0
    events: int = 0
    anchors: list[OofAnchorSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EntryEvArtifactLatestResponse(BaseModel):
    available: bool
    generated_at_utc: str | None = None
    input_path: str | None = None
    anchor: str | None = None
    sport: str | None = None
    model: str | None = None
    rows_input: int = 0
    rows_modelable: int = 0
    rows_predicted: int = 0
    events_modelable: int = 0
    feature_count: int = 0
    recommended_count: int = 0
    recommended_profit_units: float = 0.0
    recommended_roi: float = 0.0
    mean_model_ev_units: float = 0.0
    predictions_path: str | None = None
    warnings: list[str] = Field(default_factory=list)


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
    configured_sports: list[str]
    odds_api_monthly_budget: int
    odds_api_reserve_requests: int
    odds_api_requests_recorded_month: int
    odds_api_requests_used: int | None
    odds_api_requests_remaining: int | None
    odds_api_last_request_utc: datetime | None
    odds_api_requests_by_sport: dict[str, int]
    latest_odds_quote_utc: datetime | None
    odds_data_age_min: int | None
    odds_stale: bool
    last_run_status: str | None
    last_run_completed_utc: datetime | None
    failed_runs_24h: int
    odds_quotes_by_league: dict[str, int]


class RunStepOut(BaseModel):
    rc: int
    duration_sec: int


class IngestionRunOut(BaseModel):
    run_id: str
    started_at_utc: datetime
    completed_at_utc: datetime
    status: str
    steps: dict[str, RunStepOut]
