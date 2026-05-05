"""
Pydantic response schemas for the read-only API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    book: str = "draftkings"
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
    best_entry_anchor: str | None = None
    best_entry_line: float | None = None
    best_entry_price_american: int | None = None
    best_entry_implied_probability: float | None = None
    best_entry_quote_utc: datetime | None = None
    price_move_implied_from_open: float | None = None
    price_move_american_from_open: int | None = None
    number_move_from_open: float | None = None


class BoardSplitSummary(BaseModel):
    market: str
    side: str
    bets_pct: float | None = None
    handle_pct: float | None = None
    collected_at_utc: datetime | None = None


class SlateIntelligenceSignal(BaseModel):
    label: str
    value: str
    detail: str | None = None


class SlateIntelligenceSummary(BaseModel):
    score: int = 0
    tier: str = "low_signal"
    headline: str = "Low signal"
    primary_action: str = "monitor"
    next_action_label: str = "Monitor"
    reasons: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    strongest_move_label: str | None = None
    strongest_number_move: float | None = None
    strongest_price_move_american: float | None = None
    split_pressure_label: str | None = None
    split_gap: float | None = None
    evidence_label: str = "Research only"
    signals: list[SlateIntelligenceSignal] = Field(default_factory=list)


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
    slate_intelligence: SlateIntelligenceSummary = Field(default_factory=SlateIntelligenceSummary)


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
    game_total: float | None = None
    open_implied_team_total: float | None = None
    close_implied_team_total: float | None = None
    open_implied_opponent_total: float | None = None
    close_implied_opponent_total: float | None = None
    team_runs_vs_close_implied: float | None = None
    opponent_runs_vs_close_implied: float | None = None
    game_total_vs_close_total: float | None = None
    # ATS / result vs line
    spread_result: str | None = None     # "W" / "L" / "P" (push)
    total_result: str | None = None      # "O" / "U" / "P"
    team_total_result: str | None = None  # "O" / "U" / "P" vs implied team total


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

    model_config = ConfigDict(extra="allow")


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


class TeamLineEvidenceRow(BaseModel):
    team_name: str
    line_source: str
    open_team_total: float | None = None
    current_team_total: float | None = None
    best_entry_team_total: float | None = None
    best_entry_anchor: str | None = None
    open_over_price_american: int | None = None
    current_over_price_american: int | None = None
    open_under_price_american: int | None = None
    current_under_price_american: int | None = None
    over_price_move_american_from_open: int | None = None
    under_price_move_american_from_open: int | None = None
    number_move_from_open: float | None = None
    latest_quote_utc: datetime | None = None
    history_points: list[EventOddsHistoryPoint] = Field(default_factory=list)
    games_sampled: int = 0
    posted_line_games_sampled: int = 0
    avg_runs_last_n: float | None = None
    avg_runs_allowed_last_n: float | None = None
    avg_close_implied_team_total_last_n: float | None = None
    avg_runs_vs_close_implied_last_n: float | None = None
    avg_allowed_vs_close_implied_last_n: float | None = None
    avg_game_total_vs_close_total_last_n: float | None = None
    team_total_record_last_n: str | None = None
    game_total_record_last_n: str | None = None
    record_vs_current_line_last_n: str | None = None
    avg_margin_vs_current_line_last_n: float | None = None
    recent_results_vs_current_line: list["RecentLineResultPoint"] = Field(default_factory=list)
    record_vs_market_line_last_n: str | None = None
    avg_margin_vs_market_line_last_n: float | None = None
    recent_results_vs_market_lines: list["RecentLineResultPoint"] = Field(default_factory=list)
    settled_market_history: list["SettledMarketHistoryPoint"] = Field(default_factory=list)
    note: str | None = None


class PlayerStatRow(BaseModel):
    player_name: str
    team_name: str | None = None
    role: str | None = None
    position: str | None = None
    games: int | None = None
    at_bats: int | None = None
    hits: int | None = None
    home_runs: int | None = None
    rbi: int | None = None
    base_on_balls: int | None = None
    strike_outs: int | None = None
    stolen_bases: int | None = None
    batting_avg: float | None = None
    slugging: float | None = None
    obp_proxy: float | None = None
    ops_proxy: float | None = None
    last_games: list[dict] = Field(default_factory=list)
    note: str | None = None


class RecentStatPoint(BaseModel):
    game_date_utc: datetime
    label: str
    value: float


class RecentLineResultPoint(BaseModel):
    game_date_utc: datetime
    label: str
    value: float
    line: float
    margin_vs_line: float
    result: str


class RecentPriceResultPoint(BaseModel):
    game_date_utc: datetime
    label: str
    price_american: int
    implied_probability: float | None = None
    result: str  # "W" / "L"


class EventOddsHistoryPoint(BaseModel):
    collected_at_utc: datetime
    label: str
    line: float | None = None
    over_price_american: int | None = None
    under_price_american: int | None = None


class SettledMarketHistoryPoint(BaseModel):
    event_id: int | None = None
    game_date_utc: datetime
    label: str
    opponent_name: str | None = None
    value: float
    line: float
    over_price_american: int | None = None
    under_price_american: int | None = None
    margin_vs_line: float
    result: str


class PlayerPropInsightRow(BaseModel):
    market_key: str
    market_label: str
    player_name: str
    team_name: str | None = None
    line_source: str
    open_line: float | None = None
    current_line: float | None = None
    open_over_price_american: int | None = None
    over_price_american: int | None = None
    open_under_price_american: int | None = None
    under_price_american: int | None = None
    best_entry_anchor: str | None = None
    best_entry_line: float | None = None
    best_entry_over_price_american: int | None = None
    best_entry_under_price_american: int | None = None
    number_move_from_open: float | None = None
    over_price_move_american_from_open: int | None = None
    under_price_move_american_from_open: int | None = None
    latest_quote_utc: datetime | None = None
    games_sampled: int = 0
    posted_line_games_sampled: int = 0
    avg_last_n: float | None = None
    hit_rate_over_last_n: float | None = None
    hit_rate_under_last_n: float | None = None
    last_values: list[float] = Field(default_factory=list)
    recent_results: list[RecentStatPoint] = Field(default_factory=list)
    history_points: list[EventOddsHistoryPoint] = Field(default_factory=list)
    record_vs_current_line_last_n: str | None = None
    avg_margin_vs_current_line_last_n: float | None = None
    recent_results_vs_current_line: list[RecentLineResultPoint] = Field(default_factory=list)
    record_vs_market_line_last_n: str | None = None
    avg_margin_vs_market_line_last_n: float | None = None
    recent_results_vs_market_lines: list[RecentLineResultPoint] = Field(default_factory=list)
    settled_market_history: list[SettledMarketHistoryPoint] = Field(default_factory=list)
    context_note: str | None = None
    note: str | None = None


class MarketContextRow(BaseModel):
    market: str
    side: str
    selection: str
    book: str = "draftkings"
    current_line: float | None = None
    current_price_american: int | None = None
    open_line: float | None = None
    open_price_american: int | None = None
    best_entry_anchor: str | None = None
    best_entry_line: float | None = None
    best_entry_price_american: int | None = None
    best_entry_quote_utc: datetime | None = None
    implied_probability: float | None = None
    price_move_implied_from_open: float | None = None
    price_move_american_from_open: int | None = None
    number_move_from_open: float | None = None
    latest_quote_utc: datetime | None = None
    is_live: bool = False
    is_stale: bool = True
    bets_pct: float | None = None
    handle_pct: float | None = None
    record_vs_current_line_last_n: str | None = None
    avg_margin_vs_current_line_last_n: float | None = None
    recent_results_vs_current_line: list[RecentLineResultPoint] = Field(default_factory=list)
    record_vs_market_line_last_n: str | None = None
    avg_margin_vs_market_line_last_n: float | None = None
    recent_results_vs_market_lines: list[RecentLineResultPoint] = Field(default_factory=list)
    recent_record_last_n: str | None = None
    recent_win_rate_last_n: float | None = None
    avg_market_price_american_last_n: int | None = None
    avg_market_implied_probability_last_n: float | None = None
    current_implied_delta_vs_avg_last_n: float | None = None
    recent_results_vs_market_prices: list[RecentPriceResultPoint] = Field(default_factory=list)
    signal_notes: list[str] = Field(default_factory=list)


class LineEvidenceStatusRow(BaseModel):
    focus_key: str
    market: str
    side: str
    participant_name: str | None = None
    current_line: float | None = None
    current_price_american: int | None = None
    line_lifecycle_status: str
    market_readiness_verdict: str | None = None
    settled_sample_size: int = 0
    posted_line_sample_size: int = 0
    oof_predicted_rows: int = 0
    oof_recommended_rows: int = 0
    evidence_tier: str = "research_only"
    promotion_status: str = "research_only"
    promotion_gaps: list[str] = Field(default_factory=list)
    min_oof_rows: int = 100
    min_settled_events: int = 30
    min_posted_line_samples: int = 10
    gaps: list[str] = Field(default_factory=list)


class LineThesisRow(BaseModel):
    focus_key: str
    market: str
    side: str
    participant_name: str | None = None
    headline: str
    action_status: str
    line_quality_score: int = 0
    evidence_quality_score: int = 0
    current_summary: str
    movement_summary: str
    history_summary: str
    evidence_summary: str
    risk_summary: str
    support_points: list[str] = Field(default_factory=list)
    caution_points: list[str] = Field(default_factory=list)
    next_step: str | None = None


class TeamTrendContext(BaseModel):
    team: TeamOut
    games: int = 0
    win_pct: float | None = None
    avg_runs_for_l5: float | None = None
    avg_runs_against_l5: float | None = None
    run_diff_l5: float | None = None
    avg_hits_l5: float | None = None
    avg_home_runs_l5: float | None = None
    avg_walks_l5: float | None = None
    avg_strikeouts_l5: float | None = None
    avg_steals_l5: float | None = None
    batting_avg_l5: float | None = None
    slugging_l5: float | None = None
    avg_bullpen_outs_l3: float | None = None
    rest_days: int | None = None
    last_game_utc: datetime | None = None
    note: str | None = None


class StarterContext(BaseModel):
    team: TeamOut
    player_id: int | None = None
    player_name: str | None = None
    primary_position: str | None = None
    prior_starts: int = 0
    days_rest: int | None = None
    era_l3: float | None = None
    whip_l3: float | None = None
    k_bb_l3: int | None = None
    avg_ip_l3: float | None = None
    avg_pitches_l3: float | None = None
    avg_home_runs_allowed_l3: float | None = None
    last_start_utc: datetime | None = None
    note: str | None = None


class EnvironmentContext(BaseModel):
    provider: str | None = None
    available: bool = False
    venue_name: str | None = None
    roof_type: str | None = None
    park_factor_runs: float | None = None
    park_factor_hr: float | None = None
    park_factor_source: str | None = None
    park_factor_season: int | None = None
    park_factor_rolling_years: int | None = None
    temperature_f: float | None = None
    wind_mph: float | None = None
    wind_direction: str | None = None
    wind_from_degrees: float | None = None
    wind_to_center_alignment: float | None = None
    wind_out_mph: float | None = None
    wind_in_mph: float | None = None
    crosswind_mph: float | None = None
    field_wind_label: str | None = None
    precipitation_chance: float | None = None
    conditions: str | None = None
    forecast_for_utc: datetime | None = None
    collected_at_utc: datetime | None = None
    note: str | None = None


class WhyThisLineFactor(BaseModel):
    factor: str
    market_focus: str
    lean: str | None = None
    score: float | None = None
    headline: str
    detail: str | None = None


class MatchupStatRow(BaseModel):
    category: str
    metric: str
    away_value: str | float | int | None = None
    home_value: str | float | int | None = None
    note: str | None = None


class BullpenUsageRow(BaseModel):
    team_name: str
    pitcher_name: str
    outings_last_3d: int
    outs_last_3d: int | None = None
    pitches_last_3d: int | None = None
    strikeouts_last_3d: int | None = None
    walks_last_3d: int | None = None
    earned_runs_last_3d: int | None = None
    home_runs_allowed_last_3d: int | None = None
    last_appearance_utc: datetime | None = None
    note: str | None = None


class BattingOrderStabilityRow(BaseModel):
    team_name: str
    player_name: str
    starts_last_5: int
    avg_batting_order: float | None = None
    slots_used: int = 0
    last_batting_order: int | None = None
    last_started_utc: datetime | None = None
    stability_label: str | None = None
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
    team_line_evidence: list[TeamLineEvidenceRow] = Field(default_factory=list)
    market_context: list[MarketContextRow] = Field(default_factory=list)
    line_evidence_status: list[LineEvidenceStatusRow] = Field(default_factory=list)
    line_thesis: list[LineThesisRow] = Field(default_factory=list)
    team_trends: dict[str, TeamTrendContext] = Field(default_factory=dict)
    starter_context: dict[str, StarterContext] = Field(default_factory=dict)
    environment_context: EnvironmentContext | None = None
    why_this_line: list[WhyThisLineFactor] = Field(default_factory=list)
    matchup_snapshot: list[MatchupStatRow] = Field(default_factory=list)
    bullpen_usage: list[BullpenUsageRow] = Field(default_factory=list)
    batting_order_stability: list[BattingOrderStabilityRow] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    player_stats: list[PlayerStatRow]
    player_stats_note: str
    player_prop_insights: list[PlayerPropInsightRow] = Field(default_factory=list)
    player_props_note: str = ""
    warnings: list[str]


class PropMarketRegistryRow(BaseModel):
    sport_key: str
    provider: str
    provider_market_key: str
    market_key: str
    label: str
    entity_type: str
    selection_type: str
    stat_key: str
    ui_enabled: bool
    collection_enabled: bool
    notes: str | None = None


class PropMarketRegistryResponse(BaseModel):
    sport: str
    count: int
    rows: list[PropMarketRegistryRow]


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
    settled_quoted_events: int
    settled_trainable_events: int
    ready_after_settlement_events: int


class MlbReadinessResponse(BaseModel):
    generated_at_utc: datetime
    summary: MlbReadinessSummary
    events: list[MlbReadinessEvent]
    warnings: list[str] = Field(default_factory=list)


class MlbMarketReadinessRow(BaseModel):
    market: str
    label: str
    market_type: str
    verdict: str
    current_quoted_rows: int = 0
    current_quoted_events: int = 0
    settled_quoted_rows: int = 0
    settled_quoted_events: int = 0
    oof_predicted_rows: int = 0
    oof_recommended_rows: int = 0
    participant_quote_rows: int = 0
    participant_linked_rows: int = 0
    participant_link_rate: float | None = None
    stat_context_rows: int = 0
    stat_context_label: str | None = None
    priority_score: int = 0
    next_action: str = "ready_for_review"
    next_action_label: str = "Ready for review"
    next_action_command: str | None = None
    next_action_reason: str | None = None
    gaps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class MlbMarketReadinessSummary(BaseModel):
    sport: str
    league_key: str
    window_start_utc: datetime
    window_end_utc: datetime
    markets_ready: int
    markets_thin: int
    markets_collect_more: int
    markets_missing_data: int
    total_current_quoted_rows: int
    total_oof_predicted_rows: int
    artifact_generated_at_utc: str | None = None
    artifact_anchor: str | None = None
    artifact_path: str | None = None


class MlbMarketReadinessResponse(BaseModel):
    generated_at_utc: datetime
    summary: MlbMarketReadinessSummary
    markets: list[MlbMarketReadinessRow]
    warnings: list[str] = Field(default_factory=list)


class MlbEvidenceGrowthMarketRow(BaseModel):
    market: str
    label: str
    verdict: str
    previous_verdict: str | None = None
    verdict_changed: bool = False
    current_quoted_rows: int = 0
    current_quoted_rows_delta: int = 0
    settled_quoted_rows: int = 0
    settled_quoted_rows_delta: int = 0
    oof_predicted_rows: int = 0
    oof_predicted_rows_delta: int = 0
    priority_score: int = 0
    next_action: str = "ready_for_review"
    next_action_label: str = "Ready for review"
    next_action_command: str | None = None
    next_action_reason: str | None = None
    gaps: list[str] = Field(default_factory=list)


class MlbEvidenceGrowthLatestResponse(BaseModel):
    available: bool = False
    generated_at_utc: datetime | None = None
    label: str | None = None
    previous_generated_at_utc: datetime | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    priority_markets: list[MlbEvidenceGrowthMarketRow] = Field(default_factory=list)
    markets: list[MlbEvidenceGrowthMarketRow] = Field(default_factory=list)
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
    promotion_status: str = "research_only"
    promotion_gaps: list[str] = Field(default_factory=list)
    min_oof_rows: int = 100
    min_settled_events: int = 30
    min_posted_line_samples: int = 10
    predictions_path: str | None = None
    rows_predicted_by_market: dict[str, int] = Field(default_factory=dict)
    recommended_by_market: dict[str, int] = Field(default_factory=dict)
    recommendations: list[dict[str, object]] = Field(default_factory=list)
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
    settled_events_with_pregame_odds: int
    strict_entry_ev_events_modelable: int
    strict_entry_ev_rows_predicted: int
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
