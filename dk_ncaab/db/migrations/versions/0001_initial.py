"""Initial schema — all tables from models.py

Revision ID: 0001_initial
Revises: 
Create Date: 2026-02-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── leagues ──────────────────────────────────────────────────
    op.create_table(
        'leagues',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('key', sa.String(64), unique=True, nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
    )

    # ── teams ────────────────────────────────────────────────────
    op.create_table(
        'teams',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('league_id', sa.Integer(), sa.ForeignKey('leagues.id'), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('normalized_name', sa.String(128), nullable=False),
    )
    op.create_index('ix_teams_normalized_name', 'teams', ['normalized_name'])

    # ── team_aliases ─────────────────────────────────────────────
    op.create_table(
        'team_aliases',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=False),
        sa.Column('alias', sa.String(256), nullable=False),
        sa.Column('source', sa.String(64), nullable=False),
    )
    op.create_index('ix_team_aliases_source_alias', 'team_aliases', ['source', 'alias'])

    # ── kenpom_ratings ───────────────────────────────────────────
    op.create_table(
        'kenpom_ratings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=False),
        sa.Column('rating_date', sa.DateTime(), nullable=False),
        sa.Column('adj_o', sa.Float(), nullable=False),
        sa.Column('adj_d', sa.Float(), nullable=False),
        sa.Column('adj_em', sa.Float(), nullable=False),
        sa.Column('tempo', sa.Float(), nullable=False),
        sa.Column('sos', sa.Float(), nullable=True),
        sa.UniqueConstraint('team_id', 'rating_date', name='uq_kenpom_team_date'),
    )
    op.create_index('ix_kenpom_team_date', 'kenpom_ratings', ['team_id', 'rating_date'])

    # ── ap_rankings ──────────────────────────────────────────────
    op.create_table(
        'ap_rankings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=False),
        sa.Column('poll_date', sa.DateTime(), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('votes', sa.Integer(), nullable=True),
        sa.UniqueConstraint('team_id', 'poll_date', name='uq_ap_team_poll'),
    )
    op.create_index('ix_ap_poll_date', 'ap_rankings', ['poll_date'])

    # ── events ───────────────────────────────────────────────────
    op.create_table(
        'events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('league_id', sa.Integer(), sa.ForeignKey('leagues.id'), nullable=False),
        sa.Column('external_event_key', sa.String(256), unique=True, nullable=False),
        sa.Column('start_time_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('home_team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=False),
        sa.Column('away_team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='upcoming'),
        sa.Column('first_seen_at_utc', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_events_status_start', 'events', ['status', 'start_time_utc'])

    # ── odds_quotes ──────────────────────────────────────────────
    op.create_table(
        'odds_quotes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('events.id'), nullable=False),
        sa.Column('book', sa.String(64), nullable=False),
        sa.Column('market', sa.String(20), nullable=False),
        sa.Column('side', sa.String(10), nullable=False),
        sa.Column('line', sa.Float(), nullable=True),
        sa.Column('price_american', sa.Integer(), nullable=False),
        sa.Column('implied_probability', sa.Float(), nullable=True),
        sa.Column('collected_at_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(64), nullable=False),
        sa.UniqueConstraint(
            'event_id', 'book', 'market', 'side',
            'price_american', 'line', 'collected_at_utc',
            name='uq_odds_dedup',
        ),
    )
    op.create_index(
        'ix_odds_event_market_side_time',
        'odds_quotes',
        ['event_id', 'market', 'side', 'collected_at_utc'],
    )

    # ── odds_raw_payloads ────────────────────────────────────────
    op.create_table(
        'odds_raw_payloads',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('collected_at_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(64), nullable=False),
        sa.Column('payload_json', sa.JSON(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
    )

    # ── splits_quotes ────────────────────────────────────────────
    op.create_table(
        'splits_quotes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('events.id'), nullable=False),
        sa.Column('market', sa.String(20), nullable=False),
        sa.Column('side', sa.String(10), nullable=False),
        sa.Column('bets_pct', sa.Float(), nullable=False),
        sa.Column('handle_pct', sa.Float(), nullable=False),
        sa.Column('collected_at_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(64), nullable=False),
    )
    op.create_index(
        'ix_splits_event_market_side_time',
        'splits_quotes',
        ['event_id', 'market', 'side', 'collected_at_utc'],
    )

    # ── splits_raw_payloads ──────────────────────────────────────
    op.create_table(
        'splits_raw_payloads',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('collected_at_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('payload_html', sa.Text(), nullable=True),
        sa.Column('screenshot_path', sa.String(512), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
    )

    # ── unmatched_splits ─────────────────────────────────────────
    op.create_table(
        'unmatched_splits',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('collected_at_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('raw_team_a', sa.String(256), nullable=False),
        sa.Column('raw_team_b', sa.String(256), nullable=False),
        sa.Column('market', sa.String(20), nullable=False),
        sa.Column('side', sa.String(10), nullable=False),
        sa.Column('bets_pct', sa.Float(), nullable=False),
        sa.Column('handle_pct', sa.Float(), nullable=False),
        sa.Column('raw_text', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
    )

    # ── event_results ────────────────────────────────────────────
    op.create_table(
        'event_results',
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('events.id'), primary_key=True),
        sa.Column('home_score', sa.Integer(), nullable=False),
        sa.Column('away_score', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('completed_at_utc', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('event_results')
    op.drop_table('unmatched_splits')
    op.drop_table('splits_raw_payloads')
    op.drop_table('splits_quotes')
    op.drop_table('odds_raw_payloads')
    op.drop_table('odds_quotes')
    op.drop_table('events')
    op.drop_table('ap_rankings')
    op.drop_table('kenpom_ratings')
    op.drop_table('team_aliases')
    op.drop_table('teams')
    op.drop_table('leagues')
