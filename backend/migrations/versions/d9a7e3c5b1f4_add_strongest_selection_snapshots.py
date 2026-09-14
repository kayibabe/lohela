"""add immutable strongest-selection snapshots

Revision ID: d9a7e3c5b1f4
Revises: b6c1d8e4f902
"""

from alembic import op
import sqlalchemy as sa


revision = "d9a7e3c5b1f4"
down_revision = "b6c1d8e4f902"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "strongest_selection_snapshots" not in inspector.get_table_names():
        op.create_table(
            "strongest_selection_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("target_date", sa.Date(), nullable=False),
            sa.Column("model_run_id", sa.Integer(), nullable=False),
            sa.Column("prediction_id", sa.Integer(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column(
                "capture_source",
                sa.String(length=40),
                nullable=False,
                server_default="model_run_completion",
            ),
            sa.Column(
                "captured_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["model_run_id"], ["model_runs.id"]),
            sa.ForeignKeyConstraint(["prediction_id"], ["predictions.id"]),
            sa.UniqueConstraint(
                "model_run_id", "prediction_id", name="uq_strongest_snapshot_prediction"
            ),
            sa.UniqueConstraint(
                "model_run_id", "rank", name="uq_strongest_snapshot_rank"
            ),
        )
        op.create_index(
            "ix_strongest_selection_snapshots_target_date",
            "strongest_selection_snapshots",
            ["target_date"],
        )
        op.create_index(
            "ix_strongest_selection_snapshots_model_run_id",
            "strongest_selection_snapshots",
            ["model_run_id"],
        )
        op.create_index(
            "ix_strongest_selection_snapshots_prediction_id",
            "strongest_selection_snapshots",
            ["prediction_id"],
        )

    # Existing predictions are append-only, so their historical top-eight
    # membership can be reconstructed once. The provenance label keeps that
    # backfill distinguishable from snapshots captured at model-run completion.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    mr.target_date,
                    p.model_run_id,
                    p.id AS prediction_id,
                    row_number() OVER (
                        PARTITION BY p.model_run_id
                        ORDER BY p.q_score DESC, p.id DESC
                    ) AS pick_rank,
                    COALESCE(mr.completed_at, now()) AS captured_at
                FROM predictions p
                JOIN model_runs mr ON mr.id = p.model_run_id
                JOIN matches m ON m.id = p.match_id
                WHERE mr.status = 'COMPLETED'
                  AND p.q_score >= 85
                  AND m.excluded_from_models = false
            )
            INSERT INTO strongest_selection_snapshots (
                target_date,
                model_run_id,
                prediction_id,
                rank,
                capture_source,
                captured_at
            )
            SELECT
                target_date,
                model_run_id,
                prediction_id,
                pick_rank,
                'historical_backfill',
                captured_at
            FROM ranked
            WHERE pick_rank <= 8
            ON CONFLICT DO NOTHING
            """
        )
    )


def downgrade():
    op.drop_index(
        "ix_strongest_selection_snapshots_prediction_id",
        table_name="strongest_selection_snapshots",
    )
    op.drop_index(
        "ix_strongest_selection_snapshots_model_run_id",
        table_name="strongest_selection_snapshots",
    )
    op.drop_index(
        "ix_strongest_selection_snapshots_target_date",
        table_name="strongest_selection_snapshots",
    )
    op.drop_table("strongest_selection_snapshots")
