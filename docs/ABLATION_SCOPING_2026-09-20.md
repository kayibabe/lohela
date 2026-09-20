# Frozen ablation experiment: scoping

Status: **scoping only — no code, gates, or production behavior changed.** This defines what the experiment in [`SELECTION_CALIBRATION_REVIEW_2026-09-20.md`](SELECTION_CALIBRATION_REVIEW_2026-09-20.md), "Next experiment, before any operational change" (lines 70–75), actually requires before it can run.

## 1. What "frozen ablation" means here

- **Frozen**: the selection policy under test is fixed for the whole prospective window — no retraining, no threshold tuning, no mid-window edits — so any measured difference is attributable to the policy variant, not to the model or gates moving underneath it.
- **Ablation**: systematically removing or rescaling one or more of the nine weighted Q-score components computed in [`ensemble.py:139-234`](../backend/app/services/models/ensemble.py:139), then re-normalizing the remaining weights, to see how selection and outcomes shift.
- **Retrospective vs. prospective**: re-running an ablated policy against the 61-pick historical archive is **diagnosis only** — it explains what already happened. It is not evidence that the ablated policy will do better going forward, because the archive is exactly the sample the review used to find the problem. A verdict requires evaluating a genuinely frozen policy on **later fixtures the policy has not seen or influenced**.

## 2. Policy variants to compare

Held fixed for the whole run — same code path, same seed data pulls, same publish gates except where a variant explicitly changes them:

| Variant | Definition |
| --- | --- |
| **Control** | Current production Q-score weights and gates, unchanged ([`Q_SCORE_WEIGHTS`](../backend/app/services/models/ensemble.py), thresholds in [`TICKET_SPECS`](../backend/app/services/accumulator_builder.py:55-59)). |
| **Q-ablation A** | Zero the `market_consensus` (10%) and `league_reliability` (5%) weights, re-normalize the remaining seven to sum to 100. These two are flagged in the review (line 60) as constant maximum contributions across all 61 published records — never observed to vary — so their predictive content is unverified. |
| **Q-ablation B** | Rescale `model_probability` down from 25% to **12.5%** (exactly half), redistributing the difference across the other available components; **not** dropped to zero, since edge and EV are themselves derived from p (review line 32) and a p-free ranking is a hypothesis, not a validated baseline. Weight decided per §7 item 4 below — the originally-placeholder 15% was tested retrospectively in `ablation_diagnostic.json` and found close to a no-op (mean Q shift +0.87 pts), so it was rejected in favor of a value large enough to be a meaningful test. |
| **Calibration-aware variant** | Apply a calibrator (e.g. isotonic or Platt) to `model_probability` before it enters the Q-score, fit strictly on a period disjoint from both the diagnostic archive and the prospective test window (see §4). |

Do not add an EV-only or edge-only ranking as a standalone variant per the review's explicit caution (line 73) — it is not established as a probability-independent baseline.

## 3. Matched comparison protocol

For every decision point compared across variants, hold identical:

- **Decision time** — same strictly-pre-kickoff cutoff used for the matched cohort in the review (§"New matched evidence").
- **Fixture set** — same matches, same markets.
- **Quote/odds eligibility** — same bookmaker/odds availability at decision time; do not let one variant see odds the other didn't.
- **Coverage and risk constraints** — same leg-count bounds, correlation caps, and daily-ticket minimums as `TICKET_SPECS` / `_RELAXATION_STEPS` (accumulator_builder.py:55-76), unless the relaxation ladder itself is the thing under test.
- **Rejected / no-bet decisions preserved** — a fixture a variant declines is a data point, not a gap. Do not silently drop no-bets when comparing hit rate or Brier across variants.

Record per decision, per variant: eligible candidate pool before gates, gate that included/excluded each candidate, final Q/EV/p, and outcome once settled.

## 4. Train/calibrate/test separation (if a calibrator is fit)

Three non-overlapping periods, in this order:

1. **Fit** — historical data used only to fit the calibrator.
2. **Calibrate/validate** — a separate historical slice used only to check the fit and pick a cutoff, never touched during fitting.
3. **Test** — the prospective window (§5), touched by nothing above.

Isotonic regression is flagged in the review (line 74) as especially prone to overfitting a small calibration sample — do not let the calibrate slice shrink below what the fit requires just to free up more test data.

**Decided (§7 item 3, see [`ABLATION_OPEN_ITEMS_PROPOSAL_2026-09-20.md`](ABLATION_OPEN_ITEMS_PROPOSAL_2026-09-20.md)):** Platt (logistic) scaling, not isotonic, fit only on the p≥0.65 tail — the retrospective diagnostic found the full forecast pool already reasonably calibrated (gap −0.006 across 1,963 forecasts) with the overconfidence concentrated in the published/selected subset's 70–90% band, so a full-range calibrator would risk distorting a region that isn't broken. No disjoint historical fit/calibrate slice exists (live predictions only start 2026-09-16, the same window the diagnostic archive covers), so both periods must be new forward data: ~15-day fit (target ≥300 forecasts, ≥60 in the p≥0.65 tail) then ~10-day calibrate, sequential, before the test window opens.

## 5. Prospective evaluation window

- The retrospective run (control + all ablations replayed against the existing 61-pick / 466-forecast archive) is diagnostic evidence for this scoping only — it must be labeled as such in any report and must not be used to declare a winner.
- The decision-bearing comparison runs all frozen variants **forward, in parallel, on fixtures published after this scoping is approved**, none of which fed the calibrator fit or the ablation design. **Decided (§7 item 2):** a 21-day test window, sized off the archive's per-leg gap variance (61 picks, SD 0.485) for 80% power to detect the calibration gap closing from +0.29 to ≤+0.15 (α=0.05, two-sided), with a 50% margin over the naive i.i.d. estimate (~14 days) for day-clustering the 5-day archive is too thin to model precisely, plus a mid-window variance recheck (not an outcome peek) around day 10. Calibration gap is the primary gate; Brier/ECE are tracked but not gating — detecting even a 0.02 Brier improvement would need ~9.5 months at current publishing volume (~12 picks/day), so it cannot be a launch criterion. See [`ABLATION_OPEN_ITEMS_PROPOSAL_2026-09-20.md`](ABLATION_OPEN_ITEMS_PROPOSAL_2026-09-20.md) for the full derivation.
- No variant may be edited once the prospective window starts. A code or weight change mid-window invalidates that variant's run.

## 6. Metrics and gates

Per variant, over both the retrospective (diagnostic) and prospective (decision) runs:

- Brier score, ECE, and per-market/per-probability-bin calibration (same methodology as `pre_retraining_report.py` / `review_selection_attribution.py`).
- Coverage: candidate pool size, selected count, no-bet rate.
- Profit/uncertainty: flat-stake return with bootstrap interval, not point estimate alone.
- Existing promotion gates (prior-period league/market calibration sample-size ≥ 20, etc.) — a variant must still pass these to be eligible for any future promotion discussion; this scoping does not change or waive them.

## 7. Open items / blockers before the prospective run can start

1. **Historical run configuration recovery** (review item 1, line 72) — candidates, exclusion reasons, and calibration-gate state per historical decision are not fully recoverable from the current snapshot (review line 58: "the snapshot lacks model-run and ticket-generation configuration tables and historical gate inputs"). This blocks a *complete* retrospective replay, though it does not block starting the prospective forward run. **Status: still open — this is a data-availability gap, not a decision, and is unaffected by items 2–4 below.**
2. **Power/window sizing** — ~~how many fixtures/dates are needed for the prospective comparison to be statistically informative has not been calculated yet.~~ **Status: proposed and accepted (see §5 above and [`ABLATION_OPEN_ITEMS_PROPOSAL_2026-09-20.md`](ABLATION_OPEN_ITEMS_PROPOSAL_2026-09-20.md) item 2) — 21-day test window, calibration gap ≤+0.15 as the pre-registered target, Brier/ECE monitoring-only.**
3. **Calibrator choice and fit/calibrate split sizes** — ~~not yet decided; depends on how much historical data can be cleanly separated from the diagnostic archive without reusing it.~~ **Status: proposed and accepted (see §4 above and proposal doc item 3) — Platt scaling on the p≥0.65 tail; ~15-day fit / ~10-day calibrate phases on new forward data, since no disjoint historical slice exists.**
4. **Ownership of "carefully rescaled/re-gated"** — ~~the exact rescaled weights for Q-ablation B need a specific number before implementation, not just "smaller."~~ **Status: proposed and accepted (see §2 above and proposal doc item 4) — 12.5% (half of 25%), rejecting the original 15% placeholder after the retrospective diagnostic showed it was close to a no-op.**

All three decisions in items 2–4 were drafted in [`ABLATION_OPEN_ITEMS_PROPOSAL_2026-09-20.md`](ABLATION_OPEN_ITEMS_PROPOSAL_2026-09-20.md) (2026-09-20), grounded in `ablation_diagnostic.json` (`source_sha256 = 388db18a93...`), and accepted the same day. Item 1 remains the sole blocker before a *complete* retrospective replay, and does not block starting the Fit phase (§4) for the prospective run.

## 8. Explicitly out of scope

- No retraining of any model.
- No change to production selection code, gates, or weights.
- No live/production ticket generation using any ablated variant.
- No promotion decision — this experiment can only inform one, not make one.

## 9. Deliverables when scoping is accepted

- A frozen-variant runner script (parallel to `backend/scripts/review_selection_attribution.py`) that replays control + ablations against fixed input snapshots and asserts source-hash identity, matching the existing reproducibility pattern.
- A retrospective (diagnostic) report clearly labeled as non-decision-bearing.
- A prospective run plan with fixed start date, window length, and pre-registered metrics/thresholds for what would count as a meaningful difference — pre-registered before the window starts, not chosen after seeing results.

## Requirements checklist

| Item | Status |
| --- | --- |
| Identify what "frozen ablation experiment" refers to in this codebase | Completed |
| Enumerate ablation candidates from Q-score components | Completed |
| Define matched comparison protocol | Completed |
| Define train/calibrate/test separation rule | Completed |
| Define prospective (decision-bearing) window | Completed — 21-day test window, gap ≤+0.15 target (§5, §7 item 2) |
| Recover historical run/gate configuration for full retrospective replay | Blocked — data gap noted in prior review (§7 item 1), unaffected by items 2–4 |
| Build frozen-variant runner script (retrospective) | Completed — `backend/scripts/ablation_diagnostic.py`, see `ABLATION_DIAGNOSTIC_RETROSPECTIVE_2026-09-20.md` |
| Resolve power/window sizing, calibrator choice/split, Q-ablation B weight (§7 items 2–4) | Completed — see `ABLATION_OPEN_ITEMS_PROPOSAL_2026-09-20.md`, accepted 2026-09-20 |
| Start the Fit phase (calibrator, §4) | **In progress** — started 2026-09-20T05:33:30Z, tracked in [`FIT_PHASE_TRACKER_2026-09-20.md`](FIT_PHASE_TRACKER_2026-09-20.md); no production change, per §8 |
| Build frozen-variant runner for the prospective (forward) Test run | Not started — gated on the Fit + Calibrate phases completing first |
