# Frozen ablation experiment: retrospective diagnostic

Status: **retrospective diagnostic only — NOT decision-bearing.** Produced by [`backend/scripts/ablation_diagnostic.py`](../backend/scripts/ablation_diagnostic.py), fulfilling the first deliverable in [`ABLATION_SCOPING_2026-09-20.md`](ABLATION_SCOPING_2026-09-20.md) §9. Per that scoping doc §1 and §5: replaying a policy against the same 61-pick archive used to discover the calibration problem cannot show the policy will do better going forward. **This report must not be used to declare a winner or inform any promotion decision.** The prospective (decision-bearing) run is still blocked on the three open items in §7 (window/power sizing, calibrator fit/calibrate split, Q-ablation B's exact weight) and was explicitly out of scope for this pass.

Frozen input: `docs/evidence/pre_retraining_2026-09-20/source.json.gz`, `source_sha256 = 388db18a93acc3c46760318ac863c884b7112c7618862d5bf13b1785a978a5dd` (verified at run time). Full machine-readable output: [`docs/evidence/pre_retraining_2026-09-20/ablation_diagnostic.json`](evidence/pre_retraining_2026-09-20/ablation_diagnostic.json).

## Two known data gaps that bound what this diagnostic can say

1. **Republished-picks-only.** The snapshot lacks model-run/ticket-generation configuration and historical gate inputs (scoping §7 item 1), so the candidates gated *out* under control aren't recoverable. This script can only re-score the 61 picks that *were* published — it cannot simulate which new candidates an ablated Q-score would have admitted. "Still clears gate" below means exactly that: of the 61 published picks, how many survive the ablated weights against their own ticket's threshold — not a full re-selection.
2. **Relaxed-ticket thresholds are a range, not a number.** `relaxed_tier` is a boolean (`backend/app/models/research.py:234`), not a step count, so a relaxed ticket's true effective `min_q_score` (base − 5 or base − 10) can't be reconstructed. Reported as `[relaxation floor 60.0, full-strength threshold]`.

## Q-score ablations (published picks only)

| Variant | Mean Q shift | Still clear full-strength gate | Hit rate (of those clearing) | Brier | Calibration gap (mean p − actual) |
| --- | --- | --- | --- | --- | --- |
| Control | 0 | 58 / 61 | 50.0% | 0.322 | +0.299 |
| Q-ablation A (drop market_consensus + league_reliability) | −2.59 pts | 48 / 61 | 54.2% | 0.301 | +0.256 |
| Q-ablation B (model_probability 25%→15%) | +0.87 pts | 55 / 61 | 50.9% | 0.313 | +0.285 |

Reading this: dropping the two components the prior review flagged as constant/non-discriminative (`market_consensus`, `league_reliability`) is the only ablation that moves the needle — hit rate +4.2 points, Brier down 0.021, calibration gap down 3.4 points among the picks it would still have allowed. Ablation B (de-weighting raw model probability) is close to a no-op on this archive. **Neither gets anywhere near solving the problem**: even under the best-performing ablation, the surviving picks are still 25.6 points overconfident and still worse than a flat coin-flip on Brier (0.301 vs 0.25). This is consistent with the scoping doc's own caution — these are diagnostic shifts on the same archive that revealed the problem, not a fix.

Ablation A's rejected picks include 10 of the 61 that control alone would have allowed but a de-weighted-consensus/league-reliability policy would not (prediction IDs in the full JSON) — worth a manual look at whether those 10 skew toward the loss-heavy totals/BTTS markets already flagged in the prior review.

## The most interesting new finding: a full-population calibration check

The archive's full pre-match forecast pool (1,963 forecasts across 141 fixtures — every prediction the model made, not just what got selected and published) is **close to well-calibrated**: mean probability 0.498 vs actual win rate 50.3%, gap −0.006, ECE 0.060, Brier 0.222 (better than the flat-0.25 baseline).

That is a sharp contrast with the *published* 61 picks: mean probability 0.799 vs actual 50.8%, gap +0.291, Brier 0.316 (worse than flat-0.25).

**This is the first retrospective evidence directly consistent with the "winner's-curse" selection-effect hypothesis** the prior review flagged as suspected-but-unproven (`SELECTION_CALIBRATION_REVIEW_2026-09-20.md`): the underlying model looks reasonably calibrated across everything it predicts, and the severe overconfidence appears specifically once picks are *selected* for high Q-score/probability and published. It does not prove causation — it's the same archive, and it doesn't isolate the selection mechanism from other confounds (e.g., published picks concentrate in different markets/leagues than the full pool) — but it is a concrete, checkable signal worth carrying into the prospective design.

## Calibration-aware variant (isotonic, out-of-fold by day)

Applied to the full pool: ECE drops from 0.060 to 0.014, Brier from 0.222 to 0.220, gap from −0.0057 to −0.0026 — a modest, expected tightening of an already-reasonable calibration.

Applied to the published picks: **only 16 of the 61 published picks could be matched back to a row in the full-pool calibration set** (see `n_published_missing_from_all_live_calibration_set: 45` in the JSON) — the "published" snapshot probability often comes from an earlier model revision than what the deduped "latest per fixture/market" pool retains. The recalibrated-published metrics (n=16: hit rate 50%, Brier 0.295 vs control's 0.316 on the same 16) are **not representative of the full 61** and should not be read as "recalibration fixes it" — a quarter of the published archive can't currently be checked this way at all. That mismatch is itself worth flagging to whoever owns `model_run_id`/prediction versioning, independent of the ablation question.

This variant is explicitly not validated evidence per scoping §1/§4 — it reuses the diagnostic archive for both fit and evaluation (out-of-fold by day, not a disjoint period).

## What this diagnostic does and doesn't license

- Does: give a concrete, reproducible before/after picture of what three specific policy changes would have done to the 61 published picks and the full forecast pool, using code that cross-checks its own mirrored constants against the live source (`ablation_diagnostic.py` asserts `Q_SCORE_WEIGHTS` and `TICKET_SPECS` match `ensemble.py`/`accumulator_builder.py` at run time, and failed once during development on a real casing mismatch — since fixed).
- Does: surface a specific, testable hypothesis (selection-effect overconfidence) worth carrying into the prospective design's pre-registered metrics.
- Does not: pick a winner, justify a weight change, or replace the prospective run. Per scoping §7, that still needs window/power sizing, a calibrator fit/calibrate split, and a specific Q-ablation-B number decided before it can start — none of which this diagnostic resolves.

## Requirements checklist

| Item | Status |
| --- | --- |
| Build frozen-variant runner script (retrospective) | Completed — `backend/scripts/ablation_diagnostic.py` |
| Replay control + Q-ablation A + Q-ablation B against published-pick archive | Completed |
| Replay calibration-aware variant against full forecast pool | Completed |
| Replay calibration-aware variant against published picks | Completed with caveat — only 16/61 matchable |
| Retrospective diagnostic report, labeled non-decision-bearing | Completed (this document) |
| Prospective run plan (fixed start date, window, pre-registered thresholds) | Not started — out of scope for this pass, still blocked on scoping §7 open items |
