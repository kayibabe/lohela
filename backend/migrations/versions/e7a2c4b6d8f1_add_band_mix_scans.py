"""add daily best-performing band-mix scans

Revision ID: e7a2c4b6d8f1
Revises: b2d4f6a8c0e1
"""

from alembic import op
import sqlalchemy as sa


revision = "e7a2c4b6d8f1"
down_revision = "b2d4f6a8c0e1"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "band_mix_scans" in inspector.get_table_names():
        return
    op.create_table(
        "band_mix_scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("evidence_through", sa.Date(), nullable=False),
        sa.Column("capture_source", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_band_mix_scans_target_date", "band_mix_scans", ["target_date"], unique=True
    )


def downgrade():
    op.drop_index("ix_band_mix_scans_target_date", table_name="band_mix_scans")
    op.drop_table("band_mix_scans")
