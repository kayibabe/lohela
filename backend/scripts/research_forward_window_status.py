"""Read-only status check for the frozen prospective calibration protocol.

The script intentionally does not fit a calibrator or alter production state.
It reports whether the disjoint Fit, Calibrate/validate, and Test windows have
enough elapsed time and prediction coverage in an evidence snapshot.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

START = datetime.fromisoformat('2026-09-20T05:33:30.491115+00:00')
FIT_DAYS = 15
CAL_DAYS = 10
TEST_DAYS = 21
TAIL = 0.65

def parse(value):
    return datetime.fromisoformat(value)

def window(rows, start, end):
    return [r for r in rows if r.get('created_at') and start <= parse(r['created_at']) < end]

data = json.loads(Path('/tmp/fresh_source.json').read_text())
predictions = data['tables']['predictions']
captured = parse(data['captured_at'])
fit_end = START + timedelta(days=FIT_DAYS)
cal_end = fit_end + timedelta(days=CAL_DAYS)
test_end = cal_end + timedelta(days=TEST_DAYS)

windows = {
    'fit': (START, fit_end),
    'calibrate_validate': (fit_end, cal_end),
    'prospective_test': (cal_end, test_end),
}
result = {
    'captured_at': data['captured_at'],
    'fit_phase_start': START.isoformat(),
    'window_boundaries': {name: {'start': start.isoformat(), 'end': end.isoformat()}
                          for name, (start, end) in windows.items()},
    'capture_is_before_test_end': captured < test_end,
    'windows': {},
}
for name, (start, end) in windows.items():
    rows = window(predictions, start, end)
    result['windows'][name] = {
        'prediction_count': len(rows),
        'tail_count_model_probability_ge_0_65': sum(
            isinstance(r.get('model_probability'), (int, float))
            and r['model_probability'] >= TAIL for r in rows
        ),
        'distinct_prediction_days': len({parse(r['created_at']).date().isoformat() for r in rows}),
        'min_created_at': min((r['created_at'] for r in rows), default=None),
        'max_created_at': max((r['created_at'] for r in rows), default=None),
    }
result['ready_for_calibrator_fit'] = (
    result['windows']['fit']['prediction_count'] >= 300
    and result['windows']['fit']['tail_count_model_probability_ge_0_65'] >= 60
)
result['ready_for_calibrate_validate'] = captured >= cal_end
result['ready_for_prospective_test'] = captured >= cal_end
result['test_window_complete'] = captured >= test_end
print(json.dumps(result, indent=2))
