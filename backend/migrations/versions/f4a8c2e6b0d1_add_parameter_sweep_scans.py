"""add frozen daily dynamic parameter-policy scans

Revision ID: f4a8c2e6b0d1
Revises: f2b4d6e8a0c3
"""

from alembic import op
import sqlalchemy as sa


revision = "f4a8c2e6b0d1"
down_revision = "f2b4d6e8a0c3"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "parameter_sweep_scans" in inspector.get_table_names():
        return
    op.create_table(
        "parameter_sweep_scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("evidence_through", sa.Date(), nullable=False),
        sa.Column("capture_source", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_parameter_sweep_scans_target_date",
        "parameter_sweep_scans",
        ["target_date"],
        unique=True,
    )


def downgrade():
    op.drop_index("ix_parameter_sweep_scans_target_date", table_name="parameter_sweep_scans")
    op.drop_table("parameter_sweep_scans")
