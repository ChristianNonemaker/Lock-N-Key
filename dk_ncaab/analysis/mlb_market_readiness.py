"""Market-level MLB readiness for sportsbook board evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from dk_ncaab.analysis.oof_entry_ev import read_latest_entry_ev
from dk_ncaab.db.models import (
    Event,
    EventOddsQuote,
    League,
    MlbPlayerIdCrosswalk,
    MlbStatcastDaily,
    MlbTeamGameLog,
    OddsQuote,
)

CORE_MARKETS = ("moneyline", "spread", "total")
EVENT_MARKETS = ("team_totals", "pitcher_strikeouts", "batter_hits", "batter_total_bases")
MLB_MARKETS = (*CORE_MARKETS, *EVENT_MARKETS)

Verdict = Literal["ready", "thin", "collect_more", "missing_data"]


@dataclass(frozen=True)
class MlbMarketReadinessRow:
    market: str
    label: str
    market_type: str
    verdict: Verdict
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
    gaps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MlbMarketReadinessSummary:
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


@dataclass(frozen=True)
class MlbMarketReadinessResult:
    generated_at_utc: datetime
    summary: MlbMarketReadinessSummary
    markets: list[MlbMarketReadinessRow]
    warnings: list[str] = field(default_factory=list)


_MARKET_LABELS = {
    "moneyline": "Moneyline",
    "spread": "Run Line",
    "total": "Game Total",
    "team_totals": "Team Totals",
    "pitcher_strikeouts": "Pitcher Strikeouts",
    "batter_hits": "Batter Hits",
    "batter_total_bases": "Batter Total Bases",
}


def _window(now: datetime, days_back: int, days_forward: int) -> tuple[datetime, datetime]:
    return now - timedelta(days=days_back), now + timedelta(days=days_forward)


def _league_id(session: Session, league_key: str) -> int | None:
    return session.execute(select(League.id).where(League.key == league_key)).scalar_one_or_none()


def _active_statuses() -> tuple[str, ...]:
    return ("upcoming", "live")


def _current_core_counts(
    session: Session,
    league_id: int,
    market: str,
    window_start: datetime,
    window_end: datetime,
) -> tuple[int, int]:
    rows = session.execute(
        select(OddsQuote.event_id, OddsQuote.side)
        .join(Event, Event.id == OddsQuote.event_id)
        .where(Event.league_id == league_id)
        .where(Event.status.in_(_active_statuses()))
        .where(Event.start_time_utc >= window_start)
        .where(Event.start_time_utc < window_end)
        .where(OddsQuote.book == "draftkings")
        .where(OddsQuote.market == market)
        .where(OddsQuote.collected_at_utc < Event.start_time_utc)
    ).all()
    row_keys = {(int(event_id), side) for event_id, side in rows}
    event_ids = {event_id for event_id, _side in row_keys}
    return len(row_keys), len(event_ids)


def _settled_core_counts(
    session: Session,
    league_id: int,
    market: str,
    window_start: datetime,
) -> tuple[int, int]:
    rows = session.execute(
        select(OddsQuote.event_id, OddsQuote.side)
        .join(Event, Event.id == OddsQuote.event_id)
        .where(Event.league_id == league_id)
        .where(Event.status == "final")
        .where(Event.start_time_utc >= window_start)
        .where(OddsQuote.book == "draftkings")
        .where(OddsQuote.market == market)
        .where(OddsQuote.collected_at_utc < Event.start_time_utc)
    ).all()
    row_keys = {(int(event_id), side) for event_id, side in rows}
    event_ids = {event_id for event_id, _side in row_keys}
    return len(row_keys), len(event_ids)


def _current_event_market_counts(
    session: Session,
    league_id: int,
    market: str,
    window_start: datetime,
    window_end: datetime,
) -> tuple[int, int]:
    rows = session.execute(
        select(
            EventOddsQuote.event_id,
            EventOddsQuote.participant_name,
            EventOddsQuote.side,
            EventOddsQuote.team_id,
            EventOddsQuote.player_id,
        )
        .join(Event, Event.id == EventOddsQuote.event_id)
        .where(Event.league_id == league_id)
        .where(Event.status.in_(_active_statuses()))
        .where(Event.start_time_utc >= window_start)
        .where(Event.start_time_utc < window_end)
        .where(EventOddsQuote.book == "draftkings")
        .where(EventOddsQuote.market_key == market)
        .where(EventOddsQuote.collected_at_utc < Event.start_time_utc)
    ).all()
    row_keys = {
        (int(event_id), participant_name, side, team_id, player_id)
        for event_id, participant_name, side, team_id, player_id in rows
    }
    event_ids = {event_id for event_id, *_rest in row_keys}
    return len(row_keys), len(event_ids)


def _settled_event_market_counts(
    session: Session,
    league_id: int,
    market: str,
    window_start: datetime,
) -> tuple[int, int]:
    rows = session.execute(
        select(
            EventOddsQuote.event_id,
            EventOddsQuote.participant_name,
            EventOddsQuote.side,
            EventOddsQuote.team_id,
            EventOddsQuote.player_id,
        )
        .join(Event, Event.id == EventOddsQuote.event_id)
        .where(Event.league_id == league_id)
        .where(Event.status == "final")
        .where(Event.start_time_utc >= window_start)
        .where(EventOddsQuote.book == "draftkings")
        .where(EventOddsQuote.market_key == market)
        .where(EventOddsQuote.collected_at_utc < Event.start_time_utc)
    ).all()
    row_keys = {
        (int(event_id), participant_name, side, team_id, player_id)
        for event_id, participant_name, side, team_id, player_id in rows
    }
    event_ids = {event_id for event_id, *_rest in row_keys}
    return len(row_keys), len(event_ids)


def _participant_counts(session: Session, league_id: int, market: str) -> tuple[int, int]:
    rows = session.execute(
        select(EventOddsQuote.entity_type, EventOddsQuote.team_id, EventOddsQuote.player_id)
        .join(Event, Event.id == EventOddsQuote.event_id)
        .where(Event.league_id == league_id)
        .where(EventOddsQuote.market_key == market)
    ).all()
    total = len(rows)
    linked = sum(
        1
        for entity_type, team_id, player_id in rows
        if (entity_type == "team" and team_id is not None)
        or (entity_type == "player" and player_id is not None)
    )
    return total, linked


def _stat_context_counts(session: Session, market: str) -> tuple[int, str]:
    if market in {"moneyline", "spread", "total", "team_totals"}:
        rows = int(session.execute(select(func.count(MlbTeamGameLog.id))).scalar() or 0)
        return rows, "team logs"
    if market == "pitcher_strikeouts":
        rows = int(
            session.execute(
                select(func.count(MlbStatcastDaily.id)).where(
                    MlbStatcastDaily.player_type == "pitcher"
                )
            ).scalar()
            or 0
        )
        return rows, "pitcher Statcast days"
    rows = int(
        session.execute(
            select(func.count(MlbStatcastDaily.id)).where(
                MlbStatcastDaily.player_type == "batter"
            )
        ).scalar()
        or 0
    )
    return rows, "batter Statcast days"


def _identity_warnings(session: Session) -> list[str]:
    warnings: list[str] = []
    crosswalk_rows = int(
        session.execute(select(func.count(MlbPlayerIdCrosswalk.id))).scalar() or 0
    )
    linked_crosswalk_rows = int(
        session.execute(
            select(func.count(MlbPlayerIdCrosswalk.id)).where(
                MlbPlayerIdCrosswalk.player_id.is_not(None)
            )
        ).scalar()
        or 0
    )
    if crosswalk_rows == 0:
        warnings.append("No MLB player ID crosswalk rows found.")
    elif linked_crosswalk_rows == 0:
        warnings.append("MLB player ID crosswalk exists but has no linked local players.")
    return warnings


def _latest_oof_market_counts() -> tuple[dict[str, int], dict[str, int], dict[str, object]]:
    artifact = read_latest_entry_ev() or {}
    if artifact.get("sport") != "baseball_mlb":
        return {}, {}, artifact
    predicted = {
        str(market): int(count or 0)
        for market, count in (artifact.get("rows_predicted_by_market") or {}).items()
    }
    recommended = {
        str(market): int(count or 0)
        for market, count in (artifact.get("recommended_by_market") or {}).items()
    }
    return predicted, recommended, artifact


def _verdict(
    *,
    current_rows: int,
    settled_rows: int,
    oof_rows: int,
    participant_quote_rows: int,
    participant_link_rate: float | None,
    stat_context_rows: int,
) -> tuple[Verdict, list[str], list[str]]:
    gaps: list[str] = []
    notes: list[str] = []
    if current_rows == 0:
        gaps.append("no_current_lines")
    if settled_rows == 0:
        gaps.append("no_settled_line_history")
    if oof_rows == 0:
        gaps.append("no_oof_predictions")
    elif oof_rows < 20:
        gaps.append("thin_oof_sample")
    if stat_context_rows == 0:
        gaps.append("missing_stat_context")
    if participant_quote_rows and participant_link_rate is not None and participant_link_rate < 0.9:
        gaps.append("participant_linkage_gap")

    if current_rows > 0:
        notes.append("Current DraftKings lines are available.")
    if settled_rows > 0:
        notes.append("Settled pregame DraftKings history exists.")
    if oof_rows > 0:
        notes.append("Strict OOF evidence exists for this market.")

    if oof_rows >= 20 and current_rows > 0 and stat_context_rows > 0 and "participant_linkage_gap" not in gaps:
        return "ready", gaps, notes
    if oof_rows > 0 or settled_rows >= 20:
        return "thin", gaps, notes
    if current_rows > 0 or settled_rows > 0:
        return "collect_more", gaps, notes
    return "missing_data", gaps, notes


def _next_action_for_market(
    *,
    market: str,
    market_type: str,
    current_rows: int,
    settled_rows: int,
    oof_rows: int,
    stat_context_rows: int,
    participant_link_rate: float | None,
    gaps: list[str],
) -> tuple[int, str, str, str | None, str]:
    if "participant_linkage_gap" in gaps:
        return (
            95,
            "fix_participant_identity",
            "Fix participant linkage",
            "python -m dk_ncaab mlb-data-inventory",
            "Some event-specific quotes are not linked to local team/player IDs.",
        )

    if stat_context_rows == 0 or "missing_stat_context" in gaps:
        if market == "pitcher_strikeouts":
            command = "python -m dk_ncaab backfill-mlb-statcast-daily --window-days 1"
            reason = "Pitcher prop context needs pitcher Statcast daily rows."
        elif market in {"batter_hits", "batter_total_bases"}:
            command = "python -m dk_ncaab backfill-mlb-statcast-daily --window-days 1"
            reason = "Batter prop context needs batter Statcast daily rows."
        else:
            command = "python -m dk_ncaab backfill-mlb-current-season --window-days 3"
            reason = "Team market context needs MLB Stats API game logs."
        return 90, "backfill_stats_context", "Backfill stats context", command, reason

    if current_rows == 0:
        if market_type == "event_specific":
            command = (
                "python -m dk_ncaab collect-event-odds --sport baseball_mlb "
                f"--markets {market} --max-events 3"
            )
            label = "Collect event-specific odds"
            reason = "No current DraftKings rows are stored for this event-specific market."
        else:
            command = "python -m dk_ncaab collect-odds --sport baseball_mlb"
            label = "Collect core odds"
            reason = "No current DraftKings rows are stored for this core market."
        return 85, "collect_current_odds", label, command, reason

    if settled_rows == 0:
        return (
            70,
            "wait_for_settlement",
            "Wait for settlement",
            "python -m dk_ncaab update-results --sport baseball_mlb",
            "Current lines exist, but no settled priced history exists yet for this market.",
        )

    if oof_rows == 0:
        return (
            60,
            "rerun_oof_entry_ev",
            "Rerun strict entry-EV",
            (
                "python -m dk_ncaab build-dataset && "
                "python -m dk_ncaab oof-entry-ev --sport baseball_mlb --anchor T60"
            ),
            "Settled priced rows exist, but the latest strict OOF artifact has no rows for this market.",
        )

    if oof_rows < 20:
        if market_type == "event_specific":
            return (
                55,
                "grow_settled_event_market_sample",
                "Grow settled prop sample",
                (
                    "python -m dk_ncaab collect-event-odds --sport baseball_mlb "
                    f"--markets {market} --max-events 3"
                ),
                "Strict OOF rows exist, but the sample is still thin for this market.",
            )
        return (
            45,
            "grow_settled_core_sample",
            "Grow settled core sample",
            "python -m dk_ncaab collect-odds --sport baseball_mlb",
            "Strict OOF rows exist, but more settled priced events would strengthen validation.",
        )

    if participant_link_rate is not None and participant_link_rate < 1.0:
        return (
            35,
            "review_participant_linkage",
            "Review participant linkage",
            "python -m dk_ncaab mlb-data-inventory",
            "The market is usable, but participant linkage is not perfect.",
        )

    return (
        10,
        "ready_for_review",
        "Ready for review",
        "python -m dk_ncaab oof-entry-ev --sport baseball_mlb --anchor T60",
        "Current lines, settled history, stats context, and strict OOF coverage are present.",
    )


def build_mlb_market_readiness(
    session: Session,
    *,
    sport: str = "baseball_mlb",
    league_key: str = "mlb",
    days_back: int = 30,
    days_forward: int = 7,
    now: datetime | None = None,
) -> MlbMarketReadinessResult:
    """Build local-only MLB market readiness rows for product display."""
    generated_at = now or datetime.now(timezone.utc)
    window_start, window_end = _window(generated_at, days_back, days_forward)
    league_id = _league_id(session, league_key)
    if league_id is None:
        summary = MlbMarketReadinessSummary(
            sport=sport,
            league_key=league_key,
            window_start_utc=window_start,
            window_end_utc=window_end,
            markets_ready=0,
            markets_thin=0,
            markets_collect_more=0,
            markets_missing_data=len(MLB_MARKETS),
            total_current_quoted_rows=0,
            total_oof_predicted_rows=0,
        )
        return MlbMarketReadinessResult(
            generated_at_utc=generated_at,
            summary=summary,
            markets=[],
            warnings=[f"League not found for key={league_key}."],
        )

    oof_counts, recommended_counts, artifact = _latest_oof_market_counts()
    rows: list[MlbMarketReadinessRow] = []
    for market in MLB_MARKETS:
        if market in CORE_MARKETS:
            current_rows, current_events = _current_core_counts(
                session,
                league_id,
                market,
                window_start,
                window_end,
            )
            settled_rows, settled_events = _settled_core_counts(
                session,
                league_id,
                market,
                window_start,
            )
            participant_rows = 0
            participant_linked = 0
            participant_link_rate = None
            market_type = "core"
        else:
            current_rows, current_events = _current_event_market_counts(
                session,
                league_id,
                market,
                window_start,
                window_end,
            )
            settled_rows, settled_events = _settled_event_market_counts(
                session,
                league_id,
                market,
                window_start,
            )
            participant_rows, participant_linked = _participant_counts(session, league_id, market)
            participant_link_rate = (
                float(participant_linked / participant_rows) if participant_rows else None
            )
            market_type = "event_specific"

        stat_context_rows, stat_context_label = _stat_context_counts(session, market)
        oof_rows = oof_counts.get(market, 0)
        verdict, gaps, notes = _verdict(
            current_rows=current_rows,
            settled_rows=settled_rows,
            oof_rows=oof_rows,
            participant_quote_rows=participant_rows,
            participant_link_rate=participant_link_rate,
            stat_context_rows=stat_context_rows,
        )
        (
            priority_score,
            next_action,
            next_action_label,
            next_action_command,
            next_action_reason,
        ) = _next_action_for_market(
            market=market,
            market_type=market_type,
            current_rows=current_rows,
            settled_rows=settled_rows,
            oof_rows=oof_rows,
            stat_context_rows=stat_context_rows,
            participant_link_rate=participant_link_rate,
            gaps=gaps,
        )
        rows.append(
            MlbMarketReadinessRow(
                market=market,
                label=_MARKET_LABELS[market],
                market_type=market_type,
                verdict=verdict,
                current_quoted_rows=current_rows,
                current_quoted_events=current_events,
                settled_quoted_rows=settled_rows,
                settled_quoted_events=settled_events,
                oof_predicted_rows=oof_rows,
                oof_recommended_rows=recommended_counts.get(market, 0),
                participant_quote_rows=participant_rows,
                participant_linked_rows=participant_linked,
                participant_link_rate=participant_link_rate,
                stat_context_rows=stat_context_rows,
                stat_context_label=stat_context_label,
                priority_score=priority_score,
                next_action=next_action,
                next_action_label=next_action_label,
                next_action_command=next_action_command,
                next_action_reason=next_action_reason,
                gaps=gaps,
                notes=notes,
            )
        )

    summary = MlbMarketReadinessSummary(
        sport=sport,
        league_key=league_key,
        window_start_utc=window_start,
        window_end_utc=window_end,
        markets_ready=sum(1 for row in rows if row.verdict == "ready"),
        markets_thin=sum(1 for row in rows if row.verdict == "thin"),
        markets_collect_more=sum(1 for row in rows if row.verdict == "collect_more"),
        markets_missing_data=sum(1 for row in rows if row.verdict == "missing_data"),
        total_current_quoted_rows=sum(row.current_quoted_rows for row in rows),
        total_oof_predicted_rows=sum(row.oof_predicted_rows for row in rows),
        artifact_generated_at_utc=artifact.get("generated_at_utc"),
        artifact_anchor=artifact.get("anchor"),
        artifact_path=artifact.get("predictions_path"),
    )
    warnings = _identity_warnings(session)
    if not oof_counts:
        warnings.append("No strict MLB OOF entry-EV artifact was found.")
    if all(row.current_quoted_rows == 0 for row in rows):
        warnings.append("No current MLB DraftKings market rows found in the selected window.")
    return MlbMarketReadinessResult(
        generated_at_utc=generated_at,
        summary=summary,
        markets=rows,
        warnings=warnings,
    )
