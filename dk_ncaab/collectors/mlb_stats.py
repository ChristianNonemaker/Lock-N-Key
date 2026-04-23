"""MLB Stats API ingestion for team/player game logs and starter context.

This collector uses MLB's public Stats API endpoints for schedule and
boxscore data. It does not call odds providers and does not spend Odds API
quota.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from dk_ncaab.config.sports import league_for_sport
from dk_ncaab.config.settings import get_settings
from dk_ncaab.db.models import (
    Event,
    EventProviderKey,
    EventResult,
    League,
    MlbPlayerGameLog,
    MlbProbableStarter,
    MlbStatsRawPayload,
    MlbTeamGameLog,
    Player,
    Team,
)
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.etl.normalize import get_or_create_team

log = logging.getLogger(__name__)

PROVIDER = "mlb_stats_api"
SPORT_KEY = "baseball_mlb"


@dataclass(frozen=True)
class MlbStatsResult:
    schedule_games: int
    events_created: int
    results_upserted: int
    boxscores_fetched: int
    team_logs_upserted: int
    player_logs_upserted: int
    probable_starters_upserted: int


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _outs_from_ip(value: Any) -> int | None:
    if value is None or value == "":
        return None
    text = str(value)
    if "." not in text:
        whole = _int(text)
        return whole * 3 if whole is not None else None
    innings, partial = text.split(".", 1)
    whole = _int(innings)
    outs = _int(partial[:1])
    if whole is None or outs is None:
        return None
    return (whole * 3) + min(outs, 2)


def _status_from_mlb(game: dict[str, Any]) -> str:
    state = (game.get("status") or {}).get("abstractGameState")
    detailed = ((game.get("status") or {}).get("detailedState") or "").lower()
    if state == "Final" or "final" in detailed:
        return "final"
    if state == "Live":
        return "live"
    if "postponed" in detailed:
        return "postponed"
    if "cancel" in detailed:
        return "cancelled"
    return "upcoming"


def _ensure_league(session: Session) -> League:
    league_key, league_name = league_for_sport(SPORT_KEY)
    league = session.execute(select(League).where(League.key == league_key)).scalar_one_or_none()
    if league is None:
        league = League(key=league_key, name=league_name)
        session.add(league)
        session.flush()
    return league


def _upsert(
    session: Session,
    model: type,
    filters: dict[str, Any],
    values: dict[str, Any],
) -> tuple[Any, bool]:
    row = session.execute(select(model).filter_by(**filters)).scalar_one_or_none()
    created = row is None
    if row is None:
        row = model(**filters)
        session.add(row)
    for key, value in values.items():
        setattr(row, key, value)
    session.flush()
    return row, created


def _team_from_game(session: Session, league: League, game: dict[str, Any], side: str) -> Team:
    team_name = game["teams"][side]["team"]["name"]
    return get_or_create_team(session, team_name, PROVIDER, league.id)


def _player_from_payload(
    session: Session,
    league: League,
    person: dict[str, Any],
    position: dict[str, Any] | None = None,
) -> Player:
    player_key = str(person["id"])
    values = {
        "league_id": league.id,
        "full_name": person.get("fullName") or person.get("full_name") or player_key,
        "primary_position": (position or {}).get("abbreviation"),
        "bats": None,
        "throws": None,
    }
    player, _ = _upsert(
        session,
        Player,
        {"provider": PROVIDER, "external_player_key": player_key},
        values,
    )
    return player


def _find_or_create_event(
    session: Session,
    league: League,
    game: dict[str, Any],
) -> tuple[Event, bool]:
    game_pk = str(game["gamePk"])
    provider_key = session.execute(
        select(EventProviderKey).where(
            EventProviderKey.provider == PROVIDER,
            EventProviderKey.provider_event_key == game_pk,
        )
    ).scalar_one_or_none()
    if provider_key is not None:
        return session.get(Event, provider_key.event_id), False  # type: ignore[return-value]

    start = _parse_dt(game["gameDate"])
    home = _team_from_game(session, league, game, "home")
    away = _team_from_game(session, league, game, "away")
    window_start = start - timedelta(hours=8)
    window_end = start + timedelta(hours=8)
    existing = session.execute(
        select(Event).where(
            Event.league_id == league.id,
            Event.home_team_id.in_([home.id, away.id]),
            Event.away_team_id.in_([home.id, away.id]),
            Event.start_time_utc >= window_start,
            Event.start_time_utc <= window_end,
        )
    ).scalars().all()
    existing = next(
        (event for event in existing if {event.home_team_id, event.away_team_id} == {home.id, away.id}),
        None,
    )

    created = False
    if existing is None:
        existing = Event(
            league_id=league.id,
            external_event_key=f"{PROVIDER}:{game_pk}",
            start_time_utc=start,
            home_team_id=home.id,
            away_team_id=away.id,
            status=_status_from_mlb(game),
            first_seen_at_utc=datetime.now(timezone.utc),
        )
        session.add(existing)
        session.flush()
        created = True
    else:
        existing.start_time_utc = start
        existing.status = _status_from_mlb(game)

    session.add(
        EventProviderKey(
            event_id=existing.id,
            sport_key=SPORT_KEY,
            provider=PROVIDER,
            provider_event_key=game_pk,
        )
    )
    session.flush()
    return existing, created


def _archive_raw(
    session: Session,
    endpoint: str,
    payload: dict[str, Any],
    provider_event_key: str | None = None,
    event_id: int | None = None,
) -> None:
    session.add(
        MlbStatsRawPayload(
            collected_at_utc=datetime.now(timezone.utc),
            endpoint=endpoint,
            provider_event_key=provider_event_key,
            event_id=event_id,
            payload_json=payload,
        )
    )


def _upsert_result(session: Session, event: Event, game: dict[str, Any]) -> bool:
    if _status_from_mlb(game) != "final":
        return False
    mlb_home_team = game.get("teams", {}).get("home", {}).get("team", {}).get("name")
    mlb_away_team = game.get("teams", {}).get("away", {}).get("team", {}).get("name")
    mlb_home_score = _int(game.get("teams", {}).get("home", {}).get("score"))
    mlb_away_score = _int(game.get("teams", {}).get("away", {}).get("score"))
    if mlb_home_score is None or mlb_away_score is None:
        return False
    event_home_name = event.home_team.name if event.home_team else None
    event_away_name = event.away_team.name if event.away_team else None
    if event_home_name == mlb_home_team and event_away_name == mlb_away_team:
        home_score, away_score = mlb_home_score, mlb_away_score
    elif event_home_name == mlb_away_team and event_away_name == mlb_home_team:
        home_score, away_score = mlb_away_score, mlb_home_score
    else:
        home_score, away_score = mlb_home_score, mlb_away_score
    _, created = _upsert(
        session,
        EventResult,
        {"event_id": event.id},
        {
            "home_score": home_score,
            "away_score": away_score,
            "status": "final",
            "completed_at_utc": datetime.now(timezone.utc),
        },
    )
    event.status = "final"
    return created


def _upsert_probable_starter(
    session: Session,
    league: League,
    event: Event,
    team: Team,
    game_side: dict[str, Any],
    is_home: bool,
    source: str,
) -> bool:
    probable = game_side.get("probablePitcher")
    if not probable or not probable.get("id"):
        return False
    player = _player_from_payload(session, league, probable)
    _, created = _upsert(
        session,
        MlbProbableStarter,
        {"event_id": event.id, "team_id": team.id},
        {
            "player_id": player.id,
            "is_home": is_home,
            "source": source,
            "collected_at_utc": datetime.now(timezone.utc),
        },
    )
    return created


def _player_ids(values: Any) -> list[int]:
    return [_int(v) for v in values or [] if _int(v) is not None]


def _upsert_team_boxscore(
    session: Session,
    event: Event,
    team: Team,
    opponent: Team,
    game_date: datetime,
    is_home: bool,
    team_payload: dict[str, Any],
) -> bool:
    batting = team_payload.get("teamStats", {}).get("batting", {})
    pitching = team_payload.get("teamStats", {}).get("pitching", {})
    total_outs = _outs_from_ip(pitching.get("inningsPitched"))
    starter_outs = None
    pitcher_ids = _player_ids(team_payload.get("pitchers"))
    if pitcher_ids:
        first_pitcher = team_payload.get("players", {}).get(f"ID{pitcher_ids[0]}", {})
        starter_outs = _outs_from_ip(first_pitcher.get("stats", {}).get("pitching", {}).get("inningsPitched"))
    bullpen_outs = (
        total_outs - starter_outs
        if total_outs is not None and starter_outs is not None
        else None
    )
    _, created = _upsert(
        session,
        MlbTeamGameLog,
        {"event_id": event.id, "team_id": team.id},
        {
            "game_date_utc": game_date,
            "is_home": is_home,
            "opponent_team_id": opponent.id,
            "runs_for": _int(batting.get("runs")),
            "runs_against": _int(pitching.get("runs")),
            "hits": _int(batting.get("hits")),
            "errors": _int(batting.get("errors")),
            "at_bats": _int(batting.get("atBats")),
            "doubles": _int(batting.get("doubles")),
            "triples": _int(batting.get("triples")),
            "home_runs": _int(batting.get("homeRuns")),
            "base_on_balls": _int(batting.get("baseOnBalls")),
            "strike_outs": _int(batting.get("strikeOuts")),
            "stolen_bases": _int(batting.get("stolenBases")),
            "bullpen_outs": bullpen_outs,
            "source": PROVIDER,
        },
    )
    return created


def _upsert_player_boxscores(
    session: Session,
    league: League,
    event: Event,
    team: Team,
    game_date: datetime,
    is_home: bool,
    team_payload: dict[str, Any],
) -> int:
    count = 0
    batter_ids = set(_player_ids(team_payload.get("batters")))
    pitcher_ids = _player_ids(team_payload.get("pitchers"))
    starting_pitcher_id = pitcher_ids[0] if pitcher_ids else None
    for raw_player in team_payload.get("players", {}).values():
        person = raw_player.get("person") or {}
        if not person.get("id"):
            continue
        position = raw_player.get("position") or {}
        player = _player_from_payload(session, league, person, position)
        person_id = _int(person.get("id"))
        batting = raw_player.get("stats", {}).get("batting", {})
        pitching = raw_player.get("stats", {}).get("pitching", {})
        batting_order = _int(raw_player.get("battingOrder"))
        _, created = _upsert(
            session,
            MlbPlayerGameLog,
            {"event_id": event.id, "player_id": player.id, "team_id": team.id},
            {
                "game_date_utc": game_date,
                "is_home": is_home,
                "batting_order": batting_order,
                "position_abbrev": position.get("abbreviation"),
                "batting_started": bool(batting_order and person_id in batter_ids),
                "pitching_started": bool(person_id == starting_pitcher_id),
                "at_bats": _int(batting.get("atBats")),
                "runs": _int(batting.get("runs")),
                "hits": _int(batting.get("hits")),
                "doubles": _int(batting.get("doubles")),
                "triples": _int(batting.get("triples")),
                "home_runs": _int(batting.get("homeRuns")),
                "rbi": _int(batting.get("rbi")),
                "base_on_balls": _int(batting.get("baseOnBalls")),
                "strike_outs": _int(batting.get("strikeOuts")),
                "stolen_bases": _int(batting.get("stolenBases")),
                "innings_pitched_outs": _outs_from_ip(pitching.get("inningsPitched")),
                "pitching_hits": _int(pitching.get("hits")),
                "pitching_runs": _int(pitching.get("runs")),
                "earned_runs": _int(pitching.get("earnedRuns")),
                "pitching_base_on_balls": _int(pitching.get("baseOnBalls")),
                "pitching_strike_outs": _int(pitching.get("strikeOuts")),
                "pitching_home_runs": _int(pitching.get("homeRuns")),
                "pitches_thrown": _int(pitching.get("pitchesThrown")),
                "source": PROVIDER,
            },
        )
        if created:
            count += 1
        if person_id == starting_pitcher_id:
            _upsert(
                session,
                MlbProbableStarter,
                {"event_id": event.id, "team_id": team.id},
                {
                    "player_id": player.id,
                    "is_home": is_home,
                    "source": "boxscore",
                    "collected_at_utc": datetime.now(timezone.utc),
                },
            )
    return count


def _fetch_json(
    client: httpx.Client,
    endpoint: str,
    params: dict[str, Any] | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    base_url = (base_url or get_settings().mlb_stats.base_url).rstrip("/")
    resp = client.get(f"{base_url}{endpoint}", params=params or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _date_range(start: date, end: date) -> tuple[str, str]:
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    return start.isoformat(), end.isoformat()


def collect_mlb_stats(
    start_date: date | None = None,
    end_date: date | None = None,
    *,
    days: int = 1,
    final_only: bool = True,
    max_boxscores: int | None = None,
    request_delay_sec: float | None = None,
    client: httpx.Client | None = None,
    session: Session | None = None,
) -> MlbStatsResult:
    """Collect MLB schedule/result/starter context and final boxscores."""
    today = datetime.now(timezone.utc).date()
    if start_date is None:
        start_date = today
    if end_date is None:
        end_date = start_date + timedelta(days=max(days, 1) - 1)
    start_str, end_str = _date_range(start_date, end_date)

    own_client = client is None
    own_session = session is None
    client = client or httpx.Client()
    session = session or SessionLocal()
    cfg = get_settings().mlb_stats
    max_boxscores = cfg.max_boxscores_per_run if max_boxscores is None else max_boxscores
    request_delay_sec = (
        cfg.request_delay_sec if request_delay_sec is None else request_delay_sec
    )
    base_url = cfg.base_url

    schedule_games = 0
    events_created = 0
    results_upserted = 0
    boxscores_fetched = 0
    team_logs_upserted = 0
    player_logs_upserted = 0
    probable_starters_upserted = 0
    boxscore_cap_logged = False

    try:
        league = _ensure_league(session)
        schedule_endpoint = "/schedule"
        schedule_params = {
            "sportId": 1,
            "startDate": start_str,
            "endDate": end_str,
            "hydrate": "probablePitcher",
        }
        schedule_payload = _fetch_json(client, schedule_endpoint, schedule_params, base_url)
        _archive_raw(session, schedule_endpoint, schedule_payload)

        for date_block in schedule_payload.get("dates", []):
            for game in date_block.get("games", []):
                schedule_games += 1
                event, created = _find_or_create_event(session, league, game)
                events_created += int(created)
                home = session.get(Team, event.home_team_id)
                away = session.get(Team, event.away_team_id)
                if home is None or away is None:
                    raise RuntimeError(f"Missing MLB teams for event_id={event.id}")
                probable_starters_upserted += int(
                    _upsert_probable_starter(
                        session, league, event, home, game["teams"]["home"], True, "schedule"
                    )
                )
                probable_starters_upserted += int(
                    _upsert_probable_starter(
                        session, league, event, away, game["teams"]["away"], False, "schedule"
                    )
                )
                results_upserted += int(_upsert_result(session, event, game))

                if final_only and _status_from_mlb(game) != "final":
                    continue
                if max_boxscores >= 0 and boxscores_fetched >= max_boxscores:
                    if not boxscore_cap_logged:
                        log.info(
                            "MLB Stats max_boxscores_per_run reached: %s; skipping remaining boxscores",
                            max_boxscores,
                        )
                        boxscore_cap_logged = True
                    continue

                game_pk = str(game["gamePk"])
                boxscore_endpoint = f"/game/{game_pk}/boxscore"
                if request_delay_sec and request_delay_sec > 0:
                    time.sleep(request_delay_sec)
                boxscore_payload = _fetch_json(client, boxscore_endpoint, base_url=base_url)
                _archive_raw(session, boxscore_endpoint, boxscore_payload, game_pk, event.id)
                boxscores_fetched += 1
                game_date = _parse_dt(game["gameDate"])
                home_payload = boxscore_payload["teams"]["home"]
                away_payload = boxscore_payload["teams"]["away"]
                team_logs_upserted += int(
                    _upsert_team_boxscore(session, event, home, away, game_date, True, home_payload)
                )
                team_logs_upserted += int(
                    _upsert_team_boxscore(session, event, away, home, game_date, False, away_payload)
                )
                player_logs_upserted += _upsert_player_boxscores(
                    session, league, event, home, game_date, True, home_payload
                )
                player_logs_upserted += _upsert_player_boxscores(
                    session, league, event, away, game_date, False, away_payload
                )

        session.commit()
    finally:
        if own_session:
            session.close()
        if own_client:
            client.close()

    result = MlbStatsResult(
        schedule_games=schedule_games,
        events_created=events_created,
        results_upserted=results_upserted,
        boxscores_fetched=boxscores_fetched,
        team_logs_upserted=team_logs_upserted,
        player_logs_upserted=player_logs_upserted,
        probable_starters_upserted=probable_starters_upserted,
    )
    log.info("MLB Stats API collection complete: %s", result)
    return result
