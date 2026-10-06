# Prospective Ablation / Calibration Validation — 2026-10-06

Status: research-only. No production selector weights, gates, ticket generation,
database state, or deployment were changed.

## Evidence capture

- Source: read-only repeatable-read export from Railway production PostgreSQL,
  executed in the worker service environment.
- Latest runner capture: `2026-10-06T18:19:53.013604+00:00`.
- Latest evidence SHA-256: `1771f043af6a185a240a60d18d2261b3a0abc14e96d9e2fefc306c3dc8163828`.
- Fit-phase boundary: `2026-09-20T05:33:30.491115+00:00`.
- The live fit status check reports 18,944 post-boundary predictions, 5,849
  with `model_probability >= 0.65`, 17 distinct data days, and 16.53 elapsed
  days. All frozen fit targets (300 total, 60 tail, 15 days) pass.

## Frozen forward windows

| Window | UTC interval | Predictions | Tail >= 0.65 | Data days | Decision |
|---|---|---:|---:|---:|---|
| Fit | 2026-09-20 05:33 to 2026-10-05 05:33 | 15,408 | 4,764 | 16 | Complete |
| Calibrate / validate | 2026-10-05 05:33 to 2026-10-15 05:33 | 3,536 | 1,085 | 2 | In progress |
| Prospective test | 2026-10-15 05:33 to 2026-11-05 05:33 | 0 | 0 | 0 | Not started |

The calibration window is deliberately not treated as complete merely because
it has started. The latest export contains only 48 settled, deduplicated labels
from one calibration day, so the prospective test remains locked until the
full disjoint calibration/validation period has elapsed.

## Fit-only Platt diagnostic

The reproducible runner fits the protocol's tail calibrator against the
completed Fit window only, using strict pre-kickoff, latest-per-match/market
predictions with finished-match labels and `model_probability >= 0.65`:

- Labeled Fit rows: 5,472; tail rows: 1,696.
- Positive / negative tail labels: 1,163 / 533.
- Platt coefficients: `a = 0.5032906729`, `b = 0.0602439613`.
- Raw in-sample tail Brier: `0.22212`.
- Platt in-sample tail Brier: `0.21020`.

The Brier decrease is **Inferred / in-sample diagnostic only**, not validation
evidence and not a promotion recommendation. It must be evaluated on the
disjoint 10-day calibration/validation window, then on the untouched 21-day
prospective test window.

## Current out-of-sample checkpoint

The runner found 48 settled rows in the calibration/validation window. They are
reported as `PARTIAL_NOT_DECISION_BEARING` because the window has only one day
of settled data and ends on `2026-10-15T05:33:30.491115+00:00`.

- Raw partial-window gap: `+0.02433`; Brier: `0.21358`; ECE: `0.11139`.
- Platt partial-window gap: `+0.03769`; Brier: `0.22158`; ECE: `0.09352`.
- These values are **Verified as partial diagnostics**, but **Untested as
  decision evidence** until the full calibration window is complete.

The fail-closed review gate currently returns `INCOMPLETE`: Fit targets pass and
the partial Platt gap is below 0.15, but calibration is not complete and no test
gap exists. A passing partial gap cannot unlock the next phase.

The prospective runner also refuses to claim selector-level Q-ablation results:
the current export contains production selections, but not frozen per-variant
decision records, full candidate-pool gate traces, or enough information to
reconstruct exact decision-time no-bets. Any reconstructed selector result
would violate the matched-comparison protocol. Q-score shadow calculations are
therefore kept separate from selector outcomes.

The enhanced export also captured the current `ticket_generations` configuration.
All captured generations use `pricing="market"`; their recorded Q-score
thresholds are not selector gates (`min_q_score` is 0 for the active market
tiers). This is **Verified** live evidence that Q-ablation A/B is
`NOT_APPLICABLE_CURRENT_SELECTOR` for the current production path. It does not
invalidate the pre-registered model-score calibration experiment, but it does
mean a Q-score selector winner cannot be claimed from current tickets.

## Other persisted research paths checked

Two existing research stores were inspected read-only:

- The latest parameter sweep (`target_date=2026-10-06`, evidence through
  `2026-10-05`) adapted 5,348 candidates from 12,736 database rows, rejected
  7,388 for `UNVERIFIED_EXECUTABLE_QUOTE`, and found **zero qualifying
  policies**. This is **Verified** operational evidence, not a selector win or
  loss estimate; the stored limitations correctly rule out future-fill and
  profit claims.
- Four market-policy shadow snapshots exist for October 5–6. They record
  policy exclusions and selection diagnostics, but do not provide a complete
  settled, matched control-versus-shadow outcome cohort. They cannot substitute
  for the pre-registered 21-day test.

## Corrected retrospective diagnostic

The frozen retrospective runner was also rerun against its original source
hash after aligning Q-ablation B to the accepted 12.5% weight and separating
the retired AGGRESSIVE tier from current non-retired rows. It passed its live
constant checks. The non-retired shadow results were:

| Variant | Rows clearing full gate | Hit rate | Brier | Gap |
|---|---:|---:|---:|---:|
| Control | 36 / 39 | 66.7% | 0.254 | +0.194 |
| Q-ablation A | 29 / 39 | 72.4% | 0.220 | +0.137 |
| Q-ablation B, 12.5% | 33 / 39 | 69.7% | 0.234 | +0.161 |

These are same-archive shadow diagnostics, not prospective evidence and not a
production recommendation. Full details remain in
`docs/evidence/pre_retraining_2026-09-20/ablation_diagnostic.json`.

## Remaining permitted work

1. Let the frozen calibration/validation window run through
   `2026-10-15T05:33:30.491115+00:00` without changing model weights, gates, or
   selection policy.
2. Evaluate the frozen Platt fit on that disjoint window, including calibration
   gap, Brier score, and ECE with coverage and missing-label counts.
3. If the calibration phase is valid, lock the calibrator and collect the
   untouched 21-day prospective test through `2026-11-05T05:33:30.491115+00:00`.
4. Run the pre-registered Q-ablation A/B comparisons only as matched research
   diagnostics. Do not generate live ablated tickets or promote any variant.

## Reproducibility helpers

- `backend/scripts/research_forward_window_status.py`
- `backend/scripts/research_fit_platt.py`
- `backend/scripts/research_snapshot_inspect.py`
- `backend/scripts/prospective_validation.py`
- `backend/scripts/research_validation_gate.py` (fail-closed completion/primary-gap checker)
- `backend/scripts/export_pre_retraining.py` (now includes model-run and ticket-generation configuration in future read-only exports)

Focused regression coverage is in `backend/tests/test_research_validation.py`
(`4 passed` locally).

The full backend suite also passes locally: `306 passed, 1 skipped` (one
Starlette/httpx deprecation warning).

These helpers are read-only and expect the temporary `/tmp/fresh_source.json`
inside the configured application environment; they do not write to production.

The requirement-by-requirement status is recorded in
`docs/ABLATION_VALIDATION_COMPLETION_AUDIT_2026-10-06.md`.
