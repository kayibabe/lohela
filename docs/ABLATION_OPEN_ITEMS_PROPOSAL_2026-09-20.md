# Proposals for the three open items in ABLATION_SCOPING_2026-09-20.md §7

Status: **proposal only — nothing here is implemented or approved.** These are recommendations for the three blockers listed in [`ABLATION_SCOPING_2026-09-20.md`](ABLATION_SCOPING_2026-09-20.md) §7 items 2–4 (item 1, historical run-config recovery, is a data-availability gap, not a decision, and is unaffected by this doc). Each proposal is grounded in the archive already used for [`ABLATION_DIAGNOSTIC_RETROSPECTIVE_2026-09-20.md`](ABLATION_DIAGNOSTIC_RETROSPECTIVE_2026-09-20.md) — same `source_sha256 = 388db18a93...`. Numbers below come from re-querying that frozen archive, not from the model or gates.

Per scoping §7 item 4, someone needs to own these numbers before implementation. Treat this as a draft for that sign-off, not the sign-off itself.

---

## Item 2: Power / window sizing for the prospective run

### What the archive tells us about baseline noise

The published-pick archive is thin: **61 picks, 39 fixtures, only 5 calendar days**, and one of those days has a single pick (`2026-09-20`, n=1, gap=0.79) — an unstable point that inflates day-to-day variance. Per-leg:

- Calibration gap (`p − y`): mean **+0.291**, SD **0.485** across the 61 picks.
- Brier component (`(p−y)²`): mean **0.316**, SD **0.300**.
- Daily publishing volume: **~12.2 picks/day** on average (7–25/day observed — highly non-uniform, since thin weekday slates trigger `accumulator_builder.py`'s relaxation ladder, which changes both which picks clear and how many).

### Proposed primary metric and target effect

Recommend **calibration gap** as the primary pre-registered go/no-go metric (not Brier — see below), with a **pre-registered target of closing the gap from +0.29 to ≤+0.15** (roughly halving it). This number isn't arbitrary: it's the same order of movement Q-ablation A already showed retrospectively (+0.291 → +0.256 among surviving picks) — so it's an effect size the diagnostic suggests is at least plausible to detect, not a number pulled from nowhere.

Using a standard two-sample power calculation (α=0.05 two-sided, power=0.80, per-leg SD from the archive above, i.i.d. approximation):

| Detect gap reduction to | Legs needed per variant | Days at ~12/day |
| --- | --- | --- |
| ≤ +0.20 | 92 | ~8 |
| **≤ +0.15 (proposed target)** | **164** | **~14** |
| ≤ +0.10 | 370 | ~30 |
| ≤ +0.05 | 1,480 | ~121 |

**Recommendation: a 21-day (3-week) TEST window**, not the raw 14-day figure — a 50% margin over the naive i.i.d. estimate to cover two known sources of extra variance the i.i.d. calculation ignores: (a) day-clustering — the observed per-day gaps range from +0.10 to +0.79, a spread the 5-day archive is too thin to model properly as a design effect, and (b) non-uniform daily volume from the relaxation ladder. This is a judgment call, not a derived number — flagging it as such rather than dressing it up as more rigorous than it is.

**Brier should be a secondary/monitoring metric, not a gate.** Detecting even a 0.02 Brier improvement at the observed per-leg variance needs ~289 days (~9.5 months) — not feasible as a launch criterion. Track it, report it, but don't block the decision on it.

**Recommend a mid-window variance re-check** (not a result peek): at the ~10-day mark, recompute the per-leg SD from real prospective data and confirm the 21-day window is still sized correctly, or extend it. This checks the *assumption* (variance), not the *outcome* (which variant is winning) — it doesn't inflate the false-positive rate the way outcome-based early stopping would.

### Caveat

This whole calculation rests on 5 day-clusters, one of which is a single data point. Treat the 21-day recommendation as a reasonable starting plan to be revised once ~10 real prospective days exist, not a number to defend past the point new data contradicts it.

---

## Item 3: Calibrator choice and fit/calibrate split sizes

### A finding from the retrospective diagnostic changes the recommendation here

The diagnostic report found the **full forecast pool is already reasonably calibrated** (1,963 forecasts, gap −0.006, ECE 0.060) — the severe overconfidence is concentrated in the **published/selected** subset (gap +0.291), and specifically in the 70–90% probability band (per the prior review: 70–80% bin won 3/14, 80–90% won 10/21, while 90–100% was fine at 12/13). A calibrator fit blindly across the full 0–1 range would be "fixing" a region that isn't broken and risks distorting it while barely touching the region that is.

**Recommendation: fit the calibrator only on the upper tail (p ≥ 0.65)**, where the archive shows the actual miscalibration, rather than the full range. This also directly tests — rather than assumes — the winner's-curse hypothesis: if a tail-only calibrator meaningfully closes the gap, that's evidence the problem really is concentrated at the selection boundary; if it doesn't, that's evidence against the hypothesis, which is itself useful.

**Recommendation: Platt (logistic) scaling over isotonic, at least initially.** The scoping doc itself flags (§4, citing the prior review) that isotonic regression is prone to overfitting a small calibration sample. Platt scaling has only 2 parameters vs. isotonic's effectively-one-per-bin flexibility, and the tail-only fit set will be small (see below) — Platt is the more conservative choice given the data volume, with isotonic revisited only once enough tail data accumulates (a few hundred p≥0.65 predictions, informally).

### Fit/calibrate/test split sizes — and a scheduling consequence

**There is no existing disjoint historical slice to split.** Checked directly against the archive: `matches` data goes back to 2026-08-05, but the `predictions` table — the live model-probability records this whole diagnostic depends on — only starts **2026-09-16**, the same 5-day window as the diagnostic archive. There is no earlier live-prediction history to carve a fit/calibrate period out of without reusing exactly the data the diagnosis was built on, which scoping §1 and §4 already rule out.

This means fit and calibrate periods **must be new forward data**, collected after this proposal is approved, sequentially before the test window — not sourced from anything already captured. Proposed structure:

| Phase | Length | Purpose | Gate before continuing |
| --- | --- | --- | --- |
| **Fit** | ~15 days | Log natural (uncalibrated) predictions; no calibrator or ablation variant active. Target ≥300 forecasts total and ≥60 with p≥0.65 (the tail the calibrator targets) — at ~12/day this needs the full 15 days given only a fraction of predictions land in the tail. | Confirm ≥60 tail forecasts collected; extend if the slate has been thin. |
| **Calibrate/validate** | ~10 days | Separate slice, untouched during fitting, used only to check the fitted calibrator and pick a cutoff. | Confirm calibrator doesn't degrade calibration on this slice vs. no calibrator; if it does, do not proceed to test with it. |
| **Test** | ~21 days (item 2) | Frozen variants run in parallel, including the calibration-aware variant fit above. | This is the decision-bearing window itself. |

**Total: ~46 days (~6.5 weeks) minimum before a decision-bearing verdict exists**, run sequentially and never edited mid-phase. This is a real cost of doing the calibrator properly rather than reusing tainted data — worth surfacing explicitly since it's longer than "just run the ablation" sounds like it should take.

---

## Item 4: Q-ablation B's exact rescale weight

Scoping §2 defines Q-ablation B only as "rescale `model_probability` down from 25% toward a smaller weight (e.g. 15%)" — not zero, since edge/EV are themselves derived from p. The retrospective diagnostic already ran the placeholder 15% and found it's **close to a no-op**: mean Q shift only +0.87 points, gate-crossing barely changed (55/61 still clear vs. 58/61 control), and calibration gap barely moved (+0.285 vs. control's +0.291). At 15%, this variant isn't different enough from control to be a meaningful test of "does de-weighting raw model probability help."

**Recommendation: 12.5% (exactly half of the current 25%)**, not 15%. Rationale:
- It's a principled, easy-to-defend number (a straight halving) rather than an arbitrary intermediate value.
- The 15% placeholder's near-no-op result in the diagnostic is itself evidence that a milder cut won't produce a detectable signal even with the full prospective window sized in item 2 — a weight change that doesn't move the needle isn't worth spending the 21-day test window on.
- It stays well clear of zero, respecting the scoping doc's explicit caution against a p-free ranking (§2, last line).

This is the one item that's purely a judgment call with no data-derived "right answer" — flagging for explicit sign-off rather than treating 12.5% as self-evidently correct.

---

## What happens if these are approved

These three answers, plus the already-defined variants and matched-comparison protocol (scoping §2–3), would fully unblock scoping §7 except item 1 (historical run-config recovery, a data gap unrelated to these decisions). The next step would be updating `ABLATION_SCOPING_2026-09-20.md`'s open-items table and checklist, then starting the Fit phase — none of which this document does on its own.

## Requirements checklist

| Item | Status |
| --- | --- |
| Propose power/window sizing (§7.2) with data-grounded rationale | Completed — 21-day test window, gap ≤0.15 target, Brier as monitoring-only |
| Propose calibrator choice and fit/calibrate split (§7.3) | Completed — Platt scaling on p≥0.65 tail; ~15/10-day fit/calibrate phases, new forward data required |
| Propose Q-ablation B exact weight (§7.4) | Completed — 12.5% (half of 25%), with rationale against the diagnosed near-no-op at 15% |
| Get explicit ownership sign-off on all three (§7 item 4) | Not started — pending your review of this document |
