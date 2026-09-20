"""add insert-only singles_ledger_snapshots table for the real prospective ledger

Revision ID: c3f8a1d9e2b7
Revises: b8c4d2e6f1a9
"""

from alembic import op
import sqlalchemy as sa


revision = "c3f8a1d9e2b7"
down_revision = "b8c4d2e6f1a9"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "singles_ledger_snapshots" in inspector.get_table_names():
        return
    op.create_table(
        "singles_ledger_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("model_version", sa.String(length=40), nullable=False),
        sa.Column("picks_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("sha256", name="uq_singles_ledger_snapshot_sha256"),
        sa.UniqueConstraint("captured_at", name="uq_singles_ledger_snapshot_captured_at"),
    )
    op.create_index("ix_singles_ledger_snapshots_sha256", "singles_ledger_snapshots", ["sha256"])
    op.create_index("ix_singles_ledger_snapshots_captured_at", "singles_ledger_snapshots", ["captured_at"])
    op.create_index("ix_singles_ledger_snapshots_start_date", "singles_ledger_snapshots", ["start_date"])
    op.create_index("ix_singles_ledger_snapshots_model_version", "singles_ledger_snapshots", ["model_version"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "singles_ledger_snapshots" in inspector.get_table_names():
        op.drop_table("singles_ledger_snapshots")
