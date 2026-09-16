"""add closing-line-value columns to predictions

Revision ID: f3a8c1e5d9b2
Revises: c8e2a4f6d1b3
"""
from alembic import op
import sqlalchemy as sa

revision = "f3a8c1e5d9b2"
down_revision = "c8e2a4f6d1b3"
branch_labels = None
depends_on = None


def upgrade():
    # Guarded: the baseline migration (3d52766f0fb9) creates every table from
    # the current ORM models, so a fresh database already has these columns.
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("predictions")}
    if "closing_decimal_odds" not in existing:
        op.add_column("predictions", sa.Column("closing_decimal_odds", sa.Float(), nullable=True))
    if "closing_implied_probability" not in existing:
        op.add_column("predictions", sa.Column("closing_implied_probability", sa.Float(), nullable=True))
    if "closing_odds_at" not in existing:
        op.add_column("predictions", sa.Column("closing_odds_at", sa.DateTime(timezone=True), nullable=True))
    if "clv_percentage" not in existing:
        op.add_column("predictions", sa.Column("clv_percentage", sa.Float(), nullable=True))


def downgrade():
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("predictions")}
    if "clv_percentage" in existing:
        op.drop_column("predictions", "clv_percentage")
    if "closing_odds_at" in existing:
        op.drop_column("predictions", "closing_odds_at")
    if "closing_implied_probability" in existing:
        op.drop_column("predictions", "closing_implied_probability")
    if "closing_decimal_odds" in existing:
        op.drop_column("predictions", "closing_decimal_odds")
