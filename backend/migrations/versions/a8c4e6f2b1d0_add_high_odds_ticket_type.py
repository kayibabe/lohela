"""add the public high-odds ticket tier"""

from alembic import op


revision = "a8c4e6f2b1d0"
down_revision = "f7b9c2d4e6a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLAlchemy's PostgreSQL enum stores Python enum member names.
    op.execute("ALTER TYPE tickettype ADD VALUE IF NOT EXISTS 'HIGH_ODDS' AFTER 'BALANCED'")


def downgrade() -> None:
    # PostgreSQL cannot remove an enum label safely while rows may reference it.
    # Keep the label during downgrade; the application code can no longer
    # create new high-odds tickets once the code is rolled back.
    pass
