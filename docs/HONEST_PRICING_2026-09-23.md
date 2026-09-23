# Honest ("fair-priced") tickets — decision record (2026-09-23)

## Question
"Fix the model overconfidence so tickets actually win."

## Finding (verified; diagnosis only, nothing fitted on the archive)
The ensemble is not overconfident on average. The earlier report found a
−0.6 pp signed gap across all 1,963 scored forecasts, though ECE was 6 pp. The
**published picks** are the problem: +29 pp. Selecting the legs where the
model disagrees most with the bookmaker selects the model's errors.

`blend_diag` compares the model, the de-vigged market price, and logit blends
of the two. Blend weights were chosen leave-one-date-out.

| | Production archive (1,823 forecasts) | Local DB (1,300) |
|---|---|---|
| Model log loss | 0.647 | 0.675 |
| De-vigged market log loss | **0.589** | **0.598** |
| Best held-out blend | 0.590, 0–10% model weight | 0.598, 0% model weight |
| Model "≥3% edge" legs | 714: claimed 62.5%, won 48.0%, ROI −3.1% | 491: claimed 58.8%, won 40.3%, ROI −14.8% |
| ≥3% edge after correction | 3 legs | 0 legs |

The two datasets overlap heavily in real fixtures, so they are two
prediction sets, not independent samples. The data covers only 5–6 days.

**Conclusion.** The model currently adds no information beyond the market.
An honest calibration converges to the market price, and the "edges" vanish.
No calibration can make these tickets beat the market.

## Decision (user, 2026-09-23): honest likely-winners
- `settings.leg_probability_source = "market"` (default). Each leg is priced
  at the de-vigged market probability. Pairs are normalised, 1X2 is
  normalised over three outcomes, and double chance is derived from it. The
  model's number is kept as `raw_model_probability` for audit.
- `MARKET_TICKET_SPECS`: Conservative (3–4 legs, combined 1.8–3.2), Balanced
  (3–5, 3.2–6.5), Aggressive (4–6, 6.5–16). The objective is the most likely
  ticket in the band, traded against margin: `log(p) + EV`.
- The gates now judge the price and the data: a fair price exists, the odds
  are in the tier's leg band, the margin is at most `max_leg_margin` (7%), the
  odds are fresh, and data quality passes. Edge, Q-score, model-count,
  model-calibration and research market-restriction gates don't apply, because
  they judge the model's opinion.
- The 3/day floor, relaxation and rolling horizon are unchanged. Market
  relaxation widens odds bands and drops to 2 legs at most.
- The UI labels these tickets "Fair-priced". It shows the chance to win
  ("about 1 in N") and the expected return, which is normally negative (the
  margin). It hides edge, Q-score and spread, and says no value is claimed.
- Research Q-score sweeps and historical tickets stay model-priced. Set
  `LEG_PROBABILITY_SOURCE=model` to revert everything.

## Backtest (`backend/scripts/honest_pricing_backtest.py`)
Leg calibration of the fair price is within about 1 SE in the bands these
tiers use:

| Odds | Legs (archive / local) | Fair | Won |
|---|---|---|---|
| 1.20–1.40 | 143 / 122 | 75.9% / 75.3% | 78.3% / 70.5% |
| 1.40–1.65 | 235 / 169 | 63.8% / 64.0% | 66.4% / 61.5% |
| 1.65–2.30 | 430 / 333 | 50.6% / 50.7% | 51.6% / 51.1% |

Tickets were rebuilt per day with the production builder, then settled
(11–12 per set, indicative only):
- Market pricing claimed ~31% and won 4/12 on each dataset.
- Model pricing claimed 28–33% and won 3/11 and 1/12. Its Aggressive tickets
  averaged ~380× odds.

## What this does and doesn't promise
- Tickets now win about as often as they say. Conservative is roughly a coin
  flip at ~1.9×, Balanced about 3 in 10, Aggressive about 1 in 7.
- Over many tickets the expected loss is about the bookmaker margin (≈3–11%
  per ticket). Short-run paper profits in the backtest are noise.
- Beating the market needs a model that adds information. Validate that with
  closing-line value on untouched data (see the Fit-phase plan) before
  switching back to model pricing.

## Known limitations
- On a very thin day, tiers can share the same 1–2 matches in different
  markets (the existing 2-shared-matches rule). That is honest but poorly
  diversified.
- Other surfaces still show model edges: the strongest-selections list,
  singles research and recommendation picks.
