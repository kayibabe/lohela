"""index prediction source odds foreign key for efficient refresh cleanup"""

from alembic import op

revision = "fa02a7c4d1e8"
down_revision = "f9d1e4a8b6c2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_predictions_source_odds_id",
        "predictions",
        ["source_odds_id"],
    )


def downgrade():
    op.drop_index("ix_predictions_source_odds_id", table_name="predictions")
