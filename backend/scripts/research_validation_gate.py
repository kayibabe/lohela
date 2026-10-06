"""Decision-gate checker for a prospective_validation.py report.

This is a research reporting gate only. It never promotes a variant, changes
production configuration, or treats partial windows as complete.
"""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('report', type=Path)
args = parser.parse_args()
report = json.loads(args.report.read_text())
cal = (report.get('window_metrics') or {}).get('calibrate_validate') or {}
test = (report.get('window_metrics') or {}).get('prospective_test') or {}
cal_gap = (cal.get('platt') or {}).get('calibration_gap')
test_gap = (test.get('platt') or {}).get('calibration_gap')

checks = {
    'fit_targets_met': bool((report.get('readiness') or {}).get('fit_targets_met')),
    'calibration_complete': (cal.get('status') == 'COMPLETE'),
    'test_complete': (test.get('status') == 'COMPLETE'),
    'calibration_primary_gap_available': isinstance(cal_gap, (int, float)),
    'test_primary_gap_available': isinstance(test_gap, (int, float)),
}
checks['calibration_gap_target_met'] = checks['calibration_primary_gap_available'] and cal_gap <= 0.15
checks['test_gap_target_met'] = checks['test_primary_gap_available'] and test_gap <= 0.15

result = {
    'status': 'READY_FOR_REVIEW' if all(checks.values()) else 'INCOMPLETE',
    'checks': checks,
    'calibration_gap': cal_gap,
    'test_gap': test_gap,
    'selector_path': report.get('selector_path'),
    'disclaimer': 'Research review gate only; no production promotion or mutation is performed.',
}
print(json.dumps(result, indent=2))
