# Singles overhaul baseline

Verified on 2026-09-19 at approximately 17:42 UTC (19:42 Africa/Blantyre).
Source: the existing local Lohela Docker PostgreSQL database. This is a local
snapshot, not independently verified production or bookmaker account performance.
No external credentials, database writes, scheduler changes, or promotions were used.
SQL transactions explicitly used READ ONLY and ended with ROLLBACK.

## Observed baseline

All 896 prediction rows were model 0.2.1, inserted before their fixture kickoff;
none had historical replay `as_of_at` timestamps. They represent only 42 unique
fixtures across September 14, 15, and 17, 2026. Reruns and multiple markets explain
the larger row count; 896 rows are not 896 independent trials.

| Cohort | Wins | Losses | Hit rate | Flat-stake paper ROI |
|---|---:|---:|---:|---:|
| Latest daily tickets, existing performance service | 0 | 8 | 0% | -100% |
| Unique published picks, existing singles service | 11 | 17 | 39.29% | -34.14% |
| Eligible all-market prices strictly above 1.50 | 164 | 258 | 38.86% | -5.50% |
| Same prices, model probability at least 70% | 8 | 15 | 34.78% | -40.91% |

The ticket count includes best-value tickets; six were official balanced/aggressive
cohorts. Published-pick performance deduplicates repeated ticket selections using
the existing service's earliest-published convention. The published-pick figures
span all odds, not only odds above 1.50. These are paper/what-if calculations, not
verified cash wagers. All-market rows include opposing and overlapping outcomes;
their aggregate ROI is diagnostic and does not describe a deployable strategy.

## Reproducible read-only methodology

1. Count prediction rows joined to matches, compare `created_at < kickoff_at`,
   count non-null `as_of_at`, group by model version and fixture local date.
2. Call existing `performance_summary` and `individual_selection_summary` inside
   the read-only SQLAlchemy transaction; retain their existing deduplication rules.
3. Load predictions ordered by `created_at DESC, id DESC`, with their matches.
   Require `as_of_at IS NULL`, finite quoted odds strictly greater than 1.50,
   `source_odds_at <= created_at < kickoff_at`, and therefore a quote strictly
   before kickoff. Keep the first eligible row per `(match_id, market, selection)`.
4. Require finished fixtures with both final scores. Evaluate using the existing
   `evaluate_selection` settlement function; exclude voids from the win/loss cohort.
   All observed eligible quoted odds were ordinary finite prices.
5. Apply one unit per non-void observation; profit is odds minus one for a win and
   minus one for a loss. ROI is total profit divided by non-void observations.
6. Apply `model_probability >= 0.70` as an exploratory diagnostic filter only.

The all-market cohort contains 422 non-void outcomes across 42 fixtures/3 days and
loses 23.20 units. The 70%-prediction subset contains 23 outcomes across 21
fixtures/3 days and loses 9.41 units. These are selected after seeing the archive;
neither subset is a frozen prospective singles policy. No threshold was promoted.

## Audit findings and limits

- Model confidence was badly miscalibrated on this small observed sample. Raising
  the confidence threshold alone is not a repair.
- Existing learning gates previously allowed a losing challenger to pass simply
  by being less bad than a losing incumbent. Positive ROI and a positive lower
  95% ROI bound are now additional necessary checks, rechecked at promotion.
- Existing learning quote eligibility only compared the price to kickoff; a price
  after the prediction decision was not rejected. Learning now requires quote time
  no later than the information timestamp and strictly before kickoff.
- `backtesting._walk_forward` partitions already-computed records; it does not
  retrain each fold. Its individual-row bootstrap ignores fixture/day dependence.
- Model preparation calls an Elo replay over every finished fixture; the main
  model runner's historical loader lacks a target-date cutoff. Timestamp eligibility
  alone cannot prove historical feature availability. Preserve retrospective replay
  separation and add explicit training/feature cutoffs before relying on replays.
- Published singles and all-market summaries use different version-selection rules.
  A future frozen one-pick-per-fixture ledger should be the primary prospective
  evidence source, with rejected/no-bet decisions preserved.
- Only three observed days are available. Neither profitability nor a 70% hit rate
  is established. Current confidence intervals are not dependence-adjusted evidence
  and cannot substitute for a larger untouched chronological evaluation.

Next evidence gate: freeze the singles policy, collect real pre-match selections
and quote provenance, allow no-bet days, settle from authoritative results, and
assess fixed-unit profit, losses, coverage, calibration, and uncertainty on later
untouched days before any operational promotion.
