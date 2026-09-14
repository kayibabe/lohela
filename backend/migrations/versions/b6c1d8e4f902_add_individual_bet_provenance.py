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
    op.add_column("bets", sa.Column("source_selection_id", sa.Integer(), nullable=True))
    op.add_column("bets", sa.Column("match_id", sa.Integer(), nullable=True))
    op.add_column("bets", sa.Column("market", sa.String(length=64), nullable=True))
    op.add_column("bets", sa.Column("selection", sa.String(length=128), nullable=True))
    op.create_index("ix_bets_source_selection_id", "bets", ["source_selection_id"])
    op.create_index("ix_bets_match_id", "bets", ["match_id"])
    op.create_index("ix_bets_market", "bets", ["market"])
    op.create_foreign_key("fk_bets_source_selection", "bets", "ticket_selections", ["source_selection_id"], ["id"])
    op.create_foreign_key("fk_bets_match", "bets", "matches", ["match_id"], ["id"])

def downgrade():
    op.drop_constraint("fk_bets_match", "bets", type_="foreignkey")
    op.drop_constraint("fk_bets_source_selection", "bets", type_="foreignkey")
    op.drop_index("ix_bets_market", table_name="bets")
    op.drop_index("ix_bets_match_id", table_name="bets")
    op.drop_index("ix_bets_source_selection_id", table_name="bets")
    for column in ("selection", "market", "match_id", "source_selection_id"):
        op.drop_column("bets", column)
