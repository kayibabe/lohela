"""add horizon_days for the rolling-horizon minimum-ticket fallback

Revision ID: a1c3e5f7b9d2
Revises: d4e6f8a1b3c5
"""
from alembic import op
import sqlalchemy as sa

revision = "a1c3e5f7b9d2"
down_revision = "d4e6f8a1b3c5"
branch_labels = None
depends_on = None


def upgrade():
    # Guarded like c8e2a4f6d1b3: the baseline migration creates tables from the
    # current ORM models, so a fresh database already has this column.
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accumulator_tickets")}
    if "horizon_days" not in existing:
        op.add_column(
            "accumulator_tickets",
            sa.Column("horizon_days", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade():
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accumulator_tickets")}
    if "horizon_days" in existing:
        op.drop_column("accumulator_tickets", "horizon_days")
