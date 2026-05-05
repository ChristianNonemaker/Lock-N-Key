"""Add MLB venue and environment context tables.

Revision ID: 0004_mlb_environment_context
Revises: 0003_mlb_stats_foundation
Create Date: 2026-04-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_mlb_environment_context"
down_revision: Union[str, None] = "0003_mlb_stats_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mlb_venues",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(64), nullable=False, server_default="mlb_stats_api"),
        sa.Column("provider_venue_key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("roof_type", sa.String(64), nullable=True),
        sa.Column("park_factor_runs", sa.Float(), nullable=True),
        sa.Column("park_factor_hr", sa.Float(), nullable=True),
        sa.Column("source", sa.String(64), nullable=False, server_default="mlb_stats_schedule"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("provider", "provider_venue_key", name="uq_mlb_venue_provider_key"),
    )
    op.create_index("ix_mlb_venues_name", "mlb_venues", ["name"])

    op.create_table(
        "mlb_event_venues",
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), primary_key=True),
        sa.Column("venue_id", sa.Integer(), sa.ForeignKey("mlb_venues.id"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False, server_default="mlb_stats_api"),
        sa.Column("collected_at_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mlb_event_venues_venue", "mlb_event_venues", ["venue_id"])

    op.create_table(
        "mlb_environment_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("venue_id", sa.Integer(), sa.ForeignKey("mlb_venues.id"), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("collected_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forecast_for_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("temperature_f", sa.Float(), nullable=True),
        sa.Column("wind_mph", sa.Float(), nullable=True),
        sa.Column("wind_direction", sa.String(64), nullable=True),
        sa.Column("precipitation_chance", sa.Float(), nullable=True),
        sa.Column("conditions", sa.String(256), nullable=True),
        sa.Column("source_url", sa.String(512), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_mlb_environment_event_time",
        "mlb_environment_snapshots",
        ["event_id", "collected_at_utc"],
    )
    op.create_index(
        "ix_mlb_environment_forecast",
        "mlb_environment_snapshots",
        ["forecast_for_utc"],
    )


def downgrade() -> None:
    op.drop_index("ix_mlb_environment_forecast", table_name="mlb_environment_snapshots")
    op.drop_index("ix_mlb_environment_event_time", table_name="mlb_environment_snapshots")
    op.drop_table("mlb_environment_snapshots")
    op.drop_index("ix_mlb_event_venues_venue", table_name="mlb_event_venues")
    op.drop_table("mlb_event_venues")
    op.drop_index("ix_mlb_venues_name", table_name="mlb_venues")
    op.drop_table("mlb_venues")

