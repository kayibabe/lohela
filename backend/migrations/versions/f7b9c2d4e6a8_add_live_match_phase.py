"""persist provider live phase and elapsed minutes"""

from alembic import op
import sqlalchemy as sa

revision = "f7b9c2d4e6a8"
down_revision = "c4f8a1d2e6b7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("matches", sa.Column("live_phase", sa.String(length=20), nullable=True))
    op.add_column("matches", sa.Column("elapsed_minutes", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("matches", "elapsed_minutes")
    op.drop_column("matches", "live_phase")
