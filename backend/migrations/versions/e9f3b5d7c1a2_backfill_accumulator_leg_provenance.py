"""backfill provenance for existing generated accumulator legs"""

from alembic import op
import sqlalchemy as sa


revision = "e9f3b5d7c1a2"
down_revision = "e8f2a4c6b0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing generated rows already retain their source ticket IDs in
    # settlement_details. Resolve each immutable leg against those tickets so
    # the provenance UI is complete after upgrading an existing deployment.
    op.execute(sa.text("""
        UPDATE custom_accumulator_legs AS leg
        SET source_ticket_id = source_ticket.id,
            source_ticket_type = lower(source_ticket.ticket_type::text),
            source_ticket_version = source_ticket.version,
            source_conflict = EXISTS (
                SELECT 1
                FROM ticket_selections AS other_selection
                JOIN accumulator_tickets AS other_ticket
                  ON other_ticket.id = other_selection.ticket_id
                WHERE other_ticket.id IN (
                    (accumulator.settlement_details -> 'source_ticket_ids' ->> 'safe')::integer,
                    (accumulator.settlement_details -> 'source_ticket_ids' ->> 'balanced')::integer
                )
                  AND other_ticket.id <> source_ticket.id
                  AND other_selection.match_id = leg.match_id
            )
        FROM custom_accumulators AS accumulator,
             ticket_selections AS source_selection,
             accumulator_tickets AS source_ticket
        WHERE leg.accumulator_id = accumulator.id
          AND source_selection.prediction_id = leg.prediction_id
          AND source_selection.match_id = leg.match_id
          AND source_ticket.id = source_selection.ticket_id
          AND accumulator.settlement_details ->> 'source' = 'daily_ticket_merge'
          AND source_ticket.id IN (
              (accumulator.settlement_details -> 'source_ticket_ids' ->> 'safe')::integer,
              (accumulator.settlement_details -> 'source_ticket_ids' ->> 'balanced')::integer
          )
    """))


def downgrade() -> None:
    pass
