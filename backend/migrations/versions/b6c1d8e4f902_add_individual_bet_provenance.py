"""add provenance fields for confirmed individual bets

Revision ID: b6c1d8e4f902
Revises: f7b9c2d4e6a8
"""
from alembic import op
import sqlalchemy as sa

revision = "b6c1d8e4f902"
down_revision = "f7b9c2d4e6a8"
branch_labels = None
depends_on = None

def upgrade():
    # Guarded: the historical baseline migration (3d52766f0fb9) dynamically
    # creates every table from the *current* ORM models, so a fresh database
    # already has these columns/indexes/constraints.
    inspector = sa.inspect(op.get_bind())
    existing_columns = {c["name"] for c in inspector.get_columns("bets")}
    if "source_selection_id" not in existing_columns:
        op.add_column("bets", sa.Column("source_selection_id", sa.Integer(), nullable=True))
    if "match_id" not in existing_columns:
        op.add_column("bets", sa.Column("match_id", sa.Integer(), nullable=True))
    if "market" not in existing_columns:
        op.add_column("bets", sa.Column("market", sa.String(length=64), nullable=True))
    if "selection" not in existing_columns:
        op.add_column("bets", sa.Column("selection", sa.String(length=128), nullable=True))

    existing_indexes = {i["name"] for i in inspector.get_indexes("bets")}
    if "ix_bets_source_selection_id" not in existing_indexes:
        op.create_index("ix_bets_source_selection_id", "bets", ["source_selection_id"])
    if "ix_bets_match_id" not in existing_indexes:
        op.create_index("ix_bets_match_id", "bets", ["match_id"])
    if "ix_bets_market" not in existing_indexes:
        op.create_index("ix_bets_market", "bets", ["market"])

    existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("bets")}
    if "fk_bets_source_selection" not in existing_fks:
        op.create_foreign_key(
            "fk_bets_source_selection", "bets", "ticket_selections", ["source_selection_id"], ["id"]
        )
    if "fk_bets_match" not in existing_fks:
        op.create_foreign_key("fk_bets_match", "bets", "matches", ["match_id"], ["id"])

def downgrade():
    inspector = sa.inspect(op.get_bind())
    existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("bets")}
    if "fk_bets_match" in existing_fks:
        op.drop_constraint("fk_bets_match", "bets", type_="foreignkey")
    if "fk_bets_source_selection" in existing_fks:
        op.drop_constraint("fk_bets_source_selection", "bets", type_="foreignkey")

    existing_indexes = {i["name"] for i in inspector.get_indexes("bets")}
    if "ix_bets_market" in existing_indexes:
        op.drop_index("ix_bets_market", table_name="bets")
    if "ix_bets_match_id" in existing_indexes:
        op.drop_index("ix_bets_match_id", table_name="bets")
    if "ix_bets_source_selection_id" in existing_indexes:
        op.drop_index("ix_bets_source_selection_id", table_name="bets")

    existing_columns = {c["name"] for c in inspector.get_columns("bets")}
    for column in ("selection", "market", "match_id", "source_selection_id"):
        if column in existing_columns:
            op.drop_column("bets", column)
