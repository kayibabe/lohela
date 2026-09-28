# Aggressive ticket tier retired (2026-09-28)

## Decision

The Aggressive accumulator tier is removed from the system. The builder no
longer selects matches for it, and its stored history is purged so no
performance, calibration, ledger or loss analysis includes it.

The daily public portfolio is now **Conservative + Balanced**. Best Value stays
internal research only.

## What changed

- `accumulator_builder.py`: Aggressive specs removed (model and market pricing).
  `PUBLIC_TICKET_TYPES = (SAFE, BALANCED)` is the single source of truth for
  public tiers. Publisher, API, pipeline alerts and the relaxation fallback all
  use it.
- `settings.min_daily_public_tickets`: 3 → **2**. The relaxation ladder and the
  rolling-horizon fallback now aim to fill both public tiers.
- `high_risk_label` removed (only Aggressive tickets set it). The API field, the
  "High Risk / Low Hit Rate" badge and the column are gone.
- API: `GET /tickets/daily` no longer returns an `aggressive` key.
- Frontend: the Aggressive card, ledger filter, tracker option and CSS tokens
  are removed.

## Data migration `f2b4d6e8a0c3` (irreversible)

`alembic upgrade head` does the following:

1. Deletes every Aggressive ticket along with its `ticket_selections`,
   `ticket_results` and matching `audit_events`. Any `bets` row linked to a
   deleted leg keeps its denormalised match, market and selection, and its
   `source_selection_id` is set to NULL. Real-money journal entries are never
   deleted.
2. Removes `aggressive` from `ticket_generations.config_snapshot`
   (specs, diagnostics, publication summary), from generation audit payloads
   and from pipeline stage details. It then recomputes each generation's
   `output_count` and COMPLETED/PARTIAL status against the two public tiers.
3. Drops frozen `performance_summary` snapshots from
   `performance_aggregation_and_calibration` stage details that mixed in
   Aggressive totals. The prediction-based calibration snapshot in those
   details stays. Live performance endpoints recompute from the purged ledger.
4. Rewrites the `min_daily_tickets:*` alerts to use the 2-tier floor and
   resolves any alert that fired only because Aggressive was missing.
5. Drops `accumulator_tickets.high_risk_label` and removes `AGGRESSIVE` from the
   Postgres `tickettype` enum.

`downgrade` restores the schema only (the enum value and the column). It does
not bring back the purged rows. **Take a database backup before deploying**
(see `docs/BACKUP_RESTORE_RUNBOOK.md`).

Local dev DB result: 11 Aggressive tickets purged (55 legs, 21 audit events),
and afterwards no `aggressive` string remains in any ticket, audit, pipeline or
alert JSON.

## Not changed

Dated reports under `docs/` and the frozen evidence archive
`docs/evidence/pre_retraining_2026-09-20/` are historical records and still
mention Aggressive. `scripts/ablation_diagnostic.py` now filters Aggressive
tickets out when it regenerates results from that archive.
