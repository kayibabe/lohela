"""merge the existing migration branches before future deploys"""

from alembic import op


revision = "b1d7f3a9c5e2"
down_revision = ("a8c4e6f2b1d0", "f5b9d3e7a1c4")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
