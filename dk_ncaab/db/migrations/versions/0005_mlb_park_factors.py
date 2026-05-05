"""Add MLB park factor lineage table.

Revision ID: 0005_mlb_park_factors
Revises: 0004_mlb_environment_context
Create Date: 2026-04-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_mlb_park_factors"
down_revision: Union[str, None] = "0004_mlb_environment_context"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mlb_park_factors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("venue_id", sa.Integer(), sa.ForeignKey("mlb_venues.id"), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("rolling_years", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("source_url", sa.String(512), nullable=True),
        sa.Column("imported_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("runs_factor", sa.Float(), nullable=True),
        sa.Column("hr_factor", sa.Float(), nullable=True),
        sa.Column("woba_factor", sa.Float(), nullable=True),
        sa.Column("hits_factor", sa.Float(), nullable=True),
        sa.Column("doubles_factor", sa.Float(), nullable=True),
        sa.Column("triples_factor", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "venue_id",
            "season",
            "rolling_years",
            "source",
            name="uq_mlb_park_factor_source",
        ),
    )
    op.create_index(
        "ix_mlb_park_factors_venue_season",
        "mlb_park_factors",
        ["venue_id", "season"],
    )


def downgrade() -> None:
    op.drop_index("ix_mlb_park_factors_venue_season", table_name="mlb_park_factors")
    op.drop_table("mlb_park_factors")
