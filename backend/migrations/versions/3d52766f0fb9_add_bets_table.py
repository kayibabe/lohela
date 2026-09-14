"""create the initial Lohela schema

Revision ID: 3d52766f0fb9
Revises: 
Create Date: 2026-08-11 18:24:01.509660

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3d52766f0fb9'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This is the initial migration. The project had no prior baseline
    # migration, so create every registered model table for fresh databases.
    import app.models  # noqa: F401
    from app.database import Base

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    import app.models  # noqa: F401
    from app.database import Base

    Base.metadata.drop_all(bind=op.get_bind())
