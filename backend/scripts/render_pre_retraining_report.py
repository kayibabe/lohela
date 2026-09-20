"""Render the frozen 2026-09-20 diagnostic evidence as a reviewable report."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
EVIDENCE=ROOT/'docs/evidence/pre_retraining_2026-09-20'
d=json.loads((EVIDENCE/'analysis.json').read_text(encoding='utf-8'))
if d['source_sha256'] != '388db18a93acc3c46760318ac863c884b7112c7618862d5bf13b1785a978a5dd':
    raise ValueError('This narrative is reviewed only for the frozen 20 September snapshot')


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(str(x) for x in row)+' |' for row in rows])


def calibration(groups):
    return table(['Cohort','n','Mean probability','Observed win rate','Gap (pp)','Brier','Log loss'],
        [(k,v['n'],f"{v['mean_p']:.1%}",f"{v['hit_rate']:.1%}",f"{100*v['gap']:+.1f}",
          f"{v['brier']:.4f}",f"{v['log_loss']:.4f}") for k,v in groups.items()])


parts=['''# Loss-attribution and probability-calibration report — before retraining

Follow-up: [selection/calibration reconciliation review](SELECTION_CALIBRATION_REVIEW_2026-09-20.md) adds a matched comparison of the same 61 published picks and qualifies causal claims about selection. The numerical results below retain their original frozen snapshot.

**Decision: keep retraining and promotion on hold pending a controlled data/selection audit and an untouched evaluation design.** This report identifies diagnostic priorities; it does not fit a model, alter weights or thresholds, publish selections, or change production state.

**Verified:** published selections are substantially overconfident in this archive. Their average probability was **79.90%**, versus **50.82%** observed wins: **+29.08 percentage points**. Positive paper returns do not resolve this calibration defect. **Inferred:** totals underestimation and the publication/selection process deserve investigation before general retraining. This is not a causal proof of why an individual match was lost.

Completed: production read-only capture, cohort reconciliation, loss attribution, probability scoring, boundary checks, independent settlement comparison, and report review. Needs attention: quote-record retention, feature/parameter lineage, and later untouched observations. Retraining remains unperformed.

## Evidence and scope
''',f"Capture: **{d['captured_at']}** (07:33:30 Africa/Blantyre on 20 September 2026). Source: Railway production web, PostgreSQL; transaction `REPEATABLE READ READ ONLY`, verified `transaction_read_only=on`, ended with rollback. No database writes or model fitting. Snapshot figures stop at capture time; unresolved fixtures may settle later.\n",
'''Local analysis revision before changes: `f09f23c6d17646b86054f0c7c4dac5f3cc8aae81`. The deployed Git SHA was unavailable from the runtime environment, so local/deployed code identity is not asserted. Results are computed offline from captured records. Database final scores were not independently reconciled to a second results provider. No bookmaker account or cash wagers were inspected.

This production evidence supersedes neither nor combines with the 19 September **local Docker** singles baseline; they are different databases and cohorts.
''',table(['Table','Rows'],d['table_counts'].items()),
'''## Cohort construction and exclusions

- All-market diagnostic: exclude historical `as_of_at` replays; require prediction creation strictly before kickoff and matching market/selection; retain the latest eligible prediction per fixture, market, selection and model version. Only finished fixtures with scores and binary supported outcomes are scored. Quote availability does not determine calibration inclusion.
- Published diagnostic: retain the **first pre-kickoff published snapshot** per fixture/market/selection across all ticket versions, including superseded versions. Require prediction creation no later than publication. This measures what was actually published, not a hypothetical latest prediction. Reserve the first record before looking at outcome. Repeated publication is not an independent trial.
- Ticket P&L: latest ticket version per target date/type, latest settlement version. This matches the daily-ticket accounting convention, but is not an executed-wager ledger. It is a different cohort from first-published unique picks. Four of 19 latest tickets remain unsettled and contribute no settled P&L.
- Exclude draw-no-bet from binary calibration rather than treat pushes as losses or assume unconditional probabilities are conditional on a non-draw. Quarter/integer totals are likewise unsupported by this report.
''',table(['All-prediction disposition','Count'],list(d['exclusions'].items())+[('Scored',d['all_live']['n'])]),
table(['Published-selection disposition','Count'],list(d['published_exclusions'].items())+[('Scored unique picks',d['published']['n'])]),
'''The 1,963 scored all-market records represent only **141 fixtures and five CAT dates (16–20 September)**. The 61 published records represent **39 fixtures**, also five dates. Complementary and overlapping markets are dependent. The date range includes a partially completed 20 September. Pre-kickoff insertion timestamps do not prove that every feature and parameter was constructed without future information.

## Loss attribution

### Ticket ledger: losses and offsets

The 15 settled latest daily tickets contain **3 wins and 12 losses**, staking 15 paper units. Gross losing stakes are **12.00 units**; winning tickets contribute **14.047929 units of net profit**, giving **+2.047929 units net / +13.65% paper ROI**. Winning returns are 17.047929 units. Mean adjusted ticket probability is **43.32%**, versus **20.00%** observed wins. Tickets share fixtures and cannot be treated as 15 independent trials.
''',table(['Tier','Settled','Wins','Net paper units'],[
    (kind,len(rs),sum(r['result']=='WON' for r in rs),f"{sum(r['profit'] for r in rs):+.4f}")
    for kind in sorted({r['type'] for r in d['ticket_rows']})
    for rs in [[r for r in d['ticket_rows'] if r['type']==kind]]]),
'''Allocate each lost ticket's stake equally among its losing legs. This is an additive accounting convention, **not causal attribution or the profit recoverable by removing a leg**. It avoids charging a full ticket loss to every losing selection. Allocations sum to 12.00 units.
''',table(['Failed-leg market','Allocated gross ticket loss','Share'],[
    (k,f'{v:.3f}',f'{v/12:.1%}') for k,v in sorted(d['gross_ticket_loss_allocation'].items(),key=lambda kv:-kv[1])]),
'''Under 2.5 and Under 3.5 together account for **5.75 units / 47.92%** of allocated gross ticket losses. Double chance X2 contributes **2.75 units / 22.92%**. These are the first exposure groups to inspect, not automatic exclusion recommendations.

### Unique published picks

Across 61 picks, **31 won and 30 lost**. A hypothetical one-unit stake at each saved ticket price returns **+2.72 units / +4.46%**. Home-win picks contribute +12.26 units; without that subgroup the other 57 picks total −9.54 units. This concentration matters more than the small positive aggregate return.

All 61 saved prices are usable for snapshot arithmetic, but **45 predictions have no linked source-odds ID in this snapshot**. Their saved bookmaker/provenance fields remain. This does not establish whether a link previously existed. Missing links are a traceability limitation, not proof that those prices were fabricated or unavailable. The stricter linked-quote subset has only **16 picks**, +0.52 paper units / +3.25%; it must not be represented as the full cohort. Source odds themselves were not exported, so the link's presence is not full price verification. No executable-price or realized-return claim is made.
''',table(['Market','Picks','Wins','Losses','Snapshot paper units'],[
    (k,v['n'],v['wins'],v['losses'],f"{v['snapshot_paper_profit']:+.2f}") for k,v in d['published_by_market'].items()]),
'''**23 of 30 losses** were picks assigned at least 70% probability. Only **3 of 30** had odds at least 3.0. None of the losing published picks had Q-score below 70 or recorded edge below 0.05. Therefore low Q-score/thin-edge flags do not explain these observed losses; high recorded Q/edge is not independent evidence of quality. Flags overlap and are descriptive.

## Probability calibration

Brier is mean squared probability error; log loss uses natural logarithms with numerical clipping at 1e-15. Lower is better. Gap is mean predicted probability minus win frequency. ECE is count-weighted absolute gap in fixed 10-percentage-point bins. These are descriptive scores, not guarantees of future performance.
''',calibration({'All latest pre-match binary forecasts':d['all_live'],'First published unique picks':d['published'],
                  'Published picks with p >= 70%':d['published_high_confidence']}),
'''The published cohort expected **48.74 wins** in aggregate but recorded **31**. Its Brier **0.3163** is worse than constant 50% forecasts (**0.2500**), and log loss **0.8619** is worse than the same baseline (**0.6931**). Published ECE is **29.08 pp**. The high-confidence subset is 25/48 wins, despite an average probability of **84.44%**.

The pooled all-market signed gap is only −0.57 pp, but **opposing-market errors cancel**. Its ECE is **6.00 pp**. A small pooled signed gap cannot establish calibration. All-market Brier is 0.2223, versus **0.1996 for raw 1/quoted odds on the same rows**. Raw implied probabilities include bookmaker margin and are not a de-vigged fair-probability benchmark. All-market flat-stake arithmetic loses **96.01 units / −4.89%**; wagering on all overlapping/opposing markets is not a proposed strategy.

### Published reliability bins
''',calibration(d['published_bins']),
'''Bins are left-inclusive/right-exclusive, with 1.0 included in the last bin. The 70–80% bin wins **3/14**, and the 80–90% bin wins **10/21**. The 90–100% bin's 12/13 result is too small to justify raising a threshold after seeing outcomes.

### All-market reliability bins
''',calibration(d['bins']),
'''### Market diagnosis
''',calibration(d['by_market']),
'''**Inferred:** totals lean too low in this window. Over 2.5 occurs in 61.43% of fixtures against 48.26% predicted; BTTS Yes occurs in 59.29% against 47.62%. The corresponding Under/BTTS No forecasts are too high. Both Over/Under 2.5 Brier scores are 0.2646, and BTTS scores are 0.2732, worse than constant 50%. Under 3.5 is 70.38% predicted versus 61.43% observed in the all-market cohort, but **86.73% versus 50.00%** among 16 published picks. This difference motivates investigating selection and prediction timing as well as model calibration; the two cohorts are not matched causal controls.

### Model-version separation
''',calibration(d['by_version']),
'''Version 0.2.1 contributes 1,739 forecasts across 125 fixtures/four dates; 0.3.0 contributes 224 forecasts across 16 fixtures/one date. Published picks are 60 from 0.2.1 and only one from 0.3.0. These are different fixtures/time periods, not a head-to-head experiment. No version superiority or deterioration is established.

### Components on matched rows
''',table(['Component','Available n','Component Brier','Ensemble Brier, same rows'],[
    (k,v['n'],'N/A' if v['component_brier'] is None else f"{v['component_brier']:.4f}",
     'N/A' if v['ensemble_brier_same_rows'] is None else f"{v['ensemble_brier_same_rows']:.4f}")
    for k,v in d['components'].items()]),
'''xG has a better observed Brier than the ensemble on its matched rows; Bayes is worse. This is diagnostic evidence to examine inputs/weights, not permission to tune weights on this same archive. ZINB has no observations in this scored cohort. Component errors can be correlated; counting models does not establish independent corroboration.

### Published day and league sensitivity
''',calibration(d['published_by_day']),calibration(d['published_by_league']),
'''League groups contain only 1–11 picks. Do not blacklist leagues or select a profitable market from these post-hoc comparisons. Full all-market league, date, version-by-market metrics and individual scored rows are in `analysis.json`.

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
''',table(['Prediction ID','Selection ID','Fixture ID','Market','Probability','Final score'],[
    (r['prediction_id'],r['selection_id'],r['match_id'],r['market'],f"{r['p']:.2%}",r['score'])
    for r in sorted((r for r in d['published_rows'] if not r['y']),key=lambda r:-r['p'])[:5]]),
'''## Reproduction and verification

Frozen input: `docs/evidence/pre_retraining_2026-09-20/source.json.gz` (compressed JSON). Derived output: `analysis.json` in the same folder. Input tables contain football research records, not user accounts, credentials or bet-account balances.
''',f"Input SHA-256: `{d['source_sha256']}`.\n",
'''Run from the repository root:

```powershell
python backend/scripts/pre_retraining_report.py docs/evidence/pre_retraining_2026-09-20/source.json.gz docs/evidence/pre_retraining_2026-09-20/analysis.json
python backend/scripts/render_pre_retraining_report.py
python -m unittest discover -s backend/tests -p test_pre_retraining_report.py -v
```

The production export script is `backend/scripts/export_pre_retraining.py`; it was transmitted to the web runtime and executed in memory using Railway SSH. It issues SELECT queries in an explicitly read-only transaction and rolls back. It does not invoke application lifespan, settlement, calibration aggregation, or training.

**Verified:** four focused tests passed. They compare 960 binary-market/score combinations against the application's settlement function; reject push-sensitive/unknown markets; check perfect/wrong/extreme and empty probability scores; reconcile all prediction and publication dispositions, unique picks, gross-loss allocations and paper profit. Supported finished legs in the latest settled-ticket cohort have zero stored-versus-score settlement mismatches. All those ticket publications preceded their leg kickoffs. Unsupported/push-sensitive settlements are not certified by that check.

**Limitations:** this is a timestamp-valid, retrospective diagnosis of recorded forecasts, not a fully certified no-lookahead backtest, causal loss explanation, cash performance audit, or evidence for retraining/promotion success.
''']
(ROOT/'docs/LOSS_ATTRIBUTION_CALIBRATION_2026-09-20.md').write_text('\n\n'.join(parts),encoding='utf-8')
print('Rendered docs/LOSS_ATTRIBUTION_CALIBRATION_2026-09-20.md')
