# Loss-attribution and probability-calibration report — before retraining

**Decision: keep retraining and promotion on hold pending a controlled data/selection audit and an untouched evaluation design.** This report identifies diagnostic priorities; it does not fit a model, alter weights or thresholds, publish selections, or change production state.

**Verified:** published selections are substantially overconfident in this archive. Their average probability was **79.90%**, versus **50.82%** observed wins: **+29.08 percentage points**. Positive paper returns do not resolve this calibration defect. **Inferred:** totals underestimation and the publication/selection process deserve investigation before general retraining. This is not a causal proof of why an individual match was lost.

Completed: production read-only capture, cohort reconciliation, loss attribution, probability scoring, boundary checks, independent settlement comparison, and report review. Needs attention: quote-record retention, feature/parameter lineage, and later untouched observations. Retraining remains unperformed.

## Evidence and scope


Capture: **2026-09-20T05:33:30.491115+00:00** (07:33:30 Africa/Blantyre on 20 September 2026). Source: Railway production web, PostgreSQL; transaction `REPEATABLE READ READ ONLY`, verified `transaction_read_only=on`, ended with rollback. No database writes or model fitting. Snapshot figures stop at capture time; unresolved fixtures may settle later.


Local analysis revision before changes: `f09f23c6d17646b86054f0c7c4dac5f3cc8aae81`. The deployed Git SHA was unavailable from the runtime environment, so local/deployed code identity is not asserted. Results are computed offline from captured records. Database final scores were not independently reconciled to a second results provider. No bookmaker account or cash wagers were inspected.

This production evidence supersedes neither nor combines with the 19 September **local Docker** singles baseline; they are different databases and cohorts.


| Table | Rows |
| --- | --- |
| predictions | 11444 |
| accumulator_tickets | 45 |
| ticket_selections | 201 |
| ticket_results | 37 |
| competitions | 24 |
| teams | 514 |
| matches | 1232 |

## Cohort construction and exclusions

- All-market diagnostic: exclude historical `as_of_at` replays; require prediction creation strictly before kickoff and matching market/selection; retain the latest eligible prediction per fixture, market, selection and model version. Only finished fixtures with scores and binary supported outcomes are scored. Quote availability does not determine calibration inclusion.
- Published diagnostic: retain the **first pre-kickoff published snapshot** per fixture/market/selection across all ticket versions, including superseded versions. Require prediction creation no later than publication. This measures what was actually published, not a hypothetical latest prediction. Reserve the first record before looking at outcome. Repeated publication is not an independent trial.
- Ticket P&L: latest ticket version per target date/type, latest settlement version. This matches the daily-ticket accounting convention, but is not an executed-wager ledger. It is a different cohort from first-published unique picks. Four of 19 latest tickets remain unsettled and contribute no settled P&L.
- Exclude draw-no-bet from binary calibration rather than treat pushes as losses or assume unconditional probabilities are conditional on a non-draw. Quarter/integer totals are likewise unsupported by this report.


| All-prediction disposition | Count |
| --- | --- |
| superseded_pre_match_prediction | 4566 |
| historical_replay | 3771 |
| unsupported or push-sensitive market | 280 |
| unresolved | 864 |
| Scored | 1963 |

| Published-selection disposition | Count |
| --- | --- |
| repeat_publication | 122 |
| unresolved | 18 |
| Scored unique picks | 61 |

The 1,963 scored all-market records represent only **141 fixtures and five CAT dates (16–20 September)**. The 61 published records represent **39 fixtures**, also five dates. Complementary and overlapping markets are dependent. The date range includes a partially completed 20 September. Pre-kickoff insertion timestamps do not prove that every feature and parameter was constructed without future information.

## Loss attribution

### Ticket ledger: losses and offsets

The 15 settled latest daily tickets contain **3 wins and 12 losses**, staking 15 paper units. Gross losing stakes are **12.00 units**; winning tickets contribute **14.047929 units of net profit**, giving **+2.047929 units net / +13.65% paper ROI**. Winning returns are 17.047929 units. Mean adjusted ticket probability is **43.32%**, versus **20.00%** observed wins. Tickets share fixtures and cannot be treated as 15 independent trials.


| Tier | Settled | Wins | Net paper units |
| --- | --- | --- | --- |
| AGGRESSIVE | 3 | 0 | -3.0000 |
| BALANCED | 4 | 1 | +5.1461 |
| BEST_VALUE | 4 | 0 | -4.0000 |
| SAFE | 4 | 2 | +3.9018 |

Allocate each lost ticket's stake equally among its losing legs. This is an additive accounting convention, **not causal attribution or the profit recoverable by removing a leg**. It avoids charging a full ticket loss to every losing selection. Allocations sum to 12.00 units.


| Failed-leg market | Allocated gross ticket loss | Share |
| --- | --- | --- |
| under_3.5 | 3.583 | 29.9% |
| double_chance_x2 | 2.750 | 22.9% |
| under_2.5 | 2.167 | 18.1% |
| under_4.5 | 1.000 | 8.3% |
| btts_no | 0.917 | 7.6% |
| double_chance_1x | 0.500 | 4.2% |
| away_win | 0.417 | 3.5% |
| home_win | 0.417 | 3.5% |
| over_3.5 | 0.250 | 2.1% |

Under 2.5 and Under 3.5 together account for **5.75 units / 47.92%** of allocated gross ticket losses. Double chance X2 contributes **2.75 units / 22.92%**. These are the first exposure groups to inspect, not automatic exclusion recommendations.

### Unique published picks

Across 61 picks, **31 won and 30 lost**. A hypothetical one-unit stake at each saved ticket price returns **+2.72 units / +4.46%**. Home-win picks contribute +12.26 units; without that subgroup the other 57 picks total −9.54 units. This concentration matters more than the small positive aggregate return.

All 61 saved prices are usable for snapshot arithmetic, but **45 predictions no longer have a linked source-odds ID**. Their saved bookmaker/provenance fields remain. Missing links are a traceability limitation, not proof that those prices were fabricated or unavailable. The stricter linked-quote subset has only **16 picks**, +0.52 paper units / +3.25%; it must not be represented as the full cohort. Source odds themselves were not exported, so the link's presence is not full price verification. No executable-price or realized-return claim is made.


| Market | Picks | Wins | Losses | Snapshot paper units |
| --- | --- | --- | --- | --- |
| away_win | 2 | 0 | 2 | -2.00 |
| btts_no | 7 | 3 | 4 | -0.31 |
| double_chance_1x | 5 | 3 | 2 | +1.91 |
| double_chance_x2 | 9 | 4 | 5 | -2.71 |
| home_win | 4 | 3 | 1 | +12.26 |
| over_1.5 | 1 | 1 | 0 | +0.27 |
| over_2.5 | 2 | 2 | 0 | +1.03 |
| over_3.5 | 2 | 1 | 1 | +0.17 |
| under_2.5 | 9 | 3 | 6 | -3.26 |
| under_3.5 | 16 | 8 | 8 | -4.54 |
| under_4.5 | 4 | 3 | 1 | -0.10 |

**23 of 30 losses** were picks assigned at least 70% probability. Only **3 of 30** had odds at least 3.0. None of the losing published picks had Q-score below 70 or recorded edge below 0.05. Therefore low Q-score/thin-edge flags do not explain these observed losses; high recorded Q/edge is not independent evidence of quality. Flags overlap and are descriptive.

## Probability calibration

Brier is mean squared probability error; log loss uses natural logarithms with numerical clipping at 1e-15. Lower is better. Gap is mean predicted probability minus win frequency. ECE is count-weighted absolute gap in fixed 10-percentage-point bins. These are descriptive scores, not guarantees of future performance.


| Cohort | n | Mean probability | Observed win rate | Gap (pp) | Brier | Log loss |
| --- | --- | --- | --- | --- | --- | --- |
| All latest pre-match binary forecasts | 1963 | 49.8% | 50.3% | -0.6 | 0.2223 | 0.6401 |
| First published unique picks | 61 | 79.9% | 50.8% | +29.1 | 0.3163 | 0.8619 |
| Published picks with p >= 70% | 48 | 84.4% | 52.1% | +32.4 | 0.3263 | 0.8908 |

The published cohort expected **48.74 wins** in aggregate but recorded **31**. Its Brier **0.3163** is worse than constant 50% forecasts (**0.2500**), and log loss **0.8619** is worse than the same baseline (**0.6931**). Published ECE is **29.08 pp**. The high-confidence subset is 25/48 wins, despite an average probability of **84.44%**.

The pooled all-market signed gap is only −0.57 pp, but **opposing-market errors cancel**. Its ECE is **6.00 pp**. A small pooled signed gap cannot establish calibration. All-market Brier is 0.2223, versus **0.1996 for raw 1/quoted odds on the same rows**. Raw implied probabilities include bookmaker margin and are not a de-vigged fair-probability benchmark. All-market flat-stake arithmetic loses **96.01 units / −4.89%**; wagering on all overlapping/opposing markets is not a proposed strategy.

### Published reliability bins


| Cohort | n | Mean probability | Observed win rate | Gap (pp) | Brier | Log loss |
| --- | --- | --- | --- | --- | --- | --- |
| 0.5-0.6 | 4 | 56.2% | 50.0% | +6.2 | 0.2482 | 0.6893 |
| 0.6-0.7 | 9 | 66.2% | 44.4% | +21.7 | 0.2933 | 0.7842 |
| 0.7-0.8 | 14 | 75.2% | 21.4% | +53.8 | 0.4659 | 1.1829 |
| 0.8-0.9 | 21 | 85.3% | 47.6% | +37.7 | 0.3925 | 1.0886 |
| 0.9-1.0 | 13 | 92.9% | 92.3% | +0.6 | 0.0693 | 0.2568 |

Bins are left-inclusive/right-exclusive, with 1.0 included in the last bin. The 70–80% bin wins **3/14**, and the 80–90% bin wins **10/21**. The 90–100% bin's 12/13 result is too small to justify raising a threshold after seeing outcomes.

### All-market reliability bins


| Cohort | n | Mean probability | Observed win rate | Gap (pp) | Brier | Log loss |
| --- | --- | --- | --- | --- | --- | --- |
| 0.0-0.1 | 74 | 6.3% | 13.5% | -7.3 | 0.1244 | 0.4572 |
| 0.1-0.2 | 161 | 15.5% | 21.7% | -6.2 | 0.1738 | 0.5378 |
| 0.2-0.3 | 268 | 25.2% | 35.4% | -10.3 | 0.2394 | 0.6770 |
| 0.3-0.4 | 257 | 34.6% | 40.1% | -5.4 | 0.2421 | 0.6777 |
| 0.4-0.5 | 239 | 45.0% | 48.1% | -3.2 | 0.2517 | 0.6965 |
| 0.5-0.6 | 254 | 55.0% | 54.3% | +0.7 | 0.2487 | 0.6905 |
| 0.6-0.7 | 244 | 65.1% | 60.7% | +4.4 | 0.2399 | 0.6732 |
| 0.7-0.8 | 197 | 75.1% | 66.0% | +9.1 | 0.2328 | 0.6630 |
| 0.8-0.9 | 173 | 84.9% | 76.3% | +8.6 | 0.1878 | 0.5729 |
| 0.9-1.0 | 96 | 93.7% | 85.4% | +8.3 | 0.1313 | 0.4653 |

### Market diagnosis


| Cohort | n | Mean probability | Observed win rate | Gap (pp) | Brier | Log loss |
| --- | --- | --- | --- | --- | --- | --- |
| away_win | 141 | 35.0% | 29.8% | +5.2 | 0.1935 | 0.5664 |
| btts_no | 140 | 52.4% | 40.7% | +11.7 | 0.2732 | 0.7442 |
| btts_yes | 140 | 47.6% | 59.3% | -11.7 | 0.2732 | 0.7442 |
| double_chance_1x | 140 | 63.8% | 70.7% | -6.9 | 0.1952 | 0.5676 |
| double_chance_x2 | 140 | 62.3% | 55.7% | +6.5 | 0.2315 | 0.6608 |
| draw | 141 | 25.9% | 26.2% | -0.4 | 0.1919 | 0.5704 |
| home_win | 141 | 39.1% | 44.0% | -4.9 | 0.2286 | 0.6507 |
| over_1.5 | 140 | 71.0% | 78.6% | -7.6 | 0.1851 | 0.5562 |
| over_2.5 | 140 | 48.3% | 61.4% | -13.2 | 0.2646 | 0.7267 |
| over_3.5 | 140 | 29.6% | 38.6% | -9.0 | 0.2545 | 0.7215 |
| over_4.5 | 140 | 16.6% | 17.9% | -1.3 | 0.1512 | 0.5030 |
| under_2.5 | 140 | 51.7% | 38.6% | +13.2 | 0.2646 | 0.7267 |
| under_3.5 | 140 | 70.4% | 61.4% | +9.0 | 0.2545 | 0.7215 |
| under_4.5 | 140 | 83.4% | 82.1% | +1.3 | 0.1512 | 0.5030 |

**Inferred:** totals lean too low in this window. Over 2.5 occurs in 61.43% of fixtures against 48.26% predicted; BTTS Yes occurs in 59.29% against 47.62%. The corresponding Under/BTTS No forecasts are too high. Both Over/Under 2.5 Brier scores are 0.2646, and BTTS scores are 0.2732, worse than constant 50%. Under 3.5 is 70.38% predicted versus 61.43% observed in the all-market cohort, but **86.73% versus 50.00%** among 16 published picks. This difference motivates investigating selection and prediction timing as well as model calibration; the two cohorts are not matched causal controls.

### Model-version separation


| Cohort | n | Mean probability | Observed win rate | Gap (pp) | Brier | Log loss |
| --- | --- | --- | --- | --- | --- | --- |
| 0.2.1 | 1739 | 49.7% | 50.3% | -0.6 | 0.2196 | 0.6329 |
| 0.3.0 | 224 | 50.2% | 50.4% | -0.2 | 0.2432 | 0.6965 |

Version 0.2.1 contributes 1,739 forecasts across 125 fixtures/four dates; 0.3.0 contributes 224 forecasts across 16 fixtures/one date. Published picks are 60 from 0.2.1 and only one from 0.3.0. These are different fixtures/time periods, not a head-to-head experiment. No version superiority or deterioration is established.

### Components on matched rows


| Component | Available n | Component Brier | Ensemble Brier, same rows |
| --- | --- | --- | --- |
| poisson_prob | 1960 | 0.2251 | 0.2223 |
| zinb_prob | 0 | N/A | N/A |
| bayes_prob | 1960 | 0.2495 | 0.2223 |
| elo_prob | 423 | 0.2126 | 0.2046 |
| xg_prob | 1960 | 0.2143 | 0.2223 |

xG has a better observed Brier than the ensemble on its matched rows; Bayes is worse. This is diagnostic evidence to examine inputs/weights, not permission to tune weights on this same archive. ZINB has no observations in this scored cohort. Component errors can be correlated; counting models does not establish independent corroboration.

### Published day and league sensitivity


| Cohort | n | Mean probability | Observed win rate | Gap (pp) | Brier | Log loss |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-09-16 | 7 | 81.4% | 71.4% | +10.0 | 0.2186 | 0.6154 |
| 2026-09-17 | 12 | 84.4% | 41.7% | +42.7 | 0.4082 | 1.1097 |
| 2026-09-18 | 16 | 75.1% | 43.8% | +31.3 | 0.3232 | 0.8695 |
| 2026-09-19 | 25 | 80.4% | 56.0% | +24.4 | 0.2828 | 0.7788 |
| 2026-09-20 | 1 | 79.2% | 0.0% | +79.2 | 0.6273 | 1.5704 |

| Cohort | n | Mean probability | Observed win rate | Gap (pp) | Brier | Log loss |
| --- | --- | --- | --- | --- | --- | --- |
| 2. Bundesliga | 1 | 80.8% | 100.0% | -19.2 | 0.0370 | 0.2137 |
| Brasileirão Série A | 3 | 80.5% | 0.0% | +80.5 | 0.6513 | 1.6921 |
| Bundesliga | 3 | 74.3% | 66.7% | +7.6 | 0.1619 | 0.4787 |
| Championship | 3 | 79.5% | 33.3% | +46.2 | 0.3661 | 0.9285 |
| Jupiler Pro League | 2 | 76.4% | 50.0% | +26.4 | 0.3563 | 0.9613 |
| La Liga | 1 | 80.8% | 0.0% | +80.8 | 0.6532 | 1.6513 |
| League Cup | 5 | 76.3% | 60.0% | +16.3 | 0.2937 | 0.8133 |
| League of Ireland | 6 | 72.2% | 33.3% | +38.9 | 0.3797 | 1.0149 |
| Ligue 1 | 5 | 70.3% | 40.0% | +30.3 | 0.3665 | 1.0424 |
| Ligue 2 | 9 | 83.3% | 44.4% | +38.8 | 0.3615 | 0.9618 |
| Major League Soccer | 2 | 93.2% | 100.0% | -6.8 | 0.0057 | 0.0714 |
| Premier League | 4 | 73.7% | 75.0% | -1.3 | 0.1823 | 0.5245 |
| Premiership | 1 | 85.8% | 0.0% | +85.8 | 0.7365 | 1.9535 |
| Primeira Liga | 2 | 83.9% | 50.0% | +33.9 | 0.3080 | 0.8063 |
| Serie A | 3 | 82.6% | 66.7% | +15.9 | 0.1500 | 0.4194 |
| UEFA Europa League | 11 | 87.1% | 63.6% | +23.4 | 0.2709 | 0.7637 |

League groups contain only 1–11 picks. Do not blacklist leagues or select a profitable market from these post-hoc comparisons. Full all-market league, date, version-by-market metrics and individual scored rows are in `analysis.json`.

## Uncertainty and attribution limits

The published probability-minus-outcome gap's exploratory percentile 95% interval is **+16.74 to +42.21 pp** when resampling fixtures, and **+21.33 to +39.96 pp** when resampling dates. Each uses 1,000 bootstrap replicates, seed 20260920. Entire fixture/date groups are resampled to retain within-group dependence. Only five date clusters exist, making date intervals unstable; neither method captures all repeated-team, competition, temporal, or policy-search dependence. These are diagnostic intervals, not a promotion test.

All-market fixture-resampled gap is −1.29 to +0.16 pp, but complements make this aggregate especially uninformative. No claim of adequate calibration follows from that interval. No new fitted calibrator, calibration slope/intercept, tuned threshold, or retrained model was estimated. Per-market base-rate Brier in the JSON uses the same sample's outcomes and is descriptive, not an out-of-sample baseline.

CLV fields are populated on all 1,963 scored latest predictions, but closing quote timestamps/provenance have not been independently audited here; populated CLV is not established price advantage. Match data quality at settlement time cannot explain decision-time losses without a matching historical snapshot. Existing loss-review flags such as “normal variance” do not establish the absence of systematic error.

## Required sequence before retraining

1. **Audit lineage and selection first.** Preserve this snapshot. Trace the high-confidence losses (see examples below) back to feature cutoffs, model-run parameters, quote capture, and publication. Explain why selected probabilities are so much higher than later all-market probabilities; compare the same fixture/market at first publication and later pre-match runs. Verify retention of source quotes and exact market mapping. These checks are still open.
2. **Freeze evaluation rules before fitting.** Separate prospective live records from reconstructed replays; keep fixtures and their overlapping markets together. Define chronological training, calibration, and later untouched test windows. Restrict feature/parameter availability at every forecast cutoff. The five observed dates cannot serve simultaneously as tuning data and proof of improvement.
3. **Use a bounded challenger experiment only after that audit.** Prioritize totals/BTTS probability bias and component contributions. Compare the incumbent, an explicit calibration-only challenger, and any retrained challenger on identical later fixtures/quotes. Fit calibration only inside its designated earlier window; do not change live weights or thresholds from this report.
4. **Retain operational promotion gates.** Require reproducible lineage, settlement integrity, market-level calibration/Brier/log-loss comparisons, positive fixed-unit net returns and the existing positive lower ROI-bound requirement, coverage, drawdown and dependence-aware uncertainty on untouched observations. Define acceptable calibration tolerances and sample/duration requirements before viewing those results. No adequate new promotion sample size or tolerance is inferred here.

No retraining, deployment, ledger rewrite, settlement change, or live-staking change was made. The report is complete; those follow-up investigations and a future training experiment are separate work.

## Traceable high-confidence misses


| Prediction ID | Selection ID | Fixture ID | Market | Probability | Final score |
| --- | --- | --- | --- | --- | --- |
| 6233 | 101 | 1387 | under_3.5 | 91.55% | 3-2 |
| 619 | 25 | 1350 | under_3.5 | 89.32% | 4-1 |
| 325 | 15 | 1354 | under_3.5 | 88.67% | 3-2 |
| 382 | 17 | 1345 | double_chance_x2 | 87.03% | 4-0 |
| 1758 | 70 | 1368 | under_3.5 | 86.10% | 3-4 |

## Reproduction and verification

Frozen input: `docs/evidence/pre_retraining_2026-09-20/source.json.gz` (compressed JSON). Derived output: `analysis.json` in the same folder. Input tables contain football research records, not user accounts, credentials or bet-account balances.


Input SHA-256: `388db18a93acc3c46760318ac863c884b7112c7618862d5bf13b1785a978a5dd`.


Run from the repository root:

```powershell
python backend/scripts/pre_retraining_report.py docs/evidence/pre_retraining_2026-09-20/source.json.gz docs/evidence/pre_retraining_2026-09-20/analysis.json
python backend/scripts/render_pre_retraining_report.py
python -m unittest discover -s backend/tests -p test_pre_retraining_report.py -v
```

The production export script is `backend/scripts/export_pre_retraining.py`; it was transmitted to the web runtime and executed in memory using Railway SSH. It issues SELECT queries in an explicitly read-only transaction and rolls back. It does not invoke application lifespan, settlement, calibration aggregation, or training.

**Verified:** four focused tests passed. They compare 960 binary-market/score combinations against the application's settlement function; reject push-sensitive/unknown markets; check perfect/wrong/extreme and empty probability scores; reconcile all prediction and publication dispositions, unique picks, gross-loss allocations and paper profit. Supported finished legs in the latest settled-ticket cohort have zero stored-versus-score settlement mismatches. All those ticket publications preceded their leg kickoffs. Unsupported/push-sensitive settlements are not certified by that check.

**Limitations:** this is a timestamp-valid, retrospective diagnosis of recorded forecasts, not a fully certified no-lookahead backtest, causal loss explanation, cash performance audit, or evidence for retraining/promotion success.
