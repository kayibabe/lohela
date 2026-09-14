"""add immutable odds snapshots

Revision ID: c4f8a1d2e6b7
Revises: 8a4d2f9c1b7e
"""
from alembic import op
import sqlalchemy as sa

revision = "c4f8a1d2e6b7"
down_revision = "8a4d2f9c1b7e"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS odds_snapshots (
        id SERIAL PRIMARY KEY, match_id INTEGER NOT NULL REFERENCES matches(id),
        bookmaker VARCHAR(100) NOT NULL, market VARCHAR(100) NOT NULL,
        selection VARCHAR(100) NOT NULL, decimal_odds FLOAT NOT NULL,
        implied_probability FLOAT NOT NULL, captured_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_odds_snapshots_match_id ON odds_snapshots (match_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_odds_snapshots_captured_at ON odds_snapshots (captured_at)")

def downgrade():
    op.drop_table("odds_snapshots")
