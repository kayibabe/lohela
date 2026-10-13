"""persist immutable per-pick decision and quote provenance"""

import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "fc24e8a1b3d5"
down_revision = "fb13c7d9a2e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column("feature_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "predictions",
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_predictions_decision_at", "predictions", ["decision_at"])

    op.add_column("ticket_selections", sa.Column("source_odds_id_snapshot", sa.Integer(), nullable=True))
    op.add_column(
        "ticket_selections",
        sa.Column("source_bookmaker_snapshot", sa.String(length=100), nullable=True),
    )
    op.add_column("ticket_selections", sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "ticket_selections",
        sa.Column("decision_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "ticket_selections",
        sa.Column("decision_snapshot_hash", sa.String(length=64), nullable=True),
    )

    bind = op.get_bind()
    metadata = sa.MetaData()
    selections = sa.Table("ticket_selections", metadata, autoload_with=bind)
    tickets = sa.Table("accumulator_tickets", metadata, autoload_with=bind)
    predictions = sa.Table("predictions", metadata, autoload_with=bind)
    rows = bind.execute(
        sa.select(
            selections.c.id,
            selections.c.prediction_id,
            selections.c.market,
            selections.c.selection,
            selections.c.odds_snapshot,
            selections.c.probability_snapshot,
            selections.c.q_score_snapshot,
            selections.c.edge_snapshot,
            selections.c.source_odds_at,
            tickets.c.published_at,
            predictions.c.model_run_id,
            predictions.c.model_version,
            predictions.c.source_odds_id,
            predictions.c.source_decimal_odds,
            predictions.c.source_odds_provenance,
            predictions.c.feature_snapshot,
            predictions.c.decision_at.label("prediction_decision_at"),
        )
        .select_from(
            selections.join(tickets, selections.c.ticket_id == tickets.c.id)
            .outerjoin(predictions, selections.c.prediction_id == predictions.c.id)
        )
    ).mappings().all()
    for row in rows:
        provenance = row["source_odds_provenance"] or {}
        quote_id = row["source_odds_id"]
        bookmaker = provenance.get("bookmaker")
        decision_at = row["published_at"]
        snapshot = {
            "schema_version": "lohela.pick-decision.v1",
            "legacy_backfill": True,
            "decision_at": decision_at.isoformat() if decision_at else None,
            "prediction": {
                "id": row["prediction_id"],
                "model_run_id": row["model_run_id"],
                "model_version": row["model_version"],
                "feature_snapshot": row["feature_snapshot"] or {},
            },
            "market": {
                "market": row["market"],
                "selection": row["selection"],
                "selected_odds": row["odds_snapshot"],
                "selected_probability": row["probability_snapshot"],
                "q_score": row["q_score_snapshot"],
                "edge": row["edge_snapshot"],
            },
            "quote": {
                "id": quote_id,
                "bookmaker": bookmaker,
                "decimal_odds": row["source_decimal_odds"],
                "quoted_at": row["source_odds_at"].isoformat() if row["source_odds_at"] else None,
                "provenance": provenance,
            },
        }
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
        bind.execute(
            selections.update()
            .where(selections.c.id == row["id"])
            .values(
                source_odds_id_snapshot=quote_id,
                source_bookmaker_snapshot=bookmaker,
                decision_at=decision_at,
                decision_snapshot=snapshot,
                decision_snapshot_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            )
        )


def downgrade() -> None:
    op.drop_column("ticket_selections", "decision_snapshot_hash")
    op.drop_column("ticket_selections", "decision_snapshot")
    op.drop_column("ticket_selections", "decision_at")
    op.drop_column("ticket_selections", "source_bookmaker_snapshot")
    op.drop_column("ticket_selections", "source_odds_id_snapshot")
    op.drop_index("ix_predictions_decision_at", table_name="predictions")
    op.drop_column("predictions", "decision_at")
    op.drop_column("predictions", "feature_snapshot")
