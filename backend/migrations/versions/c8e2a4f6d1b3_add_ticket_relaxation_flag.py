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
    op.add_column(
        "accumulator_tickets",
        sa.Column("relaxed_tier", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "accumulator_tickets",
        sa.Column("relaxation_level", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("accumulator_tickets", "relaxation_level")
    op.drop_column("accumulator_tickets", "relaxed_tier")
