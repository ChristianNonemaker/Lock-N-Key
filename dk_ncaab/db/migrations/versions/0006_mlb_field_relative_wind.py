"""Add MLB field-relative wind fields.

Revision ID: 0006_mlb_field_relative_wind
Revises: 0005_mlb_park_factors
Create Date: 2026-04-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_mlb_field_relative_wind"
down_revision: Union[str, None] = "0005_mlb_park_factors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("mlb_venues", sa.Column("orientation_deg", sa.Float(), nullable=True))
    op.add_column("mlb_venues", sa.Column("weather_exposure_rule", sa.String(64), nullable=True))
    op.add_column("mlb_venues", sa.Column("wind_reliable_flag", sa.Boolean(), nullable=True))
    op.add_column("mlb_environment_snapshots", sa.Column("wind_from_degrees", sa.Float(), nullable=True))
    op.add_column(
        "mlb_environment_snapshots",
        sa.Column("wind_to_center_alignment", sa.Float(), nullable=True),
    )
    op.add_column("mlb_environment_snapshots", sa.Column("wind_out_mph", sa.Float(), nullable=True))
    op.add_column("mlb_environment_snapshots", sa.Column("wind_in_mph", sa.Float(), nullable=True))
    op.add_column("mlb_environment_snapshots", sa.Column("crosswind_mph", sa.Float(), nullable=True))
    op.add_column(
        "mlb_environment_snapshots",
        sa.Column("field_wind_label", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mlb_environment_snapshots", "field_wind_label")
    op.drop_column("mlb_environment_snapshots", "crosswind_mph")
    op.drop_column("mlb_environment_snapshots", "wind_in_mph")
    op.drop_column("mlb_environment_snapshots", "wind_out_mph")
    op.drop_column("mlb_environment_snapshots", "wind_to_center_alignment")
    op.drop_column("mlb_environment_snapshots", "wind_from_degrees")
    op.drop_column("mlb_venues", "wind_reliable_flag")
    op.drop_column("mlb_venues", "weather_exposure_rule")
    op.drop_column("mlb_venues", "orientation_deg")
