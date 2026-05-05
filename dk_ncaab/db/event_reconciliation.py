"""
One-time local event-identity reconciliation helpers.

These helpers are intentionally conservative: they only auto-merge MLB events
when the same home/away matchup is within a tight start-time window and there
is no conflicting provider key lineage for the same provider.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from dk_ncaab.db.models import (
    Event,
    EventProviderKey,
    EventResult,
    League,
    MlbEnvironmentSnapshot,
    MlbEventVenue,
    MlbPlayerGameLog,
    MlbProbableStarter,
    MlbStatsRawPayload,
    MlbTeamGameLog,
    OddsQuote,
    SplitsQuote,
)
from dk_ncaab.db.session import SessionLocal

_MERGEABLE_LEAGUE_KEY = "mlb"
_SAFE_START_WINDOW_MINUTES = 90

_STATUS_RANK = {
    "upcoming": 1,
    "live": 2,
    "final": 3,
    "postponed": 0,
    "cancelled": 0,
}

T = TypeVar("T")


class EventMergeConflict(RuntimeError):
    """Raised when a duplicate-event pair is not safe to merge automatically."""


@dataclass(frozen=True)
class EventIdentityMetrics:
    event_id: int
    external_event_key: str
    start_time_utc: datetime
    status: str
    provider_keys: int
    has_result: bool
    odds_quotes: int
    splits_quotes: int
    mlb_team_logs: int
    mlb_player_logs: int
    mlb_probable_starters: int
    mlb_environment_snapshots: int
    mlb_raw_payloads: int
    has_event_venue: bool


@dataclass(frozen=True)
class EventMergePlan:
    canonical_event_id: int
    duplicate_event_ids: list[int]
    league_key: str
    home_team: str
    away_team: str
    earliest_start_utc: datetime
    latest_start_utc: datetime
    mergeable: bool
    reasons: list[str] = field(default_factory=list)
    event_details: list[EventIdentityMetrics] = field(default_factory=list)


@dataclass
class EventReconciliationSummary:
    sport: str
    dry_run: bool
    examined_events: int
    candidate_groups: int
    mergeable_groups: int
    merged_groups: int = 0
    merged_events: int = 0
    deleted_events: int = 0
    rows_moved: dict[str, int] = field(default_factory=dict)
    rows_deduped: dict[str, int] = field(default_factory=dict)
    skipped_groups: list[dict[str, Any]] = field(default_factory=list)
    plans: list[EventMergePlan] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "dry_run": self.dry_run,
            "examined_events": self.examined_events,
            "candidate_groups": self.candidate_groups,
            "mergeable_groups": self.mergeable_groups,
            "merged_groups": self.merged_groups,
            "merged_events": self.merged_events,
            "deleted_events": self.deleted_events,
            "rows_moved": dict(sorted(self.rows_moved.items())),
            "rows_deduped": dict(sorted(self.rows_deduped.items())),
            "skipped_groups": self.skipped_groups,
            "plans": [asdict(plan) for plan in self.plans],
        }


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bump(counter: dict[str, int], key: str, amount: int = 1) -> None:
    counter[key] = counter.get(key, 0) + amount


def _count_by_event(
    session: Session,
    model: type,
    event_ids: list[int],
) -> dict[int, int]:
    rows = session.execute(
        select(model.event_id, func.count())
        .where(model.event_id.in_(event_ids))
        .group_by(model.event_id)
    ).all()
    return {int(event_id): int(count) for event_id, count in rows}


def _build_metrics(session: Session, events: list[Event]) -> dict[int, EventIdentityMetrics]:
    event_ids = [ev.id for ev in events]
    provider_counts = _count_by_event(session, EventProviderKey, event_ids)
    odds_counts = _count_by_event(session, OddsQuote, event_ids)
    splits_counts = _count_by_event(session, SplitsQuote, event_ids)
    team_log_counts = _count_by_event(session, MlbTeamGameLog, event_ids)
    player_log_counts = _count_by_event(session, MlbPlayerGameLog, event_ids)
    starter_counts = _count_by_event(session, MlbProbableStarter, event_ids)
    env_counts = _count_by_event(session, MlbEnvironmentSnapshot, event_ids)
    raw_counts = _count_by_event(session, MlbStatsRawPayload, event_ids)
    venue_counts = _count_by_event(session, MlbEventVenue, event_ids)
    result_counts = {
        int(event_id): int(count)
        for event_id, count in session.execute(
            select(EventResult.event_id, func.count())
            .where(EventResult.event_id.in_(event_ids))
            .group_by(EventResult.event_id)
        ).all()
    }

    return {
        ev.id: EventIdentityMetrics(
            event_id=ev.id,
            external_event_key=ev.external_event_key,
            start_time_utc=_ensure_utc(ev.start_time_utc) or datetime.min.replace(tzinfo=timezone.utc),
            status=ev.status,
            provider_keys=provider_counts.get(ev.id, 0),
            has_result=result_counts.get(ev.id, 0) > 0,
            odds_quotes=odds_counts.get(ev.id, 0),
            splits_quotes=splits_counts.get(ev.id, 0),
            mlb_team_logs=team_log_counts.get(ev.id, 0),
            mlb_player_logs=player_log_counts.get(ev.id, 0),
            mlb_probable_starters=starter_counts.get(ev.id, 0),
            mlb_environment_snapshots=env_counts.get(ev.id, 0),
            mlb_raw_payloads=raw_counts.get(ev.id, 0),
            has_event_venue=venue_counts.get(ev.id, 0) > 0,
        )
        for ev in events
    }


def _cluster_duplicate_events(
    events: list[Event],
    *,
    max_start_diff_minutes: int,
) -> list[list[Event]]:
    groups: list[list[Event]] = []
    current: list[Event] = []
    current_anchor_start: datetime | None = None
    window = timedelta(minutes=max_start_diff_minutes)

    for ev in events:
        start = _ensure_utc(ev.start_time_utc)
        if not current:
            current = [ev]
            current_anchor_start = start
            continue
        same_matchup = (
            ev.home_team_id == current[-1].home_team_id
            and ev.away_team_id == current[-1].away_team_id
        )
        within_window = (
            start is not None
            and current_anchor_start is not None
            and abs(start - current_anchor_start) <= window
        )
        if same_matchup and within_window:
            current.append(ev)
            continue
        if len(current) > 1:
            groups.append(current)
        current = [ev]
        current_anchor_start = start

    if len(current) > 1:
        groups.append(current)
    return groups


def _provider_key_conflicts(
    session: Session,
    event_ids: list[int],
) -> list[str]:
    rows = session.execute(
        select(EventProviderKey.provider, EventProviderKey.provider_event_key)
        .where(EventProviderKey.event_id.in_(event_ids))
        .order_by(EventProviderKey.provider.asc(), EventProviderKey.provider_event_key.asc())
    ).all()
    seen: dict[str, set[str]] = defaultdict(set)
    for provider, provider_event_key in rows:
        seen[str(provider)].add(str(provider_event_key))
    conflicts = [
        f"conflicting_{provider}_keys"
        for provider, keys in seen.items()
        if len(keys) > 1
    ]
    return sorted(conflicts)


def _canonical_sort_key(metrics: EventIdentityMetrics) -> tuple[int, int, int, int, int, int, int]:
    data_richness = (
        metrics.mlb_team_logs
        + metrics.mlb_player_logs
        + metrics.mlb_probable_starters
        + metrics.mlb_environment_snapshots
        + metrics.mlb_raw_payloads
        + int(metrics.has_event_venue)
    )
    market_richness = metrics.odds_quotes + metrics.splits_quotes
    return (
        metrics.provider_keys,
        int(metrics.has_result),
        data_richness,
        market_richness,
        _STATUS_RANK.get(metrics.status, 0),
        -int(metrics.start_time_utc.timestamp()),
        -metrics.event_id,
    )


def plan_mlb_event_reconciliation(
    session: Session,
    *,
    max_start_diff_minutes: int = _SAFE_START_WINDOW_MINUTES,
    limit: int | None = None,
) -> list[EventMergePlan]:
    events = list(
        session.execute(
            select(Event)
            .options(selectinload(Event.home_team), selectinload(Event.away_team), selectinload(Event.result))
            .join(League, League.id == Event.league_id)
            .where(League.key == _MERGEABLE_LEAGUE_KEY)
            .order_by(
                Event.home_team_id.asc(),
                Event.away_team_id.asc(),
                Event.start_time_utc.asc(),
                Event.id.asc(),
            )
        ).scalars()
    )
    groups = _cluster_duplicate_events(events, max_start_diff_minutes=max_start_diff_minutes)
    if limit is not None:
        groups = groups[: max(limit, 0)]

    plans: list[EventMergePlan] = []
    for group in groups:
        metrics_by_event = _build_metrics(session, group)
        conflicts = _provider_key_conflicts(session, [ev.id for ev in group])
        ordered = sorted(
            group,
            key=lambda ev: _canonical_sort_key(metrics_by_event[ev.id]),
            reverse=True,
        )
        canonical = ordered[0]
        earliest = min(_ensure_utc(ev.start_time_utc) for ev in group if _ensure_utc(ev.start_time_utc) is not None)
        latest = max(_ensure_utc(ev.start_time_utc) for ev in group if _ensure_utc(ev.start_time_utc) is not None)
        reasons = list(conflicts)
        if canonical.result is not None:
            reasons.append("canonical_has_result")
        if metrics_by_event[canonical.id].provider_keys > 0:
            reasons.append("canonical_has_provider_keys")
        plans.append(
            EventMergePlan(
                canonical_event_id=canonical.id,
                duplicate_event_ids=[ev.id for ev in ordered[1:]],
                league_key=_MERGEABLE_LEAGUE_KEY,
                home_team=canonical.home_team.name,
                away_team=canonical.away_team.name,
                earliest_start_utc=earliest,
                latest_start_utc=latest,
                mergeable=not conflicts,
                reasons=sorted(set(reasons)),
                event_details=[metrics_by_event[ev.id] for ev in ordered],
            )
        )

    return plans


def _iter_rows(session: Session, model: type[T], event_id: int) -> list[T]:
    return list(session.execute(select(model).where(model.event_id == event_id)).scalars())


def _signature_odds(row: OddsQuote) -> tuple[Any, ...]:
    return (
        row.book,
        row.market,
        row.side,
        row.line,
        row.price_american,
        _ensure_utc(row.collected_at_utc),
        row.source,
    )


def _signature_splits(row: SplitsQuote) -> tuple[Any, ...]:
    return (
        row.market,
        row.side,
        row.bets_pct,
        row.handle_pct,
        _ensure_utc(row.collected_at_utc),
        row.source,
    )


def _signature_raw_payload(row: MlbStatsRawPayload) -> tuple[Any, ...]:
    return (
        row.endpoint,
        row.provider_event_key,
        _ensure_utc(row.collected_at_utc),
    )


def _signature_environment(row: MlbEnvironmentSnapshot) -> tuple[Any, ...]:
    return (
        row.provider,
        _ensure_utc(row.collected_at_utc),
        _ensure_utc(row.forecast_for_utc),
        row.venue_id,
        row.temperature_f,
        row.wind_mph,
        row.wind_direction,
        row.conditions,
        row.precipitation_chance,
    )


def _move_unique_rows(
    session: Session,
    *,
    model: type[T],
    target_event_id: int,
    source_event_id: int,
    table_name: str,
    signature_fn: Callable[[T], tuple[Any, ...]],
    summary: EventReconciliationSummary,
) -> None:
    target_signatures = {
        signature_fn(row): row
        for row in _iter_rows(session, model, target_event_id)
    }
    for row in _iter_rows(session, model, source_event_id):
        signature = signature_fn(row)
        if signature in target_signatures:
            session.delete(row)
            _bump(summary.rows_deduped, table_name)
            continue
        setattr(row, "event_id", target_event_id)
        target_signatures[signature] = row
        _bump(summary.rows_moved, table_name)
    session.flush()


def _merge_provider_keys(
    session: Session,
    *,
    target_event_id: int,
    source_event_id: int,
    summary: EventReconciliationSummary,
) -> None:
    target_by_provider = {
        row.provider: row
        for row in _iter_rows(session, EventProviderKey, target_event_id)
    }
    for row in _iter_rows(session, EventProviderKey, source_event_id):
        existing = target_by_provider.get(row.provider)
        if existing is not None:
            if existing.provider_event_key != row.provider_event_key:
                raise EventMergeConflict(
                    f"Conflicting provider key for provider={row.provider}: "
                    f"{existing.provider_event_key} vs {row.provider_event_key}"
                )
            session.delete(row)
            _bump(summary.rows_deduped, "event_provider_keys")
            continue
        row.event_id = target_event_id
        target_by_provider[row.provider] = row
        _bump(summary.rows_moved, "event_provider_keys")
    session.flush()


def _merge_event_result(
    session: Session,
    *,
    target: Event,
    source: Event,
    summary: EventReconciliationSummary,
) -> None:
    target_result = session.get(EventResult, target.id)
    source_result = session.get(EventResult, source.id)
    if source_result is None:
        return
    if target_result is None:
        source_result.event_id = target.id
        _bump(summary.rows_moved, "event_results")
        session.flush()
        return

    same_scores = (
        target_result.home_score == source_result.home_score
        and target_result.away_score == source_result.away_score
        and target_result.status == source_result.status
    )
    if not same_scores:
        raise EventMergeConflict(
            f"Conflicting results for events {target.id} and {source.id}"
        )
    if target_result.completed_at_utc is None and source_result.completed_at_utc is not None:
        target_result.completed_at_utc = source_result.completed_at_utc
    session.delete(source_result)
    _bump(summary.rows_deduped, "event_results")
    session.flush()


def _merge_event_venue(
    session: Session,
    *,
    target_event_id: int,
    source_event_id: int,
    summary: EventReconciliationSummary,
) -> None:
    target_row = session.get(MlbEventVenue, target_event_id)
    source_row = session.get(MlbEventVenue, source_event_id)
    if source_row is None:
        return
    if target_row is None:
        source_row.event_id = target_event_id
        _bump(summary.rows_moved, "mlb_event_venues")
        session.flush()
        return
    if target_row.venue_id != source_row.venue_id:
        raise EventMergeConflict(
            f"Conflicting MLB venue mapping for events {target_event_id} and {source_event_id}"
        )
    session.delete(source_row)
    _bump(summary.rows_deduped, "mlb_event_venues")
    session.flush()


def _merge_team_logs(
    session: Session,
    *,
    target_event_id: int,
    source_event_id: int,
    summary: EventReconciliationSummary,
) -> None:
    target_by_team = {
        row.team_id: row for row in _iter_rows(session, MlbTeamGameLog, target_event_id)
    }
    for row in _iter_rows(session, MlbTeamGameLog, source_event_id):
        existing = target_by_team.get(row.team_id)
        if existing is not None:
            same_stats = (
                existing.runs_for == row.runs_for
                and existing.runs_against == row.runs_against
                and existing.hits == row.hits
                and existing.errors == row.errors
                and existing.bullpen_outs == row.bullpen_outs
            )
            if not same_stats:
                raise EventMergeConflict(
                    f"Conflicting MLB team log for team_id={row.team_id}"
                )
            session.delete(row)
            _bump(summary.rows_deduped, "mlb_team_game_logs")
            continue
        row.event_id = target_event_id
        target_by_team[row.team_id] = row
        _bump(summary.rows_moved, "mlb_team_game_logs")
    session.flush()


def _merge_player_logs(
    session: Session,
    *,
    target_event_id: int,
    source_event_id: int,
    summary: EventReconciliationSummary,
) -> None:
    target_by_key = {
        (row.player_id, row.team_id): row
        for row in _iter_rows(session, MlbPlayerGameLog, target_event_id)
    }
    for row in _iter_rows(session, MlbPlayerGameLog, source_event_id):
        key = (row.player_id, row.team_id)
        existing = target_by_key.get(key)
        if existing is not None:
            same_box = (
                existing.pitching_started == row.pitching_started
                and existing.batting_started == row.batting_started
                and existing.at_bats == row.at_bats
                and existing.hits == row.hits
                and existing.earned_runs == row.earned_runs
                and existing.innings_pitched_outs == row.innings_pitched_outs
            )
            if not same_box:
                raise EventMergeConflict(
                    f"Conflicting MLB player log for player_id={row.player_id} team_id={row.team_id}"
                )
            session.delete(row)
            _bump(summary.rows_deduped, "mlb_player_game_logs")
            continue
        row.event_id = target_event_id
        target_by_key[key] = row
        _bump(summary.rows_moved, "mlb_player_game_logs")
    session.flush()


def _merge_probable_starters(
    session: Session,
    *,
    target_event_id: int,
    source_event_id: int,
    summary: EventReconciliationSummary,
) -> None:
    target_by_team = {
        row.team_id: row for row in _iter_rows(session, MlbProbableStarter, target_event_id)
    }
    for row in _iter_rows(session, MlbProbableStarter, source_event_id):
        existing = target_by_team.get(row.team_id)
        if existing is not None:
            if existing.player_id != row.player_id:
                raise EventMergeConflict(
                    f"Conflicting probable starter for team_id={row.team_id}"
                )
            if existing.collected_at_utc < row.collected_at_utc:
                existing.collected_at_utc = row.collected_at_utc
                existing.source = row.source
            session.delete(row)
            _bump(summary.rows_deduped, "mlb_probable_starters")
            continue
        row.event_id = target_event_id
        target_by_team[row.team_id] = row
        _bump(summary.rows_moved, "mlb_probable_starters")
    session.flush()


def _merge_one_source_event(
    session: Session,
    *,
    target: Event,
    source: Event,
    summary: EventReconciliationSummary,
) -> None:
    _merge_provider_keys(session, target_event_id=target.id, source_event_id=source.id, summary=summary)
    _merge_event_result(session, target=target, source=source, summary=summary)
    _merge_event_venue(session, target_event_id=target.id, source_event_id=source.id, summary=summary)
    _move_unique_rows(
        session,
        model=OddsQuote,
        target_event_id=target.id,
        source_event_id=source.id,
        table_name="odds_quotes",
        signature_fn=_signature_odds,
        summary=summary,
    )
    _move_unique_rows(
        session,
        model=SplitsQuote,
        target_event_id=target.id,
        source_event_id=source.id,
        table_name="splits_quotes",
        signature_fn=_signature_splits,
        summary=summary,
    )
    _move_unique_rows(
        session,
        model=MlbStatsRawPayload,
        target_event_id=target.id,
        source_event_id=source.id,
        table_name="mlb_stats_raw_payloads",
        signature_fn=_signature_raw_payload,
        summary=summary,
    )
    _move_unique_rows(
        session,
        model=MlbEnvironmentSnapshot,
        target_event_id=target.id,
        source_event_id=source.id,
        table_name="mlb_environment_snapshots",
        signature_fn=_signature_environment,
        summary=summary,
    )
    _merge_team_logs(session, target_event_id=target.id, source_event_id=source.id, summary=summary)
    _merge_player_logs(session, target_event_id=target.id, source_event_id=source.id, summary=summary)
    _merge_probable_starters(session, target_event_id=target.id, source_event_id=source.id, summary=summary)

    if _STATUS_RANK.get(source.status, 0) > _STATUS_RANK.get(target.status, 0):
        target.status = source.status
    if target.first_seen_at_utc and source.first_seen_at_utc:
        target.first_seen_at_utc = min(target.first_seen_at_utc, source.first_seen_at_utc)

    session.delete(source)
    session.flush()
    summary.merged_events += 1
    summary.deleted_events += 1


def apply_mlb_event_reconciliation(
    session: Session,
    plans: Iterable[EventMergePlan],
) -> EventReconciliationSummary:
    plan_list = list(plans)
    summary = EventReconciliationSummary(
        sport="baseball_mlb",
        dry_run=False,
        examined_events=0,
        candidate_groups=len(plan_list),
        mergeable_groups=sum(1 for plan in plan_list if plan.mergeable),
        plans=plan_list,
    )
    for plan in plan_list:
        summary.examined_events += 1 + len(plan.duplicate_event_ids)
        if not plan.mergeable:
            summary.skipped_groups.append(
                {
                    "canonical_event_id": plan.canonical_event_id,
                    "duplicate_event_ids": plan.duplicate_event_ids,
                    "reasons": plan.reasons,
                }
            )
            continue

        target = session.get(Event, plan.canonical_event_id)
        if target is None:
            summary.skipped_groups.append(
                {
                    "canonical_event_id": plan.canonical_event_id,
                    "duplicate_event_ids": plan.duplicate_event_ids,
                    "reasons": ["canonical_event_missing"],
                }
            )
            continue

        try:
            with session.begin_nested():
                for duplicate_event_id in plan.duplicate_event_ids:
                    source = session.get(Event, duplicate_event_id)
                    if source is None:
                        continue
                    _merge_one_source_event(
                        session,
                        target=target,
                        source=source,
                        summary=summary,
                    )
            summary.merged_groups += 1
        except EventMergeConflict as exc:
            summary.skipped_groups.append(
                {
                    "canonical_event_id": plan.canonical_event_id,
                    "duplicate_event_ids": plan.duplicate_event_ids,
                    "reasons": [str(exc)],
                }
            )

    return summary


def reconcile_mlb_event_identity(
    *,
    apply: bool = False,
    max_start_diff_minutes: int = _SAFE_START_WINDOW_MINUTES,
    limit: int | None = None,
) -> dict[str, Any]:
    with SessionLocal() as session:
        plans = plan_mlb_event_reconciliation(
            session,
            max_start_diff_minutes=max_start_diff_minutes,
            limit=limit,
        )
        if not apply:
            summary = EventReconciliationSummary(
                sport="baseball_mlb",
                dry_run=True,
                examined_events=sum(1 + len(plan.duplicate_event_ids) for plan in plans),
                candidate_groups=len(plans),
                mergeable_groups=sum(1 for plan in plans if plan.mergeable),
                plans=plans,
                skipped_groups=[
                    {
                        "canonical_event_id": plan.canonical_event_id,
                        "duplicate_event_ids": plan.duplicate_event_ids,
                        "reasons": plan.reasons,
                    }
                    for plan in plans
                    if not plan.mergeable
                ],
            )
            return summary.to_dict()

        summary = apply_mlb_event_reconciliation(session, plans)
        session.commit()
        return summary.to_dict()
