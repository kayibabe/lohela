**A — Accept.** The math is correct: threshold-on-P selection preserves E[Y−P|S]=0 only under exact conditional calibration on P; original claim that sorting-code alone "confirms" winner's curse as *the* mechanism overstates what code facts establish. Causal magnitude is unproven.

**B — Accept, retract original "near-perfect calibration."** Pooled signed gap of −0.6pp masking +13pp/+9pp/+12pp opposite-signed market gaps is a textbook cancellation artifact. ECE 6pp and 70–90% bin overconfidence contradict "almost perfectly calibrated." I retract that characterization.

**C — Accept as an open verification gap, not a refutation.** If local selector has undisclosed gates (sample-size, freshness, EV-based final ranking) beyond raw Q-sort, the original report's selector model may be stale or partial. This needs code-diff confirmation before the report's selection story is trusted as literally what's deployed. Flag as unresolved, not resolved in Codex's favor by assertion alone — Codex also hasn't shown the current gate code, just described it.

**D — Accept, and this is the most important new evidence.** If frozen-snapshot vs. latest-forecast comparison genuinely shows gap 0.2908→0.2918 with 0 discrepancies, that's strong evidence the ~29pp gap is not primarily a publication-timing artifact — it's baked into the underlying forecast at prediction time, concentrated among selected picks. This *narrows* the causal question to "why does the model produce miscalibrated P for this selected subgroup," not "did publication/logging corrupt it." Worth flagging: n=61 is small; CIs not given, so "barely explains" is plausible but not statistically quantified here.

**E — Accept qualification.** "All components marked available" is metadata, not validity. The xG-proxy caveat is legitimate and was underweighted in the original report.

**F — Accept.** EV/edge reusing p is a real objection; isotonic recalibration needs a genuine holdout-fit, not just a formula swap. Immediate Q-weight surgery is premature without an offline reconstructed ablation.

**G — Accept.** The two diagnostics are not independent; ticket P&L and 61-pick home-win figures are different samples and shouldn't be conflated as corroborating angles.

**What's actually established:** (1) published picks are meaningfully overconfident, gap not fully explained by cancellation, timing, or logging error (per D). (2) Market-level miscalibration is real and heterogeneous (per B), so "underlying forecasts are calibrated" is false. (3) Selection-on-P mechanically contributes but magnitude is unquantified without matched counterfactual ablation. (4) Whether Q-component weighting is the dominant lever vs. genuine subgroup (market/probability-band) miscalibration is unresolved.

**Verdict:** No immediate Q-weight change is justified. Next action: freeze current selector code/gates, reconstruct historical candidate pools per date, run offline ablation (Q-with-p vs Q-without-p vs EV-only vs isotonic-shrunk) against actual outcomes before touching production ranking. Confirm deployed selector SHA against the one Codex described.