"""Add event-specific odds quotes for team totals and player props.

Revision ID: 0007_event_odds_quotes
Revises: 0006_mlb_field_relative_wind
Create Date: 2026-04-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_event_odds_quotes"
down_revision: Union[str, None] = "0006_mlb_field_relative_wind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_odds_quotes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("book", sa.String(length=64), nullable=False),
        sa.Column("market_key", sa.String(length=64), nullable=False),
        sa.Column("provider_market_key", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("participant_name", sa.String(length=256), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("line", sa.Float(), nullable=True),
        sa.Column("price_american", sa.Integer(), nullable=False),
        sa.Column("implied_probability", sa.Float(), nullable=True),
        sa.Column("provider_updated_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
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
    )
    op.create_index(
        "ix_event_odds_quotes_event_market_time",
        "event_odds_quotes",
        ["event_id", "market_key", "collected_at_utc"],
    )
    op.create_index(
        "ix_event_odds_quotes_event_player_market",
        "event_odds_quotes",
        ["event_id", "player_id", "market_key"],
    )
    op.create_index(
        "ix_event_odds_quotes_event_team_market",
        "event_odds_quotes",
        ["event_id", "team_id", "market_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_event_odds_quotes_event_team_market", table_name="event_odds_quotes")
    op.drop_index("ix_event_odds_quotes_event_player_market", table_name="event_odds_quotes")
    op.drop_index("ix_event_odds_quotes_event_market_time", table_name="event_odds_quotes")
    op.drop_table("event_odds_quotes")
