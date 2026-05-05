"""Add MLB player identity crosswalk and Statcast daily features.

Revision ID: 0008_mlb_identity_statcast
Revises: 0007_event_odds_quotes
Create Date: 2026-05-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_mlb_identity_statcast"
down_revision: Union[str, None] = "0007_event_odds_quotes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mlb_player_id_crosswalks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("key_mlbam", sa.String(length=128), nullable=True),
        sa.Column("key_retro", sa.String(length=128), nullable=True),
        sa.Column("key_bbref", sa.String(length=128), nullable=True),
        sa.Column("key_fangraphs", sa.String(length=128), nullable=True),
        sa.Column("name_first", sa.String(length=128), nullable=True),
        sa.Column("name_last", sa.String(length=128), nullable=True),
        sa.Column("name_full", sa.String(length=256), nullable=True),
        sa.Column("mlb_played_first", sa.Integer(), nullable=True),
        sa.Column("mlb_played_last", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("source_row_key", sa.String(length=512), nullable=False),
        sa.Column("imported_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "source_row_key", name="uq_mlb_player_xwalk_source_row"),
    )
    op.create_index("ix_mlb_player_xwalk_mlbam", "mlb_player_id_crosswalks", ["key_mlbam"])
    op.create_index("ix_mlb_player_xwalk_retro", "mlb_player_id_crosswalks", ["key_retro"])
    op.create_index(
        "ix_mlb_player_xwalk_fangraphs",
        "mlb_player_id_crosswalks",
        ["key_fangraphs"],
    )
    op.create_index("ix_mlb_player_xwalk_player", "mlb_player_id_crosswalks", ["player_id"])

    op.create_table(
        "mlb_statcast_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("key_mlbam", sa.String(length=128), nullable=False),
        sa.Column("game_date_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("player_name", sa.String(length=256), nullable=True),
        sa.Column("player_type", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.String(length=512), nullable=True),
        sa.Column("imported_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pitches", sa.Integer(), nullable=True),
        sa.Column("plate_appearances", sa.Integer(), nullable=True),
        sa.Column("at_bats", sa.Integer(), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=True),
        sa.Column("total_bases", sa.Integer(), nullable=True),
        sa.Column("home_runs", sa.Integer(), nullable=True),
        sa.Column("strikeouts", sa.Integer(), nullable=True),
        sa.Column("walks", sa.Integer(), nullable=True),
        sa.Column("batted_balls", sa.Integer(), nullable=True),
        sa.Column("avg_exit_velocity", sa.Float(), nullable=True),
        sa.Column("hard_hit_rate", sa.Float(), nullable=True),
        sa.Column("barrel_rate", sa.Float(), nullable=True),
        sa.Column("xba_mean", sa.Float(), nullable=True),
        sa.Column("xslg_mean", sa.Float(), nullable=True),
        sa.Column("xwoba_mean", sa.Float(), nullable=True),
        sa.Column("whiff_rate", sa.Float(), nullable=True),
        sa.Column("csw_rate", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "key_mlbam",
            "game_date_utc",
            "player_type",
            "source",
            name="uq_mlb_statcast_daily_player_date_type_source",
        ),
    )
    op.create_index(
        "ix_mlb_statcast_daily_player_date",
        "mlb_statcast_daily",
        ["player_id", "game_date_utc"],
    )
    op.create_index(
        "ix_mlb_statcast_daily_mlbam_date",
        "mlb_statcast_daily",
        ["key_mlbam", "game_date_utc"],
    )


def downgrade() -> None:
    op.drop_index("ix_mlb_statcast_daily_mlbam_date", table_name="mlb_statcast_daily")
    op.drop_index("ix_mlb_statcast_daily_player_date", table_name="mlb_statcast_daily")
    op.drop_table("mlb_statcast_daily")
    op.drop_index("ix_mlb_player_xwalk_player", table_name="mlb_player_id_crosswalks")
    op.drop_index("ix_mlb_player_xwalk_fangraphs", table_name="mlb_player_id_crosswalks")
    op.drop_index("ix_mlb_player_xwalk_retro", table_name="mlb_player_id_crosswalks")
    op.drop_index("ix_mlb_player_xwalk_mlbam", table_name="mlb_player_id_crosswalks")
    op.drop_table("mlb_player_id_crosswalks")
