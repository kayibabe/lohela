"""add frozen market-policy shadow snapshots

Revision ID: c2d4e6f8a1b3
Revises: b1d7f3a9c5e2
"""

from alembic import op
import sqlalchemy as sa


revision = "c2d4e6f8a1b3"
down_revision = "b1d7f3a9c5e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The historical baseline migration creates the then-current ORM metadata
    # on a fresh database. Guard this explicit revision so both fresh and
    # upgraded databases converge safely.
    if sa.inspect(op.get_bind()).has_table("market_policy_shadow_snapshots"):
        return
    op.create_table(
        "market_policy_shadow_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("model_run_id", sa.Integer(), sa.ForeignKey("model_runs.id"), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("excluded_markets", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("target_date", "model_run_id", "policy_version", name="uq_market_policy_shadow_snapshot"),
    )
    op.create_index("ix_market_policy_shadow_snapshots_target_date", "market_policy_shadow_snapshots", ["target_date"])
    op.create_index("ix_market_policy_shadow_snapshots_model_run_id", "market_policy_shadow_snapshots", ["model_run_id"])


def downgrade() -> None:
    op.drop_index("ix_market_policy_shadow_snapshots_model_run_id", table_name="market_policy_shadow_snapshots")
    op.drop_index("ix_market_policy_shadow_snapshots_target_date", table_name="market_policy_shadow_snapshots")
    op.drop_table("market_policy_shadow_snapshots")
