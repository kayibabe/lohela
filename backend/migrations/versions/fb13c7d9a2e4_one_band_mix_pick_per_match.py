"""keep the first daily band-mix pick for each match

Revision ID: fb13c7d9a2e4
Revises: fa02a7c4d1e8
"""

from alembic import op
import sqlalchemy as sa


revision = "fb13c7d9a2e4"
down_revision = "fa02a7c4d1e8"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "band_mix_daily_picks" not in inspector.get_table_names():
        return

    # Preserve the earliest captured snapshot and its settlement record.
    bind.execute(sa.text("""
        DELETE FROM band_mix_daily_picks
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY target_date, match_id
                           ORDER BY captured_at ASC, id ASC
                       ) AS row_number
                FROM band_mix_daily_picks
            ) ranked
            WHERE row_number > 1
        )
    """))
    constraints = {item["name"] for item in inspector.get_unique_constraints("band_mix_daily_picks")}
    if "uq_band_mix_daily_pick_match" not in constraints:
        op.create_unique_constraint(
            "uq_band_mix_daily_pick_match",
            "band_mix_daily_picks",
            ["target_date", "match_id"],
        )


def downgrade():
    op.drop_constraint(
        "uq_band_mix_daily_pick_match",
        "band_mix_daily_picks",
        type_="unique",
    )
