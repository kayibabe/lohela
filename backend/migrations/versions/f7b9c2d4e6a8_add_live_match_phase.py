"""persist provider live phase and elapsed minutes"""

from alembic import op
import sqlalchemy as sa

revision = "f7b9c2d4e6a8"
down_revision = "c4f8a1d2e6b7"
branch_labels = None
depends_on = None


def upgrade():
    # Guarded like the other post-baseline migrations: the historical baseline
    # migration (3d52766f0fb9) dynamically creates every table from the
    # *current* ORM models, so a fresh database already has these columns.
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("matches")}
    if "live_phase" not in existing:
        op.add_column("matches", sa.Column("live_phase", sa.String(length=20), nullable=True))
    if "elapsed_minutes" not in existing:
        op.add_column("matches", sa.Column("elapsed_minutes", sa.Integer(), nullable=True))


def downgrade():
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("matches")}
    if "elapsed_minutes" in existing:
        op.drop_column("matches", "elapsed_minutes")
    if "live_phase" in existing:
        op.drop_column("matches", "live_phase")
