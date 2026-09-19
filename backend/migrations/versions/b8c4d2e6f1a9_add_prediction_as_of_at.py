"""add point-in-time provenance for historical research predictions

Revision ID: b8c4d2e6f1a9
Revises: f7b9c2d4e6a8
"""

from alembic import op
import sqlalchemy as sa


revision = "b8c4d2e6f1a9"
down_revision = "a1b2c3d4e5f8"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("predictions")}
    if "as_of_at" not in columns:
        op.add_column(
            "predictions",
            sa.Column("as_of_at", sa.DateTime(timezone=True), nullable=True),
        )
    indexes = {index["name"] for index in inspector.get_indexes("predictions")}
    if "ix_predictions_as_of_at" not in indexes:
        op.create_index("ix_predictions_as_of_at", "predictions", ["as_of_at"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("predictions")}
    if "ix_predictions_as_of_at" in indexes:
        op.drop_index("ix_predictions_as_of_at", table_name="predictions")
    columns = {column["name"] for column in inspector.get_columns("predictions")}
    if "as_of_at" in columns:
        op.drop_column("predictions", "as_of_at")
