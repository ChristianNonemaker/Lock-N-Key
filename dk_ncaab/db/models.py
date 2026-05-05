"""
SQLAlchemy ORM models for the DK NCAAB pipeline.

Design principles:
  - Append-only quote tables (odds_quotes, splits_quotes).
  - Dedup constraints prevent duplicate inserts on collector restart.
  - All timestamps stored as UTC.
  - events.status tracks lifecycle (upcoming → live → final / cancelled).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.types import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared base for all models."""
    pass


# ── Reference tables ────────────────────────────────────────────

class League(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # "ncaab"
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    teams: Mapped[list[Team]] = relationship(back_populates="league")
    events: Mapped[list[Event]] = relationship(back_populates="league")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    league: Mapped[League] = relationship(back_populates="teams")
    aliases: Mapped[list[TeamAlias]] = relationship(back_populates="team")


class TeamAlias(Base):
    __tablename__ = "team_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(256), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # "odds_api" | "dknetwork"

    team: Mapped[Team] = relationship(back_populates="aliases")

    __table_args__ = (
        Index("ix_team_aliases_source_alias", "source", "alias"),
    )


class EventProviderKey(Base):
    """Provider-specific event IDs without overwriting the primary event key."""

    __tablename__ = "event_provider_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    sport_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_event_key: Mapped[str] = mapped_column(String(256), nullable=False)
    first_seen_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    event: Mapped[Event] = relationship()

    __table_args__ = (
        UniqueConstraint("provider", "provider_event_key", name="uq_event_provider_key"),
        UniqueConstraint("event_id", "provider", name="uq_event_provider_by_event"),
        Index("ix_event_provider_keys_event", "event_id"),
    )


class Player(Base):
    """Provider-backed player identity for sport-specific logs."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_player_key: Mapped[str] = mapped_column(String(128), nullable=False)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    primary_position: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bats: Mapped[str | None] = mapped_column(String(16), nullable=True)
    throws: Mapped[str | None] = mapped_column(String(16), nullable=True)

    league: Mapped[League] = relationship()

    __table_args__ = (
        UniqueConstraint("provider", "external_player_key", name="uq_player_provider_key"),
        Index("ix_players_league_name", "league_id", "full_name"),
    )


class MlbPlayerIdCrosswalk(Base):
    """Reviewed MLB player ID bridge across public baseball data sources."""

    __tablename__ = "mlb_player_id_crosswalks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    key_mlbam: Mapped[str | None] = mapped_column(String(128), nullable=True)
    key_retro: Mapped[str | None] = mapped_column(String(128), nullable=True)
    key_bbref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    key_fangraphs: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name_first: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name_last: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name_full: Mapped[str | None] = mapped_column(String(256), nullable=True)
    mlb_played_first: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mlb_played_last: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_row_key: Mapped[str] = mapped_column(String(512), nullable=False)
    imported_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    player: Mapped[Player | None] = relationship()

    __table_args__ = (
        UniqueConstraint("source", "source_row_key", name="uq_mlb_player_xwalk_source_row"),
        Index("ix_mlb_player_xwalk_mlbam", "key_mlbam"),
        Index("ix_mlb_player_xwalk_retro", "key_retro"),
        Index("ix_mlb_player_xwalk_fangraphs", "key_fangraphs"),
        Index("ix_mlb_player_xwalk_player", "player_id"),
    )


# ── KenPom team ratings ──────────────────────────────────────────

class KenPomRating(Base):
    """
    Historical KenPom efficiency ratings per team per date.
    One row per (team, rating_date).  Ingest nightly or on-demand.
    """
    __tablename__ = "kenpom_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    rating_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    adj_o: Mapped[float] = mapped_column(Float, nullable=False)    # Adjusted Offense
    adj_d: Mapped[float] = mapped_column(Float, nullable=False)    # Adjusted Defense
    adj_em: Mapped[float] = mapped_column(Float, nullable=False)   # Adjusted Efficiency Margin
    tempo: Mapped[float] = mapped_column(Float, nullable=False)    # Adjusted Tempo
    sos: Mapped[float | None] = mapped_column(Float, nullable=True)  # Strength of Schedule

    team: Mapped[Team] = relationship()

    __table_args__ = (
        UniqueConstraint("team_id", "rating_date", name="uq_kenpom_team_date"),
        Index("ix_kenpom_team_date", "team_id", "rating_date"),
    )


# ── AP rankings ─────────────────────────────────────────────────

class APRanking(Base):
    """
    AP Top-25 poll rankings per team per week.
    Unranked teams are NOT stored; absence = unranked.
    """
    __tablename__ = "ap_rankings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    poll_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # Monday of poll week
    rank: Mapped[int] = mapped_column(Integer, nullable=False)             # 1-25
    votes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    team: Mapped[Team] = relationship()

    __table_args__ = (
        UniqueConstraint("team_id", "poll_date", name="uq_ap_team_poll"),
        Index("ix_ap_poll_date", "poll_date"),
    )


# ── Events ──────────────────────────────────────────────────────

class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), nullable=False)
    external_event_key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    start_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="upcoming"
    )  # upcoming | live | final | cancelled | postponed
    first_seen_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    league: Mapped[League] = relationship(back_populates="events")
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])
    result: Mapped[EventResult | None] = relationship(back_populates="event", uselist=False)

    __table_args__ = (
        Index("ix_events_status_start", "status", "start_time_utc"),
    )


# ── Odds (append-only) ─────────────────────────────────────────

class OddsQuote(Base):
    __tablename__ = "odds_quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    book: Mapped[str] = mapped_column(String(64), nullable=False)  # "draftkings"
    market: Mapped[str] = mapped_column(String(20), nullable=False)  # moneyline|spread|total
    side: Mapped[str] = mapped_column(String(10), nullable=False)   # home|away|over|under
    line: Mapped[float | None] = mapped_column(Float, nullable=True)  # NULL for ML
    price_american: Mapped[int] = mapped_column(Integer, nullable=False)
    implied_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    collected_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        # Dedup: same event/book/market/side/price/line/timestamp → skip
        UniqueConstraint(
            "event_id", "book", "market", "side",
            "price_american", "line", "collected_at_utc",
            name="uq_odds_dedup",
        ),
        # Primary query path: snapshots per event/market/side ordered by time
        Index(
            "ix_odds_event_market_side_time",
            "event_id", "market", "side", "collected_at_utc",
        ),
    )


class EventOddsQuote(Base):
    """Append-only event-specific markets such as team totals and player props."""

    __tablename__ = "event_odds_quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    book: Mapped[str] = mapped_column(String(64), nullable=False)
    market_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_market_key: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)  # team | player
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    participant_name: Mapped[str] = mapped_column(String(256), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)  # over | under | yes | no
    line: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_american: Mapped[int] = mapped_column(Integer, nullable=False)
    implied_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_updated_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    collected_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)

    event: Mapped[Event] = relationship()
    team: Mapped[Team | None] = relationship(foreign_keys=[team_id])
    player: Mapped[Player | None] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "book",
            "provider_market_key",
            "participant_name",
            "side",
            "price_american",
            "line",
            "collected_at_utc",
            name="uq_event_odds_quotes_dedup",
        ),
        Index(
            "ix_event_odds_quotes_event_market_time",
            "event_id",
            "market_key",
            "collected_at_utc",
        ),
        Index(
            "ix_event_odds_quotes_event_player_market",
            "event_id",
            "player_id",
            "market_key",
        ),
        Index(
            "ix_event_odds_quotes_event_team_market",
            "event_id",
            "team_id",
            "market_key",
        ),
    )


class OddsRawPayload(Base):
    __tablename__ = "odds_raw_payloads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collected_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Splits (append-only) ───────────────────────────────────────

class OddsApiUsage(Base):
    __tablename__ = "odds_api_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requested_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sport_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_sport_key: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(256), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requests_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requests_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_odds_api_usage_sport_time", "sport_key", "requested_at_utc"),
        Index("ix_odds_api_usage_requested_at", "requested_at_utc"),
    )


class SplitsQuote(Base):
    __tablename__ = "splits_quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    bets_pct: Mapped[float] = mapped_column(Float, nullable=False)     # 0-100
    handle_pct: Mapped[float] = mapped_column(Float, nullable=False)   # 0-100
    collected_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index(
            "ix_splits_event_market_side_time",
            "event_id", "market", "side", "collected_at_utc",
        ),
    )


class SplitsRawPayload(Base):
    __tablename__ = "splits_raw_payloads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collected_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class UnmatchedSplit(Base):
    __tablename__ = "unmatched_splits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collected_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_team_a: Mapped[str] = mapped_column(String(256), nullable=False)
    raw_team_b: Mapped[str] = mapped_column(String(256), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    bets_pct: Mapped[float] = mapped_column(Float, nullable=False)
    handle_pct: Mapped[float] = mapped_column(Float, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Results ─────────────────────────────────────────────────────

class EventResult(Base):
    __tablename__ = "event_results"

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id"), primary_key=True
    )
    home_score: Mapped[int] = mapped_column(Integer, nullable=False)
    away_score: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # final | overtime | ...
    completed_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    event: Mapped[Event] = relationship(back_populates="result")


class MlbStatsRawPayload(Base):
    """Raw MLB Stats API payload archive for lineage and replay."""

    __tablename__ = "mlb_stats_raw_payloads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collected_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_event_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_mlb_raw_event", "event_id"),
        Index("ix_mlb_raw_provider_event", "provider_event_key"),
    )


class MlbVenue(Base):
    """MLB venue metadata for weather and park context."""

    __tablename__ = "mlb_venues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="mlb_stats_api")
    provider_venue_key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    roof_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    orientation_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    weather_exposure_rule: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wind_reliable_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    park_factor_runs: Mapped[float | None] = mapped_column(Float, nullable=True)
    park_factor_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="mlb_stats_schedule")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "provider_venue_key", name="uq_mlb_venue_provider_key"),
        Index("ix_mlb_venues_name", "name"),
    )


class MlbEventVenue(Base):
    """Resolved venue for an MLB event."""

    __tablename__ = "mlb_event_venues"

    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("mlb_venues.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="mlb_stats_api")
    collected_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    event: Mapped[Event] = relationship()
    venue: Mapped[MlbVenue] = relationship()

    __table_args__ = (
        Index("ix_mlb_event_venues_venue", "venue_id"),
    )


class MlbParkFactor(Base):
    """Imported MLB park-factor values with source lineage."""

    __tablename__ = "mlb_park_factors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("mlb_venues.id"), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    rolling_years: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    imported_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    runs_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    hr_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    woba_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    hits_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    doubles_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    triples_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    venue: Mapped[MlbVenue] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "venue_id",
            "season",
            "rolling_years",
            "source",
            name="uq_mlb_park_factor_source",
        ),
        Index("ix_mlb_park_factors_venue_season", "venue_id", "season"),
    )


class MlbEnvironmentSnapshot(Base):
    """Append-only weather/wind context for an MLB event."""

    __tablename__ = "mlb_environment_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("mlb_venues.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    collected_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    forecast_for_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    temperature_f: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_mph: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_direction: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wind_from_degrees: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_to_center_alignment: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_out_mph: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_in_mph: Mapped[float | None] = mapped_column(Float, nullable=True)
    crosswind_mph: Mapped[float | None] = mapped_column(Float, nullable=True)
    field_wind_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    precipitation_chance: Mapped[float | None] = mapped_column(Float, nullable=True)
    conditions: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    event: Mapped[Event] = relationship()
    venue: Mapped[MlbVenue | None] = relationship()

    __table_args__ = (
        Index("ix_mlb_environment_event_time", "event_id", "collected_at_utc"),
        Index("ix_mlb_environment_forecast", "forecast_for_utc"),
    )


class MlbTeamGameLog(Base):
    """Final MLB team boxscore totals, one row per event/team."""

    __tablename__ = "mlb_team_game_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    game_date_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)
    opponent_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    runs_for: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runs_against: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    errors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    at_bats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doubles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    triples: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_on_balls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strike_outs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stolen_bases: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bullpen_outs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="mlb_stats_api")

    event: Mapped[Event] = relationship()
    team: Mapped[Team] = relationship(foreign_keys=[team_id])
    opponent: Mapped[Team] = relationship(foreign_keys=[opponent_team_id])

    __table_args__ = (
        UniqueConstraint("event_id", "team_id", name="uq_mlb_team_game_log"),
        Index("ix_mlb_team_logs_team_date", "team_id", "game_date_utc"),
    )


class MlbPlayerGameLog(Base):
    """Final MLB player boxscore totals, one row per event/player/team."""

    __tablename__ = "mlb_player_game_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    game_date_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)
    batting_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_abbrev: Mapped[str | None] = mapped_column(String(16), nullable=True)
    batting_started: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pitching_started: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    at_bats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doubles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    triples: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rbi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_on_balls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strike_outs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stolen_bases: Mapped[int | None] = mapped_column(Integer, nullable=True)
    innings_pitched_outs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pitching_hits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pitching_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    earned_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pitching_base_on_balls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pitching_strike_outs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pitching_home_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pitches_thrown: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="mlb_stats_api")

    event: Mapped[Event] = relationship()
    player: Mapped[Player] = relationship()
    team: Mapped[Team] = relationship()

    __table_args__ = (
        UniqueConstraint("event_id", "player_id", "team_id", name="uq_mlb_player_game_log"),
        Index("ix_mlb_player_logs_player_date", "player_id", "game_date_utc"),
        Index("ix_mlb_player_logs_team_date", "team_id", "game_date_utc"),
    )


class MlbStatcastDaily(Base):
    """Daily player features derived from Baseball Savant / Statcast rows."""

    __tablename__ = "mlb_statcast_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    key_mlbam: Mapped[str] = mapped_column(String(128), nullable=False)
    game_date_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    player_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    player_type: Mapped[str] = mapped_column(String(16), nullable=False)  # batter | pitcher
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    imported_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pitches: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plate_appearances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    at_bats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_bases: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strikeouts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    walks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    batted_balls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_exit_velocity: Mapped[float | None] = mapped_column(Float, nullable=True)
    hard_hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    barrel_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    xba_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    xslg_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    xwoba_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    whiff_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    csw_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    player: Mapped[Player | None] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "key_mlbam",
            "game_date_utc",
            "player_type",
            "source",
            name="uq_mlb_statcast_daily_player_date_type_source",
        ),
        Index("ix_mlb_statcast_daily_player_date", "player_id", "game_date_utc"),
        Index("ix_mlb_statcast_daily_mlbam_date", "key_mlbam", "game_date_utc"),
    )


class MlbProbableStarter(Base):
    """MLB probable/confirmed starting pitcher context."""

    __tablename__ = "mlb_probable_starters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    collected_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    event: Mapped[Event] = relationship()
    team: Mapped[Team] = relationship()
    player: Mapped[Player] = relationship()

    __table_args__ = (
        UniqueConstraint("event_id", "team_id", name="uq_mlb_probable_starter"),
        Index("ix_mlb_probable_starters_event", "event_id"),
    )
