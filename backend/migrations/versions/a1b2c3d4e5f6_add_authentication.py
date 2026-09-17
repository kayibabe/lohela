"""add users, browser sessions, and authentication audit events

Revision ID: a1b2c3d4e5f6
Revises: f3a8c1e5d9b2
"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "f3a8c1e5d9b2"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("password_hash", sa.String(512), nullable=False),
            sa.Column("role", sa.String(32), nullable=False, server_default="user"),
            sa.Column("plan", sa.String(32), nullable=False, server_default="free"),
            sa.Column("account_status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("email", name="uq_users_email"),
        )
        op.create_index("ix_users_email", "users", ["email"], unique=True)
    if "user_sessions" not in tables:
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
        )
        op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
        op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"], unique=True)
        op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    if "auth_events" not in tables:
        op.create_table(
            "auth_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("email_attempted", sa.String(320), nullable=True),
            sa.Column("event_type", sa.String(32), nullable=False),
            sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("failure_reason", sa.String(128), nullable=True),
            sa.Column("ip_address", sa.String(64), nullable=True),
            sa.Column("user_agent", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_auth_events_user_id", "auth_events", ["user_id"])
        op.create_index("ix_auth_events_email_attempted", "auth_events", ["email_attempted"])
        op.create_index("ix_auth_events_created_at", "auth_events", ["created_at"])


def downgrade():
    op.drop_table("auth_events")
    op.drop_table("user_sessions")
    op.drop_table("users")
