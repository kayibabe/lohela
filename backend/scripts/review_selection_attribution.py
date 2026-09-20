"""Frozen-evidence matched comparison; no policy changes or model fitting."""
import gzip
import hashlib
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path

from pre_retraining_report import dt, metrics

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'docs/evidence/pre_retraining_2026-09-20'


def main():
    raw = (FOLDER / 'source.json.gz').read_bytes()
    source = json.loads(gzip.decompress(raw))['tables']
    analysis = json.loads((FOLDER / 'analysis.json').read_text())
    assert hashlib.sha256(raw).hexdigest() == analysis['source_sha256']
    predictions = {p['id']: p for p in source['predictions']}
    matches = {m['id']: m for m in source['matches']}
    published = analysis['published_rows']
    latest_by_key = {(r['match_id'], r['market'], r['version']): r for r in analysis['rows']}
    pairs = []
    latest_matched = []
    missing = []
    snapshot_discrepancies = []
    statuses = Counter()
    same_run_ids = {predictions[r['prediction_id']]['model_run_id'] for r in published}
    for row in published:
        p = predictions[row['prediction_id']]
        statuses.update(f'{k}={v}' for k, v in p['q_component_status'].items())
        if abs(row['p']-p['model_probability']) > 1e-10:
            snapshot_discrepancies.append(row['prediction_id'])
        newer = latest_by_key.get((row['match_id'],row['market'],row['version']))
        if newer is None:
            missing.append(row['prediction_id'])
            continue
        assert row['y'] == newer['y']
        assert dt(predictions[newer['prediction_id']]['created_at']) >= dt(p['created_at'])
        latest_matched.append(newer)
        pairs.append(dict(match_id=row['match_id'],market=row['market'],version=row['version'],
                          first_prediction_id=p['id'],latest_prediction_id=newer['prediction_id'],
                          first_p=row['p'],latest_p=newer['p'],y=row['y']))
    # All binary, settled, pre-match forecasts from the same model runs as published picks.
    # Keep run repetitions explicit: this is a sensitivity cohort, not independent trials.
    same_run = []
    from pre_retraining_report import outcome
    for p in predictions.values():
        m=matches[p['match_id']]
        if (p['model_run_id'] not in same_run_ids or p['as_of_at'] is not None
                or not dt(p['created_at']) < dt(m['kickoff_at'])
                or m['status'] != 'FINISHED' or m['home_goals'] is None or m['away_goals'] is None
                or p['market'] != p['selection']):
            continue
        try:
            y=outcome(p['market'],m['home_goals'],m['away_goals'])
        except ValueError:
            continue
        same_run.append(dict(p=p['model_probability'],y=y,match_id=m['id'],
                             day=(dt(m['kickoff_at'])+timedelta(hours=2)).date().isoformat(),
                             odds=p['source_decimal_odds'],quote_valid=False))
    first = metrics(published)
    last = metrics(latest_matched)
    result = dict(source_sha256=analysis['source_sha256'],
                  published_first=first, matched_latest_same_version=last,
                  matched_n=len(pairs),missing=missing,snapshot_probability_discrepancies=snapshot_discrepancies,
                  identical_prediction_ids=sum(p['first_prediction_id']==p['latest_prediction_id'] for p in pairs),
                  probability_revisions=sum(abs(p['first_p']-p['latest_p'])>1e-10 for p in pairs),
                  mean_probability_revision=sum(p['latest_p']-p['first_p'] for p in pairs)/len(pairs),
                  all_latest_p_ge_70=metrics([r for r in analysis['rows'] if r['p']>=.7]),
                  same_run_sensitivity=metrics(same_run),same_run_count=len(same_run_ids),
                  published_learning_profile_count=sum(predictions[r['prediction_id']]['learning_profile_id'] is not None for r in published),
                  published_component_point_ranges={k:[min(predictions[r['prediction_id']][k] for r in published),
                      max(predictions[r['prediction_id']][k] for r in published)] for k in
                      ('q_model_probability','q_value_edge','q_recent_form','q_market_consensus',
                       'q_odds_stability','q_team_news','q_league_reliability','q_data_quality')},
                  published_q_component_status_counts=dict(statuses),pairs=pairs)
    assert not missing
    assert not snapshot_discrepancies
    assert abs(first['gap']-last['gap']+result['mean_probability_revision'])<1e-10
    # Deterministic counterexample to "thresholding always creates overconfidence".
    calibrated = [dict(p=p,y=int(i < wins),match_id=f'{p}-{i}',day='synthetic',odds=2,quote_valid=False)
                  for p,wins in [(0.2,20),(0.8,80)] for i in range(100)]
    result['synthetic_threshold_counterexample'] = {
        'all':metrics(calibrated),'p_ge_70':metrics([r for r in calibrated if r['p']>=.7])}
    assert abs(result['synthetic_threshold_counterexample']['p_ge_70']['gap'])<1e-12
    (FOLDER/'selection_review.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('pairs','published_q_component_status_counts')},indent=2))
    print('Q status:', dict(statuses))


if __name__ == '__main__':
    main()
