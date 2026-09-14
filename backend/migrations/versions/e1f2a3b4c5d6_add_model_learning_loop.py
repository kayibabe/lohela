"""add versioned model-learning challengers and promotions

Revision ID: e1f2a3b4c5d6
Revises: d9a7e3c5b1f4
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e1f2a3b4c5d6"
down_revision = "d9a7e3c5b1f4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_learning_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("base_model_version", sa.String(length=40), nullable=False),
        sa.Column("challenger_version", sa.String(length=40), nullable=False),
        sa.Column("status", postgresql.ENUM(name="runstatus", create_type=False), nullable=False),
        sa.Column("train_start", sa.Date(), nullable=False),
        sa.Column("train_end", sa.Date(), nullable=False),
        sa.Column("validation_start", sa.Date(), nullable=False),
        sa.Column("validation_end", sa.Date(), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("challenger_version", name="uq_model_learning_challenger_version"),
    )
    op.create_index("ix_model_learning_runs_base_model_version", "model_learning_runs", ["base_model_version"])
    op.create_index("ix_model_learning_runs_challenger_version", "model_learning_runs", ["challenger_version"])

    op.create_table(
        "market_learning_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("learning_run_id", sa.Integer(), nullable=False),
        sa.Column("market", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("train_sample_size", sa.Integer(), nullable=False),
        sa.Column("validation_sample_size", sa.Integer(), nullable=False),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column("calibrator", sa.JSON(), nullable=False),
        sa.Column("train_metrics", sa.JSON(), nullable=False),
        sa.Column("validation_metrics", sa.JSON(), nullable=False),
        sa.Column("promotion_checks", sa.JSON(), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["learning_run_id"], ["model_learning_runs.id"]),
        sa.UniqueConstraint("learning_run_id", "market", name="uq_learning_profile_market"),
    )
    op.create_index("ix_market_learning_profiles_learning_run_id", "market_learning_profiles", ["learning_run_id"])
    op.create_index("ix_market_learning_profiles_market", "market_learning_profiles", ["market"])

    op.create_table(
        "model_learning_promotions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("learning_run_id", sa.Integer(), nullable=False),
        sa.Column("base_model_version", sa.String(length=40), nullable=False),
        sa.Column("challenger_version", sa.String(length=40), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("promoted_by", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["learning_run_id"], ["model_learning_runs.id"]),
        sa.UniqueConstraint("learning_run_id", name="uq_model_learning_promotion_run"),
    )
    op.create_index("ix_model_learning_promotions_learning_run_id", "model_learning_promotions", ["learning_run_id"])
    op.create_index("ix_model_learning_promotions_base_model_version", "model_learning_promotions", ["base_model_version"])
    op.create_index("ix_model_learning_promotions_challenger_version", "model_learning_promotions", ["challenger_version"])
    op.create_index("ix_model_learning_promotions_effective_from", "model_learning_promotions", ["effective_from"])

    op.add_column("predictions", sa.Column("raw_ensemble_probability", sa.Float(), nullable=True))
    op.add_column("predictions", sa.Column("learning_profile_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_predictions_learning_profile_id",
        "predictions",
        "market_learning_profiles",
        ["learning_profile_id"],
        ["id"],
    )
    op.create_index("ix_predictions_learning_profile_id", "predictions", ["learning_profile_id"])


def downgrade():
    op.drop_index("ix_predictions_learning_profile_id", table_name="predictions")
    op.drop_constraint("fk_predictions_learning_profile_id", "predictions", type_="foreignkey")
    op.drop_column("predictions", "learning_profile_id")
    op.drop_column("predictions", "raw_ensemble_probability")
    op.drop_table("model_learning_promotions")
    op.drop_table("market_learning_profiles")
    op.drop_table("model_learning_runs")
