# Lohela pick decision provenance

## Contract

Each new model prediction records the exact model inputs and context used at
scoring time in `predictions.feature_snapshot`, with `predictions.decision_at`
as its scoring timestamp. The snapshot includes per-model probabilities,
Q-score inputs, relevant fixture and team context, model lineage, calibrator
configuration, and the source quote reference.

When a prediction enters a published ticket, `ticket_selections` stores a
separate immutable pick-decision snapshot. It records the publication decision
time, prediction and model-run IDs, model version, feature snapshot, market and
selection, ticket pricing basis, source quote ID, bookmaker, odds, and quote
timestamp. A canonical SHA-256 hash is stored for each pick, and the enclosing
ticket publication hash covers the same snapshots.

Publication fails closed when a candidate has no quote ID, bookmaker, feature
snapshot, or strictly pre-decision and pre-kickoff quote timestamp. The quote
ID and bookmaker are copied into the selection row so later odds-table cleanup
does not erase their provenance. Prediction scoring time and ticket decision
time remain distinct: a forecast may be created earlier, while the published
selection is decided when the immutable ticket is issued.

## Historical rows

The migration backfills existing selections with their linked prediction,
quote metadata when available, and ticket publication time. Backfilled
snapshots are marked `legacy_backfill`; they do not claim to reconstruct
features that were never stored. An empty legacy feature snapshot is an
evidence gap, not a recovered input record.

Closing-price capture and authoritative settlement remain separate evidence
events. Neither is substituted for the entry quote or pick decision timestamp.
