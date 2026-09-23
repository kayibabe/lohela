"""add accumulator_tickets.pricing (market vs model priced tickets)

Revision ID: b2d4f6a8c0e1
Revises: a1c3e5f7b9d2
"""
from alembic import op
import sqlalchemy as sa

revision = "b2d4f6a8c0e1"
down_revision = "a1c3e5f7b9d2"
branch_labels = None
depends_on = None


def upgrade():
    # Guarded like a1c3e5f7b9d2: fresh databases get the column from the ORM
    # baseline. Existing tickets were all model-priced, hence the default.
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accumulator_tickets")}
    if "pricing" not in existing:
        op.add_column(
            "accumulator_tickets",
            sa.Column("pricing", sa.String(16), nullable=False, server_default="model"),
        )


def downgrade():
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accumulator_tickets")}
    if "pricing" in existing:
        op.drop_column("accumulator_tickets", "pricing")
