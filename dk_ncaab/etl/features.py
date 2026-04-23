"""
Feature engineering pipeline.

Builds a flat feature row per (event_id, market, side) with:
  - OPEN/T60/T30/CLOSE implied probabilities and lines
  - Movement deltas, velocity, acceleration
  - Volatility (std-dev, price-change count, max drawdown)
  - KenPom deviation features (spread_dev at each anchor)         §4
  - AP ranking features (rank_home, rank_away, diff, flag)        §5
  - Late steam indicator (movement inside final 30 min)           §6
  - Splits features (bets%, handle%, divergence)                  §7
  - Interaction features (deviation×public, movement×public, etc) §7
  - CLV metrics                                                   §8
  - Model expected value                                          §9
  - Outcome labels                                                §9
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Sequence

import numpy as np
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from dk_ncaab.config.sports import sport_for_league_key
from dk_ncaab.db.models import OddsQuote, Event, EventResult
from dk_ncaab.etl.snapshots import (
    get_snapshot_set,
    get_splits_snapshot,
    Snapshot,
    SplitsSnapshot,
    SnapshotSet,
)
from dk_ncaab.etl.normalize import american_to_implied, remove_vig


# ── Feature row ─────────────────────────────────────────────────

@dataclass
class FeatureRow:
    """Flat feature vector for one (event_id, market, side)."""
    event_id: int
    market: str
    side: str
    start_time_utc: datetime | None = None
    sport: str | None = None
    league_key: str | None = None

    # Implied probabilities at anchors
    implied_OPEN: float | None = None
    implied_T60: float | None = None
    implied_T30: float | None = None
    implied_CLOSE: float | None = None

    # Lines at anchors (spread/total only)
    line_OPEN: float | None = None
    line_T60: float | None = None
    line_T30: float | None = None
    line_CLOSE: float | None = None

    # American prices at anchors
    price_american_OPEN: int | None = None
    price_american_T60: int | None = None
    price_american_T30: int | None = None
    price_american_CLOSE: int | None = None

    # Vig-removed fair implied probabilities at anchors
    fair_implied_OPEN: float | None = None
    fair_implied_T60: float | None = None
    fair_implied_T30: float | None = None
    fair_implied_CLOSE: float | None = None

    # Fair-probability movement deltas
    d_fair_OPEN_T60: float | None = None
    d_fair_T60_T30: float | None = None
    d_fair_T30_CLOSE: float | None = None
    d_fair_OPEN_CLOSE: float | None = None

    # Fair-probability CLV
    clv_fair_OPEN: float | None = None
    clv_fair_T60: float | None = None
    clv_fair_T30: float | None = None

    # Movement deltas (implied probability)
    d_implied_OPEN_T60: float | None = None
    d_implied_T60_T30: float | None = None
    d_implied_T30_CLOSE: float | None = None
    d_implied_OPEN_CLOSE: float | None = None

    # Line movement deltas
    d_line_OPEN_T60: float | None = None
    d_line_T60_T30: float | None = None
    d_line_T30_CLOSE: float | None = None
    d_line_OPEN_CLOSE: float | None = None

    # Velocity (delta / hours between anchors)
    velocity_implied_OPEN_T60: float | None = None
    velocity_implied_T60_T30: float | None = None
    velocity_implied_T30_CLOSE: float | None = None

    # Acceleration
    accel_implied: float | None = None   # velocity_T60_T30 - velocity_OPEN_T60

    # Late steam indicator (§6: movement inside final 30 min)
    late_steam: float | None = None      # abs(d_implied_T30_CLOSE)
    late_steam_direction: float | None = None  # +1 or -1

    # Volatility (computed from full quote series)
    std_implied: float | None = None
    n_price_changes: int | None = None
    max_implied_drawdown: float | None = None

    # ── KenPom features (§4) ────────────────────────────────────
    home_adj_o: float | None = None
    home_adj_d: float | None = None
    home_adj_em: float | None = None
    home_tempo: float | None = None
    home_sos: float | None = None
    away_adj_o: float | None = None
    away_adj_d: float | None = None
    away_adj_em: float | None = None
    away_tempo: float | None = None
    away_sos: float | None = None
    adj_em_diff: float | None = None
    kenpom_expected_spread: float | None = None

    # KenPom deviation at each anchor
    spread_dev_OPEN: float | None = None
    spread_dev_T60: float | None = None
    spread_dev_T30: float | None = None
    spread_dev_CLOSE: float | None = None

    # ── AP ranking features (§5) ────────────────────────────────
    ap_rank_home: int | None = None
    ap_rank_away: int | None = None
    ap_rank_diff: int | None = None          # away_eff - home_eff (positive = home ranked higher)
    ranked_vs_unranked: int | None = None    # 1 if exactly one team ranked

    # ── Splits features (§7) ───────────────────────────────────
    bets_pct_OPEN: float | None = None
    handle_pct_OPEN: float | None = None
    bets_pct_T60: float | None = None
    handle_pct_T60: float | None = None
    bets_pct_T30: float | None = None
    handle_pct_T30: float | None = None
    handle_minus_bets_OPEN: float | None = None
    handle_minus_bets_T60: float | None = None
    handle_minus_bets_T30: float | None = None
    d_handle_minus_bets_OPEN_T60: float | None = None
    d_handle_minus_bets_T60_T30: float | None = None

    # Sharp-money proxy: line moved against public majority?
    sharp_money_proxy: float | None = None
    contrarian_intensity: float | None = None

    # ── Interaction features (§7) ──────────────────────────────
    deviation_x_public_extreme: float | None = None      # spread_dev × (bets_pct > 65 flag)
    movement_x_public_extreme: float | None = None       # d_implied_OPEN_T60 × public_extreme
    hmb_x_deviation: float | None = None                 # handle_minus_bets × spread_dev

    # ── CLV (§8) ───────────────────────────────────────────────
    clv_OPEN: float | None = None     # implied_CLOSE - implied_OPEN
    clv_T60: float | None = None
    clv_T30: float | None = None
    clv_line_OPEN: float | None = None
    clv_line_T60: float | None = None
    clv_line_T30: float | None = None

    # Context
    hours_before_tip_at_OPEN: float | None = None

    # MLB team/player trend features. All are computed from games before this
    # event start time.
    home_mlb_win_pct_l10: float | None = None
    away_mlb_win_pct_l10: float | None = None
    mlb_win_pct_delta_l10: float | None = None
    home_mlb_runs_for_l5: float | None = None
    away_mlb_runs_for_l5: float | None = None
    home_mlb_runs_allowed_l5: float | None = None
    away_mlb_runs_allowed_l5: float | None = None
    home_mlb_run_diff_l5: float | None = None
    away_mlb_run_diff_l5: float | None = None
    mlb_run_diff_delta_l5: float | None = None
    home_mlb_bullpen_outs_l3: float | None = None
    away_mlb_bullpen_outs_l3: float | None = None
    mlb_bullpen_outs_delta_l3: float | None = None
    home_mlb_rest_days: float | None = None
    away_mlb_rest_days: float | None = None
    home_mlb_starter_era_l3: float | None = None
    away_mlb_starter_era_l3: float | None = None
    home_mlb_starter_whip_l3: float | None = None
    away_mlb_starter_whip_l3: float | None = None
    home_mlb_starter_k_bb_l3: float | None = None
    away_mlb_starter_k_bb_l3: float | None = None
    home_mlb_starter_avg_ip_l3: float | None = None
    away_mlb_starter_avg_ip_l3: float | None = None

    # ── Model expected value (§9) ──────────────────────────────
    model_expected_value: float | None = None  # model_prob - break_even_prob (filled post-model)

    # ── Outcome labels (§9) ────────────────────────────────────
    home_win: int | None = None
    away_win: int | None = None
    spread_cover: int | None = None     # 1=covered, 0=not, using closing spread
    spread_cover_entry: int | None = None  # cover using entry spread (not just closing)
    spread_cover_OPEN: int | None = None
    spread_cover_T60: int | None = None
    spread_cover_T30: int | None = None
    spread_cover_CLOSE: int | None = None
    total_over: int | None = None       # 1=over, 0=under
    total_over_OPEN: int | None = None
    total_over_T60: int | None = None
    total_over_T30: int | None = None
    total_over_CLOSE: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Helpers ─────────────────────────────────────────────────────

def _safe_delta(a: float | None, b: float | None) -> float | None:
    """b - a, or None if either is missing."""
    if a is None or b is None:
        return None
    return b - a


def _hours_between(t1: datetime | None, t2: datetime | None) -> float | None:
    if t1 is None or t2 is None:
        return None
    diff = (t2 - t1).total_seconds() / 3600
    return diff if diff > 0 else None


def _velocity(delta: float | None, hours: float | None) -> float | None:
    if delta is None or hours is None or hours == 0:
        return None
    return delta / hours


# ── Volatility from raw quote series ───────────────────────────

def _compute_volatility(
    session: Session,
    event_id: int,
    market: str,
    side: str,
    start_time_utc: datetime,
) -> tuple[float | None, int | None, float | None]:
    """
    Returns (std_implied, n_price_changes, max_drawdown) from the
    full pre-tip quote series.
    """
    stmt = (
        select(OddsQuote.implied_probability)
        .where(
            OddsQuote.event_id == event_id,
            OddsQuote.market == market,
            OddsQuote.side == side,
            OddsQuote.collected_at_utc <= start_time_utc,
        )
        .order_by(OddsQuote.collected_at_utc.asc())
    )
    probs = [r[0] for r in session.execute(stmt) if r[0] is not None]

    if len(probs) < 2:
        return None, len(probs), None

    arr = np.array(probs)
    std = float(np.std(arr, ddof=1))

    # Count distinct price levels as a proxy for how many times the line moved
    n_changes = len(set(probs)) - 1

    # Max drawdown: largest peak-to-trough drop
    running_max = np.maximum.accumulate(arr)
    drawdowns = running_max - arr
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else None

    return std, n_changes, max_dd


# ── Main builder ────────────────────────────────────────────────

def build_features(
    session: Session,
    event_id: int,
    market: str,
    side: str,
) -> FeatureRow:
    """Build the complete feature row for one (event, market, side)."""
    event = session.get(Event, event_id)
    start = event.start_time_utc if event else None

    row = FeatureRow(event_id=event_id, market=market, side=side, start_time_utc=start)
    if event and event.league:
        row.league_key = event.league.key
        try:
            row.sport = sport_for_league_key(event.league.key)
        except ValueError:
            row.sport = event.league.key

    # ── Odds snapshots ──────────────────────────────────────────
    ss = get_snapshot_set(session, event_id, market, side)

    for anchor_name, snap in [
        ("OPEN", ss.OPEN), ("T60", ss.T60), ("T30", ss.T30), ("CLOSE", ss.CLOSE),
    ]:
        if snap:
            setattr(row, f"implied_{anchor_name}", snap.implied_probability)
            setattr(row, f"line_{anchor_name}", snap.line)
            setattr(row, f"price_american_{anchor_name}", snap.price_american)

    # ── Vig-removed fair probabilities ──────────────────────────
    # Query the opposite side at each anchor to normalize the overround.
    _fill_fair_implied(session, row, event_id, market, side, ss)

    # Movement deltas
    row.d_implied_OPEN_T60 = _safe_delta(row.implied_OPEN, row.implied_T60)
    row.d_implied_T60_T30 = _safe_delta(row.implied_T60, row.implied_T30)
    row.d_implied_T30_CLOSE = _safe_delta(row.implied_T30, row.implied_CLOSE)
    row.d_implied_OPEN_CLOSE = _safe_delta(row.implied_OPEN, row.implied_CLOSE)

    row.d_line_OPEN_T60 = _safe_delta(row.line_OPEN, row.line_T60)
    row.d_line_T60_T30 = _safe_delta(row.line_T60, row.line_T30)
    row.d_line_T30_CLOSE = _safe_delta(row.line_T30, row.line_CLOSE)
    row.d_line_OPEN_CLOSE = _safe_delta(row.line_OPEN, row.line_CLOSE)

    # Velocity (delta / hours between snapshot timestamps)
    if ss.OPEN and ss.T60:
        h = _hours_between(ss.OPEN.collected_at_utc, ss.T60.collected_at_utc)
        row.velocity_implied_OPEN_T60 = _velocity(row.d_implied_OPEN_T60, h)
    if ss.T60 and ss.T30:
        h = _hours_between(ss.T60.collected_at_utc, ss.T30.collected_at_utc)
        row.velocity_implied_T60_T30 = _velocity(row.d_implied_T60_T30, h)
    if ss.T30 and ss.CLOSE:
        h = _hours_between(ss.T30.collected_at_utc, ss.CLOSE.collected_at_utc)
        row.velocity_implied_T30_CLOSE = _velocity(row.d_implied_T30_CLOSE, h)

    # Acceleration
    row.accel_implied = _safe_delta(row.velocity_implied_OPEN_T60, row.velocity_implied_T60_T30)

    # Late steam indicator (§6: movement inside final 30 min)
    if row.d_implied_T30_CLOSE is not None:
        row.late_steam = abs(row.d_implied_T30_CLOSE)
        row.late_steam_direction = (
            1.0 if row.d_implied_T30_CLOSE > 0
            else (-1.0 if row.d_implied_T30_CLOSE < 0 else 0.0)
        )

    # Hours before tip at OPEN
    if ss.OPEN and start:
        row.hours_before_tip_at_OPEN = _hours_between(ss.OPEN.collected_at_utc, start)

    _fill_mlb_features(session, row, event, _entry_as_of_time(ss, start))

    # ── Volatility ──────────────────────────────────────────────
    if start:
        row.std_implied, row.n_price_changes, row.max_implied_drawdown = _compute_volatility(
            session, event_id, market, side, start
        )

    # ── KenPom features (§4) ───────────────────────────────────
    _fill_kenpom(session, row, event)

    # ── AP ranking features (§5) ───────────────────────────────
    _fill_ap_rankings(session, row, event)

    # ── Splits (§7) ────────────────────────────────────────────
    for anchor_name in ("OPEN", "T60", "T30"):
        sp = get_splits_snapshot(session, event_id, market, side, anchor_name)
        if sp:
            setattr(row, f"bets_pct_{anchor_name}", sp.bets_pct)
            setattr(row, f"handle_pct_{anchor_name}", sp.handle_pct)
            setattr(row, f"handle_minus_bets_{anchor_name}", sp.handle_pct - sp.bets_pct)

    row.d_handle_minus_bets_OPEN_T60 = _safe_delta(
        row.handle_minus_bets_OPEN, row.handle_minus_bets_T60
    )
    row.d_handle_minus_bets_T60_T30 = _safe_delta(
        row.handle_minus_bets_T60, row.handle_minus_bets_T30
    )

    # Sharp-money proxy: line moved against public majority
    if row.bets_pct_T60 is not None and row.d_implied_OPEN_T60 is not None:
        public_side = 1 if row.bets_pct_T60 > 50 else -1
        move_sign = 1 if row.d_implied_OPEN_T60 > 0 else (-1 if row.d_implied_OPEN_T60 < 0 else 0)
        row.sharp_money_proxy = 1.0 if (move_sign != 0 and move_sign != public_side) else 0.0

    # Contrarian intensity
    if row.handle_pct_T60 is not None and row.bets_pct_T60 is not None and row.d_implied_OPEN_T60 is not None:
        hmb = row.handle_pct_T60 - row.bets_pct_T60
        sign = 1 if row.d_implied_OPEN_T60 > 0 else (-1 if row.d_implied_OPEN_T60 < 0 else 0)
        row.contrarian_intensity = hmb * sign

    # ── Interaction features (§7) ──────────────────────────────
    _fill_interactions(row)

    # ── CLV (§8) ───────────────────────────────────────────────
    row.clv_OPEN = _safe_delta(row.implied_OPEN, row.implied_CLOSE)
    row.clv_T60 = _safe_delta(row.implied_T60, row.implied_CLOSE)
    row.clv_T30 = _safe_delta(row.implied_T30, row.implied_CLOSE)
    row.clv_line_OPEN = _safe_delta(row.line_OPEN, row.line_CLOSE)
    row.clv_line_T60 = _safe_delta(row.line_T60, row.line_CLOSE)
    row.clv_line_T30 = _safe_delta(row.line_T30, row.line_CLOSE)

    # Fair-probability CLV (vig-removed, more accurate for outcome correlation)
    row.clv_fair_OPEN = _safe_delta(row.fair_implied_OPEN, row.fair_implied_CLOSE)
    row.clv_fair_T60 = _safe_delta(row.fair_implied_T60, row.fair_implied_CLOSE)
    row.clv_fair_T30 = _safe_delta(row.fair_implied_T30, row.fair_implied_CLOSE)

    # Fair-probability movement deltas
    row.d_fair_OPEN_T60 = _safe_delta(row.fair_implied_OPEN, row.fair_implied_T60)
    row.d_fair_T60_T30 = _safe_delta(row.fair_implied_T60, row.fair_implied_T30)
    row.d_fair_T30_CLOSE = _safe_delta(row.fair_implied_T30, row.fair_implied_CLOSE)
    row.d_fair_OPEN_CLOSE = _safe_delta(row.fair_implied_OPEN, row.fair_implied_CLOSE)

    # ── Outcomes (§9) ──────────────────────────────────────────
    _fill_outcomes(session, row, event)

    return row


# ── Vig-removal helper ──────────────────────────────────────────

_OPPOSITE_SIDE = {
    "home": "away", "away": "home",
    "over": "under", "under": "over",
}


def _fill_fair_implied(
    session: Session,
    row: FeatureRow,
    event_id: int,
    market: str,
    side: str,
    ss: SnapshotSet,
) -> None:
    """
    Compute vig-removed fair implied probabilities at each anchor.

    For each anchor, queries the opposite side's snapshot and normalizes
    the two-sided market so that probabilities sum to 1.0.

    This gives a "true" market-implied probability without the bookmaker's
    overround, which is more meaningful for outcome prediction.
    """
    opp_side = _OPPOSITE_SIDE.get(side)
    if not opp_side:
        return

    # Get opposite side's snapshots
    opp_ss = get_snapshot_set(session, event_id, market, opp_side)

    for anchor_name, this_snap, opp_snap in [
        ("OPEN", ss.OPEN, opp_ss.OPEN),
        ("T60", ss.T60, opp_ss.T60),
        ("T30", ss.T30, opp_ss.T30),
        ("CLOSE", ss.CLOSE, opp_ss.CLOSE),
    ]:
        if this_snap and opp_snap:
            this_imp = this_snap.implied_probability
            opp_imp = opp_snap.implied_probability
            if this_imp is not None and opp_imp is not None and (this_imp + opp_imp) > 0:
                fair_this, _ = remove_vig(this_imp, opp_imp)
                setattr(row, f"fair_implied_{anchor_name}", round(fair_this, 6))


# ── KenPom helpers ──────────────────────────────────────────────

def _fill_kenpom(session: Session, row: FeatureRow, event: Event | None) -> None:
    """Populate KenPom efficiency ratings and spread deviations."""
    if not event:
        return

    from dk_ncaab.collectors.kenpom import compute_event_kenpom, spread_deviation

    kp = compute_event_kenpom(
        session, event.home_team_id, event.away_team_id, event.start_time_utc
    )
    if not kp:
        return

    # Store raw KenPom metrics
    for key in (
        "home_adj_o", "home_adj_d", "home_adj_em", "home_tempo", "home_sos",
        "away_adj_o", "away_adj_d", "away_adj_em", "away_tempo", "away_sos",
        "adj_em_diff", "kenpom_expected_spread",
    ):
        setattr(row, key, kp[key])

    # Compute spread deviation at each anchor (§4)
    kp_spread = kp["kenpom_expected_spread"]
    for anchor in ("OPEN", "T60", "T30", "CLOSE"):
        line_val = getattr(row, f"line_{anchor}", None)
        if line_val is not None:
            setattr(row, f"spread_dev_{anchor}", spread_deviation(line_val, kp_spread))


def _fill_ap_rankings(session: Session, row: FeatureRow, event: Event | None) -> None:
    """Populate AP ranking features (§5)."""
    if not event:
        return

    from dk_ncaab.collectors.ap_rankings import compute_event_ap_features

    ap = compute_event_ap_features(
        session, event.home_team_id, event.away_team_id, event.start_time_utc
    )
    row.ap_rank_home = ap["ap_rank_home"]
    row.ap_rank_away = ap["ap_rank_away"]
    row.ap_rank_diff = ap["ap_rank_diff"]
    row.ranked_vs_unranked = ap["ranked_vs_unranked"]


def _fill_interactions(row: FeatureRow) -> None:
    """
    Compute interaction features (§7).

    - deviation × public_extreme
    - movement × public_extreme
    - handle_minus_bets × deviation
    """
    # Public extreme flag: bets_pct > 65 on this side
    public_extreme = None
    if row.bets_pct_T60 is not None:
        public_extreme = 1.0 if row.bets_pct_T60 > 65 else 0.0

    # deviation × public_extreme
    if row.spread_dev_T60 is not None and public_extreme is not None:
        row.deviation_x_public_extreme = row.spread_dev_T60 * public_extreme

    # movement × public_extreme
    if row.d_implied_OPEN_T60 is not None and public_extreme is not None:
        row.movement_x_public_extreme = row.d_implied_OPEN_T60 * public_extreme

    # handle_minus_bets × deviation
    if row.handle_minus_bets_T60 is not None and row.spread_dev_T60 is not None:
        row.hmb_x_deviation = row.handle_minus_bets_T60 * row.spread_dev_T60


def _avg(values: Sequence[float | int | None]) -> float | None:
    nums = [float(v) for v in values if v is not None]
    return float(sum(nums) / len(nums)) if nums else None


def _days_between(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    if later.tzinfo is not None and earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=later.tzinfo)
    elif later.tzinfo is None and earlier.tzinfo is not None:
        later = later.replace(tzinfo=earlier.tzinfo)
    return (later - earlier).total_seconds() / 86400


def _entry_as_of_time(ss: SnapshotSet, fallback: datetime | None) -> datetime | None:
    for snap in (ss.OPEN, ss.T60, ss.T30):
        if snap is not None:
            return snap.collected_at_utc
    return fallback


def _fill_mlb_features(
    session: Session,
    row: FeatureRow,
    event: Event | None,
    as_of_utc: datetime | None,
) -> None:
    """Populate MLB rolling team and starting-pitcher features without leakage."""
    if not event or row.sport != "baseball_mlb" or as_of_utc is None:
        return

    from dk_ncaab.db.models import (
        MlbPlayerGameLog,
        MlbProbableStarter,
        MlbTeamGameLog,
    )

    start = event.start_time_utc

    def team_logs(team_id: int, limit: int) -> list[MlbTeamGameLog]:
        stmt = (
            select(MlbTeamGameLog)
            .where(
                MlbTeamGameLog.team_id == team_id,
                MlbTeamGameLog.event_id != event.id,
                MlbTeamGameLog.game_date_utc < as_of_utc,
            )
            .order_by(MlbTeamGameLog.game_date_utc.desc(), MlbTeamGameLog.id.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars())

    def set_team_side(prefix: str, team_id: int) -> None:
        logs5 = team_logs(team_id, 5)
        logs10 = team_logs(team_id, 10)
        runs_for = _avg([g.runs_for for g in logs5])
        runs_allowed = _avg([g.runs_against for g in logs5])
        setattr(row, f"{prefix}_mlb_runs_for_l5", runs_for)
        setattr(row, f"{prefix}_mlb_runs_allowed_l5", runs_allowed)
        if runs_for is not None and runs_allowed is not None:
            setattr(row, f"{prefix}_mlb_run_diff_l5", runs_for - runs_allowed)
        wins = [
            1.0 if g.runs_for is not None and g.runs_against is not None and g.runs_for > g.runs_against else 0.0
            for g in logs10
            if g.runs_for is not None and g.runs_against is not None
        ]
        setattr(row, f"{prefix}_mlb_win_pct_l10", _avg(wins))
        setattr(row, f"{prefix}_mlb_bullpen_outs_l3", _avg([g.bullpen_outs for g in logs5[:3]]))
        if logs5:
            setattr(row, f"{prefix}_mlb_rest_days", _days_between(start, logs5[0].game_date_utc))

    set_team_side("home", event.home_team_id)
    set_team_side("away", event.away_team_id)

    if row.home_mlb_win_pct_l10 is not None and row.away_mlb_win_pct_l10 is not None:
        row.mlb_win_pct_delta_l10 = row.home_mlb_win_pct_l10 - row.away_mlb_win_pct_l10
    if row.home_mlb_run_diff_l5 is not None and row.away_mlb_run_diff_l5 is not None:
        row.mlb_run_diff_delta_l5 = row.home_mlb_run_diff_l5 - row.away_mlb_run_diff_l5
    if row.home_mlb_bullpen_outs_l3 is not None and row.away_mlb_bullpen_outs_l3 is not None:
        row.mlb_bullpen_outs_delta_l3 = row.home_mlb_bullpen_outs_l3 - row.away_mlb_bullpen_outs_l3

    def set_starter_side(prefix: str, team_id: int) -> None:
        starter = session.execute(
            select(MlbProbableStarter).where(
                MlbProbableStarter.event_id == event.id,
                MlbProbableStarter.team_id == team_id,
            )
        ).scalar_one_or_none()
        if starter is None:
            return
        stmt = (
            select(MlbPlayerGameLog)
            .where(
                MlbPlayerGameLog.player_id == starter.player_id,
                MlbPlayerGameLog.pitching_started.is_(True),
                MlbPlayerGameLog.game_date_utc < as_of_utc,
            )
            .order_by(MlbPlayerGameLog.game_date_utc.desc(), MlbPlayerGameLog.id.desc())
            .limit(3)
        )
        logs = list(session.execute(stmt).scalars())
        outs = [g.innings_pitched_outs for g in logs if g.innings_pitched_outs]
        total_outs = sum(outs)
        total_ip = total_outs / 3 if total_outs else None
        er = sum(g.earned_runs or 0 for g in logs)
        hits = sum(g.pitching_hits or 0 for g in logs)
        walks = sum(g.pitching_base_on_balls or 0 for g in logs)
        strikeouts = sum(g.pitching_strike_outs or 0 for g in logs)
        if total_ip and total_ip > 0:
            setattr(row, f"{prefix}_mlb_starter_era_l3", (er * 9) / total_ip)
            setattr(row, f"{prefix}_mlb_starter_whip_l3", (hits + walks) / total_ip)
            setattr(row, f"{prefix}_mlb_starter_avg_ip_l3", total_ip / len(logs) if logs else None)
        if logs:
            setattr(row, f"{prefix}_mlb_starter_k_bb_l3", float(strikeouts - walks))

    set_starter_side("home", event.home_team_id)
    set_starter_side("away", event.away_team_id)


def _fill_outcomes(session: Session, row: FeatureRow, event: Event | None) -> None:
    """Populate outcome labels from event_results."""
    if not event or not event.result:
        return

    res = event.result
    h, a = res.home_score, res.away_score

    row.home_win = int(h > a)
    row.away_win = int(a > h)

    for anchor in ("OPEN", "T60", "T30", "CLOSE"):
        line = getattr(row, f"line_{anchor}", None)
        if line is None:
            continue

        if row.side in ("home", "away"):
            if row.side == "home":
                margin = h + line - a
            else:
                margin = a + line - h
            setattr(row, f"spread_cover_{anchor}", int(margin > 0) if margin != 0 else None)

        if row.side in ("over", "under"):
            total_score = h + a
            if total_score == line:
                outcome = None
            elif row.side == "over":
                outcome = int(total_score > line)
            else:
                outcome = int(total_score < line)
            setattr(row, f"total_over_{anchor}", outcome)

    # Backward-compatible aliases. Backtests use anchor-specific labels so
    # moved spread/total lines settle at the actual entry line.
    row.spread_cover = row.spread_cover_CLOSE
    row.spread_cover_entry = row.spread_cover_OPEN
    row.total_over = row.total_over_CLOSE
