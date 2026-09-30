"""add automatic band-mix lifecycle and daily pick ledger

Revision ID: f5b9d3e7a1c4
Revises: f4a8c2e6b0d1
"""

from alembic import op
import sqlalchemy as sa


revision = "f5b9d3e7a1c4"
down_revision = "f4a8c2e6b0d1"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = inspector.get_table_names()

    if "band_mix_pair_states" not in tables:
        op.create_table(
            "band_mix_pair_states",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("policy_version", sa.String(length=40), nullable=False),
            sa.Column("lohela_band", sa.String(length=20), nullable=False),
            sa.Column("market_band", sa.String(length=20), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("first_promoted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_evidence_through", sa.Date(), nullable=False),
            sa.Column("last_sample_size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_roi", sa.Float(), nullable=True),
            sa.Column("last_reason", sa.String(length=40), nullable=True),
            sa.UniqueConstraint("policy_version", "lohela_band", "market_band", name="uq_band_mix_pair_state"),
        )
        op.create_index("ix_band_mix_pair_states_policy_version", "band_mix_pair_states", ["policy_version"])

    if "band_mix_lifecycle_events" not in tables:
        op.create_table(
            "band_mix_lifecycle_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("target_date", sa.Date(), nullable=False),
            sa.Column("evidence_through", sa.Date(), nullable=False),
            sa.Column("policy_version", sa.String(length=40), nullable=False),
            sa.Column("lohela_band", sa.String(length=20), nullable=False),
            sa.Column("market_band", sa.String(length=20), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("transition", sa.String(length=24), nullable=False),
            sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("roi", sa.Float(), nullable=True),
            sa.Column("reason", sa.String(length=40), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("target_date", "policy_version", "lohela_band", "market_band", name="uq_band_mix_lifecycle_day"),
        )
        op.create_index("ix_band_mix_lifecycle_events_target_date", "band_mix_lifecycle_events", ["target_date"])

    if "band_mix_daily_picks" not in tables:
        op.create_table(
            "band_mix_daily_picks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("target_date", sa.Date(), nullable=False),
            sa.Column("prediction_id", sa.Integer(), nullable=False),
            sa.Column("match_id", sa.Integer(), nullable=False),
            sa.Column("lohela_band", sa.String(length=20), nullable=False),
            sa.Column("market_band", sa.String(length=20), nullable=False),
            sa.Column("mix_roi", sa.Float(), nullable=True),
            sa.Column("mix_sample_size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("result", sa.String(length=12), nullable=True),
            sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("target_date", "prediction_id", name="uq_band_mix_daily_pick"),
        )
        op.create_index("ix_band_mix_daily_picks_target_date", "band_mix_daily_picks", ["target_date"])
        op.create_index("ix_band_mix_daily_picks_prediction_id", "band_mix_daily_picks", ["prediction_id"])
        op.create_index("ix_band_mix_daily_picks_match_id", "band_mix_daily_picks", ["match_id"])


def downgrade():
    op.drop_index("ix_band_mix_daily_picks_match_id", table_name="band_mix_daily_picks")
    op.drop_index("ix_band_mix_daily_picks_prediction_id", table_name="band_mix_daily_picks")
    op.drop_index("ix_band_mix_daily_picks_target_date", table_name="band_mix_daily_picks")
    op.drop_table("band_mix_daily_picks")
    op.drop_index("ix_band_mix_lifecycle_events_target_date", table_name="band_mix_lifecycle_events")
    op.drop_table("band_mix_lifecycle_events")
    op.drop_index("ix_band_mix_pair_states_policy_version", table_name="band_mix_pair_states")
    op.drop_table("band_mix_pair_states")
