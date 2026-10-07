"""persist immutable bet watch pass evidence on predictions"""

from alembic import op
import sqlalchemy as sa

revision = "f8c0d3e7a1b5"
down_revision = "f7b9c2d4e6a8"
branch_labels = None
depends_on = None


def upgrade():
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("predictions")}
    if "recommendation_status" not in existing:
        op.add_column("predictions", sa.Column("recommendation_status", sa.String(length=10), nullable=True))
    if "recommendation_reasons" not in existing:
        op.add_column("predictions", sa.Column("recommendation_reasons", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    if "recommendation_risks" not in existing:
        op.add_column("predictions", sa.Column("recommendation_risks", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    if "recommendation_policy_version" not in existing:
        op.add_column("predictions", sa.Column("recommendation_policy_version", sa.String(length=40), nullable=True))


def downgrade():
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("predictions")}
    for name in ("recommendation_policy_version", "recommendation_risks", "recommendation_reasons", "recommendation_status"):
        if name in existing:
            op.drop_column("predictions", name)
