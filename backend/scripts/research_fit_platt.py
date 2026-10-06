"""Fit the protocol's Platt calibrator on the disjoint Fit window only.

This is a research diagnostic. It never writes to the database or production
configuration, and its in-sample metrics are explicitly not validation.
"""
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

START = datetime.fromisoformat('2026-09-20T05:33:30.491115+00:00')
END = START + timedelta(days=15)
TAIL = 0.65

def dt(value):
    return datetime.fromisoformat(value)

def outcome(selection, match):
    hg, ag = match.get('home_goals'), match.get('away_goals')
    if hg is None or ag is None or match.get('status') != 'FINISHED':
        return None
    total = hg + ag
    if selection.startswith('over_'):
        return int(total > float(selection.split('_')[1]))
    if selection.startswith('under_'):
        return int(total < float(selection.split('_')[1]))
    if selection == 'home_win': return int(hg > ag)
    if selection == 'draw': return int(hg == ag)
    if selection == 'away_win': return int(ag > hg)
    if selection == 'btts_yes': return int(hg > 0 and ag > 0)
    if selection == 'btts_no': return int(not (hg > 0 and ag > 0))
    if selection == 'double_chance_1x': return int(hg >= ag)
    if selection == 'double_chance_x2': return int(ag >= hg)
    if selection == 'dnb_home': return int(hg > ag)
    if selection == 'dnb_away': return int(ag > hg)
    return None

def sigmoid(z):
    if z >= 0:
        e = math.exp(-z)
        return 1 / (1 + e)
    e = math.exp(z)
    return e / (1 + e)

def fit(xs, ys):
    a, b = 1.0, 0.0
    for _ in range(100):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in zip(xs, ys):
            q = sigmoid(a * x + b)
            e = q - y
            g0 += e * x; g1 += e
            h00 += q * (1 - q) * x * x
            h01 += q * (1 - q) * x
            h11 += q * (1 - q)
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12: break
        da = (h11 * g0 - h01 * g1) / det
        db = (-h01 * g0 + h00 * g1) / det
        a -= da; b -= db
        if max(abs(da), abs(db)) < 1e-9: break
    return a, b

def brier(values, ys):
    return sum((p - y) ** 2 for p, y in zip(values, ys)) / len(ys)

data = json.loads(Path('/tmp/fresh_source.json').read_text())
matches = {m['id']: m for m in data['tables']['matches']}
rows = []
for p in data['tables']['predictions']:
    if not p.get('created_at') or not (START <= dt(p['created_at']) < END):
        continue
    prob = p.get('model_probability')
    if not isinstance(prob, (int, float)) or not (TAIL <= prob < 1):
        continue
    y = outcome(p.get('selection'), matches.get(p.get('match_id'), {}))
    if y is not None and 0 < prob < 1:
        rows.append((math.log(prob / (1 - prob)), prob, y))

xs = [r[0] for r in rows]
raw = [r[1] for r in rows]
ys = [r[2] for r in rows]
result = {'fit_window_start': START.isoformat(), 'fit_window_end': END.isoformat(),
          'tail_threshold': TAIL, 'eligible_tail_predictions': len(xs),
          'positive_outcomes': sum(ys), 'negative_outcomes': len(ys) - sum(ys)}
if xs and len(set(ys)) > 1:
    a, b = fit(xs, ys)
    calibrated = [sigmoid(a * x + b) for x in xs]
    result.update({'platt_a': a, 'platt_b': b,
                   'in_sample_raw_brier': brier(raw, ys),
                   'in_sample_platt_brier': brier(calibrated, ys),
                   'warning': 'in-sample diagnostic only; do not use for promotion'})
else:
    result['warning'] = 'insufficient labeled class diversity for fit'
print(json.dumps(result, indent=2))
