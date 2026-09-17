"""add usernames to users"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "username" in columns:
        return
    op.add_column("users", sa.Column("username", sa.String(80), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, email FROM users ORDER BY id")).mappings().all()
    used = set()
    for row in rows:
        base = (row["email"].split("@", 1)[0] or "user")[:70]
        candidate = base
        suffix = 1
        while candidate.lower() in used:
            suffix += 1
            candidate = f"{base[:70 - len(str(suffix)) - 1]}_{suffix}"
        used.add(candidate.lower())
        bind.execute(sa.text("UPDATE users SET username = :username WHERE id = :id"), {"username": candidate, "id": row["id"]})
    op.alter_column("users", "username", existing_type=sa.String(80), nullable=False)
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade():
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
