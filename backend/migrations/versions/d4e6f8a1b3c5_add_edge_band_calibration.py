"""add edge_band_calibration table

Revision ID: d4e6f8a1b3c5
Revises: c3f8a1d9e2b7
Create Date: 2026-09-20
"""

from typing import Sequence, Union

from alembic import op


revision: str = "d4e6f8a1b3c5"
down_revision: Union[str, None] = "c3f8a1d9e2b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    import app.models  # noqa: F401
    from app.database import Base

    table = Base.metadata.tables["edge_band_calibration"]
    Base.metadata.create_all(bind=op.get_bind(), tables=[table], checkfirst=True)


def downgrade() -> None:
    op.drop_table("edge_band_calibration")
