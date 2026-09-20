# Selection and calibration: reconciliation review

This reviews the Claude Code assessment pasted by the user against the frozen production evidence and the current local selection code. No training, production writes, selection-rule changes, or deployment took place. **A qualified consensus was reached with a fresh Claude Code review after two successful reconciliation responses.** The original Claude session was not resumed. The new reviewer had tools disabled and assessed supplied evidence; it did not independently query the database or rerun the calculations.

The initial longer request timed out without a verdict. A compact retry returned successfully; its response was saved before a local console-encoding error, which was corrected. A follow-up supplied the exact gate-code excerpts and resolved the one remaining causal wording disagreement. These execution details do not affect the frozen numerical evidence.

## Recorded agreement

The [first Claude response](evidence/pre_retraining_2026-09-20/claude_review_response_compact.md) accepted the substantive statistical corrections and rejected immediate Q-weight changes, but retained an inconsistent “mechanically contributes” causal claim. The [follow-up response](evidence/pre_retraining_2026-09-20/claude_review_response_followup.md) explicitly retracted that claim, accepted the supplied local gate-code excerpts, and stated that it had no remaining substantive objection.

The agreed replacement is: **selection concentrates observed forecast errors in this sample; a causal amplification mechanism and magnitude remain unproven.** Agreement does not close the empirical gaps about historical gate activation, input quality, or the effect of alternative policies. The supplied current code and calculations support the scoped conclusions below, not an independently replicated historical experiment.

## Evidence-supported conclusion

**Keep retraining and live selection changes on hold. Published selections concentrate substantial forecast overconfidence, while the underlying forecasts also have market-specific and high-probability calibration errors. The selector demonstrably depends on probability and derived scores; its causal contribution, and the benefit of removing the probability weight, remain unproven.**

Audit the historical eligible pools and gates, then compare frozen selection alternatives offline and on later untouched fixtures. Do not replace the current system with an unvalidated EV-only ranking or a calibrator fitted to these same five days.

## Claims reconciled against evidence

| Claim in the supplied Claude review | Assessment |
| --- | --- |
| The published forecasts are substantially overconfident in this archive. | Agree: 61 picks, 39 fixtures, five dates; mean p 79.90%, wins 50.82%, Brier 0.3163. Generalization beyond this archive remains uncertain. |
| The underlying all-market forecasts are almost perfectly calibrated. | Reject: pooled signed errors cancel across complementary markets. ECE is 6.00 pp; several markets and high-probability bins are materially overconfident. Brier below 0.25 alone does not establish calibration. |
| Q-score embeds p at 25%; baseline tier gates are 75–85; candidates are sorted by Q/EV/p and capped at 30. | Verified in current local code, with additional gates, bounded relaxation, and later beam/objective ranking. This is within a selected completed run/date, not across all 11,444 predictions. |
| The publish path is unconditioned on accuracy. | Too broad: local code includes prior-period league/market calibration gates for sample size at least 20. Their historical activation/effectiveness is not established by this snapshot. |
| Thresholding probability always makes a calibrated model overconfident. | Incorrect. A probability-only threshold preserves population calibration when E[Y\|P]=P. General composite-score selection may reveal subgroup errors, but the magnitude is not determined by the code alone. |
| Missing/zero Q components explain these published losses. | Unsupported for this cohort: all 61 records mark all nine components available, with nonzero stored contributions. Availability labels do not establish input quality. |
| The publication writer inflates individual probabilities. | Not observed: all 61 published snapshots equal their linked forecast probabilities. The writer copies the leg probability. Selection can change the cohort without changing a forecast. |
| The totals diagnostics independently corroborate each other. | Complementary, not statistically independent: both reuse the same fixture outcomes. Ticket-loss allocation remains an accounting convention, not a causal estimate. |
| Home-win profit explains the +2.05 ticket units. | The cited +12.26 belongs to the separate 61-pick flat-stake calculation. It must not be attributed directly to the 15-ticket ledger. |
| Remove p from Q or switch to edge/EV immediately. | Not justified yet. Edge and EV reuse p; removing up to 25 points also changes eligibility under unchanged Q thresholds. Treat alternatives as frozen experimental policies. |

## New matched evidence

The original comparison mixed different cohorts and forecast times. This review matches every first-published pick to the latest strictly pre-kickoff forecast for the **same fixture, market and model version**, using the same outcomes.

| Cohort | n | Mean p | Win rate | Gap | Brier |
| --- | --- | --- | --- | --- | --- |
| First-published unique picks | 61 | 79.8966% | 50.8197% | +29.0769 pp | 0.316343 |
| Latest pre-match forecasts for those same picks | 61 | 80.0019% | 50.8197% | +29.1823 pp | 0.316821 |
| All latest forecasts with p >= 70% | 466 | 82.5828% | 73.8197% | +8.7630 pp | 0.195209 |

There are no unmatched picks and no published-versus-linked-probability discrepancies. Sixteen retain the same prediction ID; 45 have a later row, and 30 have a changed probability. The mean change is only **+0.10535 pp**. Thus forecast timing barely accounts for the observed published-cohort problem; this narrows an open question in the original report.

The high-probability all-market subset establishes that relevant forecast overconfidence already exists outside the publication comparison. Its lower gap does not quantify a causal selection effect: it is not a matched counterfactual and differs in market, fixture and other characteristics. Likewise, comparing raw Brier scores across different outcome mixes is not a controlled policy comparison.

A sensitivity cohort drawn from the 11 model runs that supplied published picks has 5,060 settled binary forecast rows, 141 fixtures, Brier 0.221396 and ECE 5.8503 pp. This repeats fixtures across runs and is **not 5,060 independent trials** or a reconstruction of historical eligible candidate pools. It does not change the matched result.

## What the code actually establishes

- `backend/app/services/models/ensemble.py:139–160`: the probability component is 25 points before a disagreement downgrade; the 20-point edge component also depends on p. `:219` calculates EV as p × odds − 1.
- `backend/app/services/accumulator_builder.py:55–59`: baseline Q thresholds range from 75 to 85. `:63–76` allows bounded relaxation; these are not immutable universal floors.
- `backend/app/services/accumulator_builder.py:313–367`: selects a completed model run and loads prior-period calibration evidence. `:426–453` applies odds, edge, Q, model coverage, data quality, freshness and conditional calibration gates.
- `backend/app/services/accumulator_builder.py:610–620`: beam scoring and final objectives also use Q, EV, probability and risk. `:632–636` selects the top 30 by (Q, EV, p). Removing the final p tie-breaker alone would leave the primary probability-derived signals intact.
- `backend/app/services/ticket_publisher.py:216`: saves `probability_snapshot=leg.model_probability`, consistent with the frozen snapshot comparison.

These references describe current local code at base revision `f09f23c6d17646b86054f0c7c4dac5f3cc8aae81`, not a verified historical deployed implementation/configuration. The snapshot lacks model-run and ticket-generation configuration tables and historical gate inputs. Full historical selection replay therefore remains open.

All 61 published records have no learning profile ID. All mark the nine Q components available. Market-consensus contribution is 10/10 and league-reliability contribution is 5/5 throughout this cohort; constant maximum contributions deserve inspection rather than being treated as demonstrated predictive evidence. The stored xG-input label identifies a rolling-goal-derived expected-goals proxy. Neither these labels nor stored nonzero values certify feature quality or causal relevance.

## Statistical correction

For a calibrated probability P, E[Y\|P]=P. If inclusion S is determined solely by a threshold on P, then E[Y−P\|S]=E[E[Y−P\|P]\|S]=0. This is a population statement, not a guarantee of zero error in a small realized sample. General selection using other variables, slate rankings, estimated edges or correlated errors requires stronger conditional guarantees; marginal calibration is not enough.

The reproducible script includes a **synthetic counterexample**, separate from real evidence: 100 forecasts at p=.2 with 20 wins, and 100 at p=.8 with 80 wins. Filtering to p>=.7 leaves exactly 80% predicted and 80% observed, refuting the word “always.” It does not model Lohela or establish the absence of winner's curse here.

Brier measures reliability, resolution and outcome uncertainty together. A lower Brier score need not mean better calibration; the [scikit-learn calibration documentation](https://scikit-learn.org/1.8/modules/calibration.html) states this distinction. The observed published errors warrant investigation, but five dates and overlapping fixtures do not prove a particular cause or rule out variance. The original bootstrap intervals remain exploratory.

## Next experiment, before any operational change

1. Recover historical generation/run configuration, candidates, exclusion reasons and calibration-gate state. Establish feature/quote availability at each decision, including the constant maximum Q contributions.
2. Freeze the comparison protocol: current selector, a carefully rescaled/re-gated Q ablation, and any calibration-aware variant. Match decision times, fixtures, quote eligibility, allowed markets, coverage and risk constraints. Preserve rejected/no-bet decisions. Edge/EV-only ranking is a hypothesis, not a probability-independent baseline.
3. If fitting a calibrator, separate training/calibration/test periods. Isotonic regression is a fitted model and is particularly vulnerable to an undersized calibration sample. Never fit and validate it on these same outcomes.
4. Treat retrospective ablations as diagnosis; assess a frozen policy prospectively on later untouched fixtures. Retain lineage, calibration, coverage, profit/uncertainty and existing promotion gates. No causal benefit or safe live fix has yet been demonstrated.

## Reproducibility and scope

Frozen source SHA-256: `388db18a93acc3c46760318ac863c884b7112c7618862d5bf13b1785a978a5dd`.

Run `python backend/scripts/review_selection_attribution.py` to reproduce `docs/evidence/pre_retraining_2026-09-20/selection_review.json`. Assertions check source identity, complete matching, outcome agreement, chronological ordering, probability-copy identity, gap-change reconciliation, and the calibrated-threshold counterexample. Six report tests pass: the original four plus independent checks of the matched cohort and the calibrated-threshold counterexample. Run `python -m unittest discover -s backend/tests -p test_pre_retraining_report.py -v`.

The original report wording “no longer have a linked source-odds ID” was corrected to “have no linked source-odds ID in this snapshot”: the archive does not prove an earlier link existed. The user-supplied root-level copy was preserved. No reported original numerical result changed.
