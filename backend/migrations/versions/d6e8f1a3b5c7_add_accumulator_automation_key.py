"""add an idempotency key for system-generated accumulators

Revision ID: d6e8f1a3b5c7
Revises: c2d4e6f8a1b3
"""

from alembic import op
import sqlalchemy as sa


revision = "d6e8f1a3b5c7"
down_revision = "c2d4e6f8a1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("custom_accumulators")}
    if "automation_key" not in columns:
        op.add_column(
            "custom_accumulators",
            sa.Column("automation_key", sa.String(length=80), nullable=True),
        )
        op.create_index(
            "ix_custom_accumulators_automation_key",
            "custom_accumulators",
            ["automation_key"],
            unique=True,
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("custom_accumulators")}
    if "automation_key" in columns:
        op.drop_index("ix_custom_accumulators_automation_key", table_name="custom_accumulators")
        op.drop_column("custom_accumulators", "automation_key")
