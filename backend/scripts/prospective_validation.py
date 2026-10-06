"""Fail-closed prospective validation runner for the frozen ablation protocol.

This script is deliberately offline. It reads an evidence export and writes a
research report only; it never connects to a database, changes weights, builds
tickets, or changes production configuration.

It fits Platt scaling on the frozen Fit window, evaluates it only on disjoint
windows, and reports Q-score A/B/C shadow diagnostics. Full selector-level
ablation is refused unless the source contains variant decision records, since
reconstructing candidate gates and decision-time pools from published tickets
would violate the protocol.
"""
import argparse
import collections
import gzip
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

FIT_START = datetime.fromisoformat('2026-09-20T05:33:30.491115+00:00')
FIT_DAYS, CAL_DAYS, TEST_DAYS = 15, 10, 21
TAIL_THRESHOLD = 0.65
COMPONENTS = ('model_probability', 'value_edge', 'xg_model', 'recent_form',
              'market_consensus', 'odds_stability', 'team_news',
              'league_reliability', 'data_quality')

def dt(value):
    return datetime.fromisoformat(value)

def sigmoid(value):
    if value >= 0:
        e = math.exp(-value)
        return 1 / (1 + e)
    e = math.exp(value)
    return e / (1 + e)

def outcome(selection, match):
    home, away = match.get('home_goals'), match.get('away_goals')
    if match.get('status') != 'FINISHED' or home is None or away is None:
        return None
    total = home + away
    if selection.startswith(('over_', 'under_')):
        threshold = float(selection.split('_', 1)[1])
        return int(total > threshold) if selection.startswith('over_') else int(total < threshold)
    rules = {
        'home_win': home > away, 'draw': home == away, 'away_win': away > home,
        'btts_yes': home > 0 and away > 0, 'btts_no': home == 0 or away == 0,
        'double_chance_1x': home >= away, 'double_chance_x2': away >= home,
        'dnb_home': home > away, 'dnb_away': away > home,
    }
    return int(rules[selection]) if selection in rules else None

def brier(values, labels):
    return sum((p - y) ** 2 for p, y in zip(values, labels)) / len(labels) if labels else None

def metrics(rows):
    if not rows:
        return {'n': 0}
    bins = collections.defaultdict(list)
    for row in rows:
        bins[min(9, int(row['p'] * 10))].append(row)
    n = len(rows)
    gap = sum(row['p'] - row['y'] for row in rows) / n
    return {
        'n': n,
        'fixtures': len({row['match_id'] for row in rows}),
        'days': len({row['day'] for row in rows}),
        'wins': sum(row['y'] for row in rows),
        'hit_rate': sum(row['y'] for row in rows) / n,
        'mean_p': sum(row['p'] for row in rows) / n,
        'calibration_gap': gap,
        'brier': brier([row['p'] for row in rows], [row['y'] for row in rows]),
        'ece': sum(len(group) / n * abs(sum(r['p'] - r['y'] for r in group) / len(group))
                  for group in bins.values()),
        'bins': {str(k): {'n': len(group), 'mean_p': sum(r['p'] for r in group) / len(group),
                          'hit_rate': sum(r['y'] for r in group) / len(group)}
                 for k, group in sorted(bins.items())},
    }

def fit_platt(rows):
    xs = [math.log(row['p'] / (1 - row['p'])) for row in rows]
    ys = [row['y'] for row in rows]
    if len(rows) < 2 or len(set(ys)) < 2:
        raise ValueError('Fit tail needs both outcome classes')
    a, b = 1.0, 0.0
    for _ in range(100):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in zip(xs, ys):
            q = sigmoid(a * x + b)
            weight = q * (1 - q)
            g0 += (q - y) * x; g1 += q - y
            h00 += weight * x * x; h01 += weight * x; h11 += weight
        determinant = h00 * h11 - h01 * h01
        if abs(determinant) < 1e-12:
            break
        da = (h11 * g0 - h01 * g1) / determinant
        db = (-h01 * g0 + h00 * g1) / determinant
        a -= da; b -= db
        if max(abs(da), abs(db)) < 1e-9:
            break
    return a, b

def component_scores(prediction):
    weights = prediction.get('q_component_weights') or {}
    values = {}
    for name in COMPONENTS:
        weight = float(weights.get(name, 0.0) or 0.0)
        contribution = prediction.get('q_' + name)
        values[name] = 0.0 if not weight else max(0.0, min(1.0, float(contribution or 0.0) / weight))
    return values, weights

def q_score(prediction, variant, calibrated_p=None):
    components, base = component_scores(prediction)
    weights = dict(base)
    if variant == 'q_ablation_a':
        weights['market_consensus'] = 0.0
        weights['league_reliability'] = 0.0
        scale = 100.0 / sum(weights.values())
        weights = {key: value * scale for key, value in weights.items()}
    elif variant == 'q_ablation_b':
        old = weights['model_probability']
        weights['model_probability'] = old / 2.0
        scale = (100.0 - weights['model_probability']) / (100.0 - old)
        weights = {key: (value if key == 'model_probability' else value * scale)
                   for key, value in weights.items()}
    elif variant == 'calibration_aware':
        if calibrated_p is None:
            raise ValueError('calibration-aware score requires calibrated probability')
        components['model_probability'] = calibrated_p
        implied = prediction.get('source_implied_probability')
        components['value_edge'] = (max(0.0, min(1.0, (calibrated_p - implied) / 0.20))
                                    if implied is not None and calibrated_p > implied else 0.0)
    return max(0.0, min(100.0, sum(components[name] * weights[name] for name in COMPONENTS)))

def load(path):
    raw = path.read_bytes()
    payload = json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw)
    return raw, payload

def prepare_rows(payload):
    tables = payload['tables']
    matches = {row['id']: row for row in tables['matches']}
    latest = {}
    excluded = collections.Counter()
    for prediction in sorted(tables['predictions'], key=lambda row: (row.get('created_at', ''), row['id'])):
        match = matches.get(prediction.get('match_id'))
        if not match or prediction.get('as_of_at') is not None:
            excluded['historical_or_missing_match'] += 1; continue
        if not prediction.get('created_at') or dt(prediction['created_at']) >= dt(match['kickoff_at']):
            excluded['not_strictly_pre_kickoff'] += 1; continue
        probability = prediction.get('model_probability')
        if not isinstance(probability, (int, float)) or not 0 < probability < 1:
            excluded['invalid_probability'] += 1; continue
        key = (prediction['match_id'], prediction.get('market'), prediction.get('selection'), prediction.get('model_version'))
        latest[key] = prediction
    rows = []
    for prediction in latest.values():
        label = outcome(prediction.get('selection'), matches[prediction['match_id']])
        if label is None:
            continue
        created = dt(prediction['created_at'])
        rows.append({'prediction': prediction, 'match_id': prediction['match_id'],
                     'day': created.date().isoformat(), 'created_at': created,
                     'p': float(prediction['model_probability']), 'y': label})
    return rows, excluded

def selector_path_status(payload):
    """Classify whether Q-score ablations can affect the captured selector."""
    generations = payload.get('tables', {}).get('ticket_generations') or []
    if not generations:
        return {'status': 'NOT_VERIFIED',
                'reason': 'Evidence source has no ticket_generations table'}
    modes = {str((row.get('config_snapshot') or {}).get('pricing')).lower()
             for row in generations if (row.get('config_snapshot') or {}).get('pricing')}
    if modes == {'market'}:
        specs = []
        for row in generations:
            for spec in (row.get('config_snapshot') or {}).get('ticket_specs') or []:
                if spec.get('min_q_score') is not None:
                    specs.append(float(spec['min_q_score']))
        return {'status': 'NOT_APPLICABLE_CURRENT_SELECTOR',
                'pricing_modes': sorted(modes),
                'observed_min_q_scores': sorted(set(specs)),
                'reason': 'All captured ticket generations use market pricing; Q-score and model-edge gates are not selector gates.'}
    return {'status': 'REQUIRES_VARIANT_DECISION_RECORDS',
            'pricing_modes': sorted(modes),
            'reason': 'Non-market selector records exist, but per-variant decisions and gate traces are not present.'}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    raw, payload = load(args.source)
    captured = dt(payload['captured_at'])
    fit_end = FIT_START + timedelta(days=FIT_DAYS)
    cal_end = fit_end + timedelta(days=CAL_DAYS)
    test_end = cal_end + timedelta(days=TEST_DAYS)
    rows, excluded = prepare_rows(payload)
    fit = [r for r in rows if FIT_START <= r['created_at'] < fit_end]
    calibrate = [r for r in rows if fit_end <= r['created_at'] < cal_end]
    test = [r for r in rows if cal_end <= r['created_at'] < test_end]
    fit_tail = [r for r in fit if r['p'] >= TAIL_THRESHOLD]
    result = {
        'scope': 'RESEARCH ONLY; no production state or deployment mutation',
        'source_sha256': hashlib.sha256(raw).hexdigest(),
        'captured_at': payload['captured_at'],
        'windows': {'fit': [FIT_START.isoformat(), fit_end.isoformat()],
                    'calibrate_validate': [fit_end.isoformat(), cal_end.isoformat()],
                    'prospective_test': [cal_end.isoformat(), test_end.isoformat()]},
        'excluded_prediction_counts': dict(excluded),
        'window_counts': {name: {'labeled_rows': len(value), 'distinct_days': len({r['day'] for r in value})}
                          for name, value in (('fit', fit), ('calibrate_validate', calibrate), ('prospective_test', test))},
        'readiness': {'fit_targets_met': len(fit) >= 300 and len(fit_tail) >= 60,
                      'calibrate_validate_complete': captured >= cal_end,
                      'prospective_test_complete': captured >= test_end},
        'selector_path': selector_path_status(payload),
        'selector_ablation': {
            'status': 'NOT_RUN',
            'reason': 'Source contains production selections only; it has no frozen per-variant decision records or full gate traces. Reconstructing selector decisions would not satisfy the matched protocol.'
        },
    }
    if len(fit) < 300 or len(fit_tail) < 60 or len([r for r in fit_tail if r['y'] == 1]) == 0 or len({r['y'] for r in fit_tail}) < 2:
        result['calibration_fit'] = {'status': 'NOT_FIT', 'reason': 'Fit tail lacks the required size or class diversity'}
    else:
        a, b = fit_platt(fit_tail)
        result['calibration_fit'] = {'status': 'FIT_ONLY', 'tail_rows': len(fit_tail),
                                     'positive': sum(r['y'] for r in fit_tail),
                                     'negative': len(fit_tail) - sum(r['y'] for r in fit_tail),
                                     'platt_a': a, 'platt_b': b,
                                     'fit_metrics_raw': metrics(fit_tail),
                                     'fit_metrics_platt_in_sample': metrics([
                                         dict(r, p=sigmoid(a * math.log(r['p'] / (1 - r['p'])) + b))
                                         for r in fit_tail])}
        for name, window in (('calibrate_validate', calibrate), ('prospective_test', test)):
            if not window:
                result.setdefault('window_metrics', {})[name] = {'status': 'NOT_AVAILABLE'}
                continue
            calibrated = [dict(r, p=sigmoid(a * math.log(r['p'] / (1 - r['p'])) + b)) for r in window]
            result.setdefault('window_metrics', {})[name] = {
                'status': 'COMPLETE' if (captured >= cal_end if name == 'calibrate_validate' else captured >= test_end) else 'PARTIAL_NOT_DECISION_BEARING',
                'raw': metrics(window), 'platt': metrics(calibrated),
            }
    # Shadow score diagnostics are intentionally separate from selector results.
    if test:
        variants = ('control', 'q_ablation_a', 'q_ablation_b')
        scores = {}
        for name in variants:
            scored = [dict(row, p=q_score(row['prediction'], name)) for row in test]
            scores[name] = {'score_mean': sum(r['p'] for r in scored) / len(scored),
                            'score_ge_80': sum(r['p'] >= 80 for r in scored),
                            'score_ge_85': sum(r['p'] >= 85 for r in scored)}
        result['test_shadow_q_scores'] = scores
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
