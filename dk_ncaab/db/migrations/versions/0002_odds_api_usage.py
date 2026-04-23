"""Add append-only Odds API usage table.

Revision ID: 0002_odds_api_usage
Revises: 0001_initial
Create Date: 2026-04-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_odds_api_usage"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "odds_api_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("requested_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sport_key", sa.String(64), nullable=False),
        sa.Column("provider_sport_key", sa.String(64), nullable=False),
        sa.Column("endpoint", sa.String(256), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requests_used", sa.Integer(), nullable=True),
        sa.Column("requests_remaining", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_odds_api_usage_sport_time",
        "odds_api_usage",
        ["sport_key", "requested_at_utc"],
    )
    op.create_index(
        "ix_odds_api_usage_requested_at",
        "odds_api_usage",
        ["requested_at_utc"],
    )


def downgrade() -> None:
    op.drop_index("ix_odds_api_usage_requested_at", table_name="odds_api_usage")
    op.drop_index("ix_odds_api_usage_sport_time", table_name="odds_api_usage")
    op.drop_table("odds_api_usage")
