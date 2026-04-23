"""Add provider-backed MLB stats foundation.

Revision ID: 0003_mlb_stats_foundation
Revises: 0002_odds_api_usage
Create Date: 2026-04-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_mlb_stats_foundation"
down_revision: Union[str, None] = "0002_odds_api_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_provider_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("sport_key", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_event_key", sa.String(256), nullable=False),
        sa.Column(
            "first_seen_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("provider", "provider_event_key", name="uq_event_provider_key"),
        sa.UniqueConstraint("event_id", "provider", name="uq_event_provider_by_event"),
    )
    op.create_index("ix_event_provider_keys_event", "event_provider_keys", ["event_id"])

    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("league_id", sa.Integer(), sa.ForeignKey("leagues.id"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("external_player_key", sa.String(128), nullable=False),
        sa.Column("full_name", sa.String(256), nullable=False),
        sa.Column("primary_position", sa.String(32), nullable=True),
        sa.Column("bats", sa.String(16), nullable=True),
        sa.Column("throws", sa.String(16), nullable=True),
        sa.UniqueConstraint("provider", "external_player_key", name="uq_player_provider_key"),
    )
    op.create_index("ix_players_league_name", "players", ["league_id", "full_name"])

    op.create_table(
        "mlb_stats_raw_payloads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("collected_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("endpoint", sa.String(256), nullable=False),
        sa.Column("provider_event_key", sa.String(256), nullable=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_mlb_raw_event", "mlb_stats_raw_payloads", ["event_id"])
    op.create_index(
        "ix_mlb_raw_provider_event",
        "mlb_stats_raw_payloads",
        ["provider_event_key"],
    )

    op.create_table(
        "mlb_team_game_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("game_date_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_home", sa.Boolean(), nullable=False),
        sa.Column("opponent_team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("runs_for", sa.Integer(), nullable=True),
        sa.Column("runs_against", sa.Integer(), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=True),
        sa.Column("errors", sa.Integer(), nullable=True),
        sa.Column("at_bats", sa.Integer(), nullable=True),
        sa.Column("doubles", sa.Integer(), nullable=True),
        sa.Column("triples", sa.Integer(), nullable=True),
        sa.Column("home_runs", sa.Integer(), nullable=True),
        sa.Column("base_on_balls", sa.Integer(), nullable=True),
        sa.Column("strike_outs", sa.Integer(), nullable=True),
        sa.Column("stolen_bases", sa.Integer(), nullable=True),
        sa.Column("bullpen_outs", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(64), nullable=False, server_default="mlb_stats_api"),
        sa.UniqueConstraint("event_id", "team_id", name="uq_mlb_team_game_log"),
    )
    op.create_index(
        "ix_mlb_team_logs_team_date",
        "mlb_team_game_logs",
        ["team_id", "game_date_utc"],
    )

    op.create_table(
        "mlb_player_game_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("game_date_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_home", sa.Boolean(), nullable=False),
        sa.Column("batting_order", sa.Integer(), nullable=True),
        sa.Column("position_abbrev", sa.String(16), nullable=True),
        sa.Column("batting_started", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pitching_started", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("at_bats", sa.Integer(), nullable=True),
        sa.Column("runs", sa.Integer(), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=True),
        sa.Column("doubles", sa.Integer(), nullable=True),
        sa.Column("triples", sa.Integer(), nullable=True),
        sa.Column("home_runs", sa.Integer(), nullable=True),
        sa.Column("rbi", sa.Integer(), nullable=True),
        sa.Column("base_on_balls", sa.Integer(), nullable=True),
        sa.Column("strike_outs", sa.Integer(), nullable=True),
        sa.Column("stolen_bases", sa.Integer(), nullable=True),
        sa.Column("innings_pitched_outs", sa.Integer(), nullable=True),
        sa.Column("pitching_hits", sa.Integer(), nullable=True),
        sa.Column("pitching_runs", sa.Integer(), nullable=True),
        sa.Column("earned_runs", sa.Integer(), nullable=True),
        sa.Column("pitching_base_on_balls", sa.Integer(), nullable=True),
        sa.Column("pitching_strike_outs", sa.Integer(), nullable=True),
        sa.Column("pitching_home_runs", sa.Integer(), nullable=True),
        sa.Column("pitches_thrown", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(64), nullable=False, server_default="mlb_stats_api"),
        sa.UniqueConstraint(
            "event_id",
            "player_id",
            "team_id",
            name="uq_mlb_player_game_log",
        ),
    )
    op.create_index(
        "ix_mlb_player_logs_player_date",
        "mlb_player_game_logs",
        ["player_id", "game_date_utc"],
    )
    op.create_index(
        "ix_mlb_player_logs_team_date",
        "mlb_player_game_logs",
        ["team_id", "game_date_utc"],
    )

    op.create_table(
        "mlb_probable_starters",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("is_home", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("collected_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", "team_id", name="uq_mlb_probable_starter"),
    )
    op.create_index(
        "ix_mlb_probable_starters_event",
        "mlb_probable_starters",
        ["event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_mlb_probable_starters_event", table_name="mlb_probable_starters")
    op.drop_table("mlb_probable_starters")
    op.drop_index("ix_mlb_player_logs_team_date", table_name="mlb_player_game_logs")
    op.drop_index("ix_mlb_player_logs_player_date", table_name="mlb_player_game_logs")
    op.drop_table("mlb_player_game_logs")
    op.drop_index("ix_mlb_team_logs_team_date", table_name="mlb_team_game_logs")
    op.drop_table("mlb_team_game_logs")
    op.drop_index("ix_mlb_raw_provider_event", table_name="mlb_stats_raw_payloads")
    op.drop_index("ix_mlb_raw_event", table_name="mlb_stats_raw_payloads")
    op.drop_table("mlb_stats_raw_payloads")
    op.drop_index("ix_players_league_name", table_name="players")
    op.drop_table("players")
    op.drop_index("ix_event_provider_keys_event", table_name="event_provider_keys")
    op.drop_table("event_provider_keys")
