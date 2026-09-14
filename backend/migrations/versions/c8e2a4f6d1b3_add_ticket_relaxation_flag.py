"""add relaxed-tier flag for adaptive minimum-ticket fallback

Revision ID: c8e2a4f6d1b3
Revises: a7c9e1f3b5d7
"""
from alembic import op
import sqlalchemy as sa

revision = "c8e2a4f6d1b3"
down_revision = "a7c9e1f3b5d7"
branch_labels = None
depends_on = None


def upgrade():
    # Guarded: the historical baseline migration (3d52766f0fb9) dynamically
    # creates every table from the *current* ORM models, so a fresh database
    # already has these columns.
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accumulator_tickets")}
    if "relaxed_tier" not in existing:
        op.add_column(
            "accumulator_tickets",
            sa.Column("relaxed_tier", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "relaxation_level" not in existing:
        op.add_column(
            "accumulator_tickets",
            sa.Column("relaxation_level", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade():
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accumulator_tickets")}
    if "relaxation_level" in existing:
        op.drop_column("accumulator_tickets", "relaxation_level")
    if "relaxed_tier" in existing:
        op.drop_column("accumulator_tickets", "relaxed_tier")
