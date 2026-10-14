"""add account-owned match favorites"""

from alembic import op
import sqlalchemy as sa


revision = "fd35a7c2b901"
down_revision = "fc24e8a1b3d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_match_favorites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "match_id", name="uq_user_match_favorite"),
    )
    op.create_table(
        "user_match_filter_presets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_match_filter_presets_user_id", "user_match_filter_presets", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_match_filter_presets_user_id", table_name="user_match_filter_presets")
    op.drop_table("user_match_filter_presets")
    op.drop_table("user_match_favorites")
