"""add immutable research ledger and prediction provenance

Revision ID: 8a4d2f9c1b7e
Revises: 3d52766f0fb9
Create Date: 2026-08-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8a4d2f9c1b7e"
down_revision: Union[str, None] = "3d52766f0fb9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RESEARCH_TABLES = [
    "model_runs",
    "ticket_generations",
    "accumulator_tickets",
    "ticket_selections",
    "ticket_results",
    "audit_events",
    "model_performance",
    "league_performance",
    "correlation_coefficients",
    "pipeline_runs",
    "pipeline_stage_runs",
    "backtest_runs",
]


def _prediction_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "predictions" not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns("predictions")}


def upgrade() -> None:
    # create_all(checkfirst=True) makes this migration safe for both existing
    # installations and fresh databases whose historical baseline migration
    # dynamically registered all current ORM models.
    import app.models  # noqa: F401
    from app.database import Base

    tables = [Base.metadata.tables[name] for name in _RESEARCH_TABLES]
    Base.metadata.create_all(bind=op.get_bind(), tables=tables, checkfirst=True)

    existing = _prediction_columns()
    additions = {
        "model_run_id": sa.Column("model_run_id", sa.Integer(), sa.ForeignKey("model_runs.id"), nullable=True),
        "q_component_weights": sa.Column(
            "q_component_weights", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        "q_component_status": sa.Column(
            "q_component_status", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        "active_models": sa.Column(
            "active_models", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        "data_quality_snapshot": sa.Column(
            "data_quality_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        "source_odds_id": sa.Column(
            "source_odds_id",
            sa.Integer(),
            sa.ForeignKey("odds.id", ondelete="SET NULL"),
            nullable=True,
        ),
        "source_odds_at": sa.Column("source_odds_at", sa.DateTime(timezone=True), nullable=True),
        "source_decimal_odds": sa.Column("source_decimal_odds", sa.Float(), nullable=True),
        "source_implied_probability": sa.Column("source_implied_probability", sa.Float(), nullable=True),
        "source_odds_provenance": sa.Column(
            "source_odds_provenance", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
    }
    for name, column in additions.items():
        if name not in existing:
            op.add_column("predictions", column)

    odds_existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("odds")
    }
    if "source_type" not in odds_existing:
        op.add_column(
            "odds",
            sa.Column("source_type", sa.String(length=40), nullable=False, server_default="legacy"),
        )
    if "is_fallback" not in odds_existing:
        op.add_column(
            "odds",
            sa.Column("is_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("predictions")}
    if "ix_predictions_model_run_id" not in indexes:
        op.create_index("ix_predictions_model_run_id", "predictions", ["model_run_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "odds" in inspector.get_table_names():
        odds_existing = {column["name"] for column in inspector.get_columns("odds")}
        if "is_fallback" in odds_existing:
            op.drop_column("odds", "is_fallback")
        if "source_type" in odds_existing:
            op.drop_column("odds", "source_type")
    if "predictions" in inspector.get_table_names():
        indexes = {index["name"] for index in inspector.get_indexes("predictions")}
        if "ix_predictions_model_run_id" in indexes:
            op.drop_index("ix_predictions_model_run_id", table_name="predictions")
        existing = _prediction_columns()
        for name in (
            "source_odds_provenance",
            "source_implied_probability",
            "source_decimal_odds",
            "source_odds_at",
            "source_odds_id",
            "data_quality_snapshot",
            "active_models",
            "q_component_status",
            "q_component_weights",
            "model_run_id",
        ):
            if name in existing:
                op.drop_column("predictions", name)

    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())
    for name in reversed(_RESEARCH_TABLES):
        if name in existing_tables:
            # Alembic's table operation avoids SQLAlchemy metadata-level enum
            # hooks, which would otherwise try to drop unrelated legacy enums.
            op.drop_table(name)

    if op.get_bind().dialect.name == "postgresql":
        for enum_name in ("runstatus", "tickettype", "ticketstatus", "selectionresult"):
            op.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))
