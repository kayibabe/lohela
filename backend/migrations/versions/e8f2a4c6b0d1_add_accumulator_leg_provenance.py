"""add immutable source-ticket provenance to accumulator legs"""

from alembic import op
import sqlalchemy as sa


revision = "e8f2a4c6b0d1"
down_revision = "d6e8f1a3b5c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("custom_accumulator_legs")}
    if "source_ticket_id" not in columns:
        op.add_column("custom_accumulator_legs", sa.Column("source_ticket_id", sa.Integer(), nullable=True))
        op.create_index("ix_custom_accumulator_legs_source_ticket_id", "custom_accumulator_legs", ["source_ticket_id"])
    if "source_ticket_type" not in columns:
        op.add_column("custom_accumulator_legs", sa.Column("source_ticket_type", sa.String(length=32), nullable=True))
    if "source_ticket_version" not in columns:
        op.add_column("custom_accumulator_legs", sa.Column("source_ticket_version", sa.Integer(), nullable=True))
    if "source_conflict" not in columns:
        op.add_column("custom_accumulator_legs", sa.Column("source_conflict", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("custom_accumulator_legs")}
    if "source_conflict" in columns:
        op.drop_column("custom_accumulator_legs", "source_conflict")
    if "source_ticket_version" in columns:
        op.drop_column("custom_accumulator_legs", "source_ticket_version")
    if "source_ticket_type" in columns:
        op.drop_column("custom_accumulator_legs", "source_ticket_type")
    if "source_ticket_id" in columns:
        op.drop_index("ix_custom_accumulator_legs_source_ticket_id", table_name="custom_accumulator_legs")
        op.drop_column("custom_accumulator_legs", "source_ticket_id")
