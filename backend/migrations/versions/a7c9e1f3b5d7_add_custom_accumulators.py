"""persist user-built accumulators and immutable legs

Revision ID: a7c9e1f3b5d7
Revises: e1f2a3b4c5d6
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a7c9e1f3b5d7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    status_enum = postgresql.ENUM(
        "DRAFT", "PLACED", "WON", "LOST", "VOID", "CASHOUT",
        name="customaccumulatorstatus",
        create_type=False,
    )
    status_enum.create(op.get_bind(), checkfirst=True)

    # Guarded: the historical baseline migration (3d52766f0fb9) dynamically
    # creates every table from the *current* ORM models, so a fresh database
    # already has these tables/indexes.
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())

    if "custom_accumulators" not in existing_tables:
        op.create_table(
            "custom_accumulators",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("target_date", sa.Date(), nullable=False),
            sa.Column("status", status_enum, nullable=False),
            sa.Column("stake", sa.Float(), nullable=True),
            sa.Column("combined_odds", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("potential_return", sa.Float(), nullable=True),
            sa.Column("actual_return", sa.Float(), nullable=True),
            sa.Column("settlement_details", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_custom_accumulators_target_date", "custom_accumulators", ["target_date"])
        op.create_index("ix_custom_accumulators_status", "custom_accumulators", ["status"])

    if "custom_accumulator_legs" not in existing_tables:
        op.create_table(
            "custom_accumulator_legs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("accumulator_id", sa.Integer(), nullable=False),
            sa.Column("prediction_id", sa.Integer(), nullable=False),
            sa.Column("match_id", sa.Integer(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("home_team", sa.String(length=160), nullable=False),
            sa.Column("away_team", sa.String(length=160), nullable=False),
            sa.Column("competition", sa.String(length=160), nullable=False),
            sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("market", sa.String(length=100), nullable=False),
            sa.Column("selection", sa.String(length=100), nullable=False),
            sa.Column("odds_snapshot", sa.Float(), nullable=False),
            sa.Column("probability_snapshot", sa.Float(), nullable=True),
            sa.Column("q_score_snapshot", sa.Float(), nullable=True),
            sa.Column("edge_snapshot", sa.Float(), nullable=True),
            sa.Column("result", postgresql.ENUM(name="selectionresult", create_type=False), nullable=False),
            sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["accumulator_id"], ["custom_accumulators.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["prediction_id"], ["predictions.id"]),
            sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
            sa.UniqueConstraint("accumulator_id", "position", name="uq_custom_accumulator_leg_position"),
            sa.UniqueConstraint("accumulator_id", "match_id", name="uq_custom_accumulator_one_leg_per_match"),
        )
        op.create_index("ix_custom_accumulator_legs_accumulator_id", "custom_accumulator_legs", ["accumulator_id"])
        op.create_index("ix_custom_accumulator_legs_match_id", "custom_accumulator_legs", ["match_id"])

    if "automation_alerts" not in existing_tables:
        op.create_table(
            "automation_alerts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("dedupe_key", sa.String(length=240), nullable=False, unique=True),
            sa.Column("severity", sa.String(length=20), nullable=False),
            sa.Column("task_name", sa.String(length=120), nullable=False),
            sa.Column("target_date", sa.Date(), nullable=True),
            sa.Column("title", sa.String(length=240), nullable=False),
            sa.Column("detail", sa.Text(), nullable=False),
            sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_automation_alerts_dedupe_key", "automation_alerts", ["dedupe_key"], unique=True)
        op.create_index("ix_automation_alerts_target_date", "automation_alerts", ["target_date"])
        op.create_index("ix_automation_alerts_resolved", "automation_alerts", ["resolved"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())

    if "automation_alerts" in existing_tables:
        op.drop_index("ix_automation_alerts_resolved", table_name="automation_alerts")
        op.drop_index("ix_automation_alerts_target_date", table_name="automation_alerts")
        op.drop_index("ix_automation_alerts_dedupe_key", table_name="automation_alerts")
        op.drop_table("automation_alerts")
    if "custom_accumulator_legs" in existing_tables:
        op.drop_index("ix_custom_accumulator_legs_match_id", table_name="custom_accumulator_legs")
        op.drop_index("ix_custom_accumulator_legs_accumulator_id", table_name="custom_accumulator_legs")
        op.drop_table("custom_accumulator_legs")
    if "custom_accumulators" in existing_tables:
        op.drop_index("ix_custom_accumulators_status", table_name="custom_accumulators")
        op.drop_index("ix_custom_accumulators_target_date", table_name="custom_accumulators")
        op.drop_table("custom_accumulators")
    postgresql.ENUM(name="customaccumulatorstatus").drop(op.get_bind(), checkfirst=True)
