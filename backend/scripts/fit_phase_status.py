"""Fit-phase progress check for the calibrator described in
docs/ABLATION_OPEN_ITEMS_PROPOSAL_2026-09-20.md item 3 (folded into
docs/ABLATION_SCOPING_2026-09-20.md sec 4 and 7 item 3).

This does not fit anything and does not touch production. It answers one
question against a fresh read-only export: has the Fit phase collected
enough NEW data (created strictly after the diagnostic snapshot's freeze
point, so none of it can overlap the archive the calibration problem was
diagnosed from) to move on to the Calibrate/validate phase.

Usage:
    python backend/scripts/export_pre_retraining.py > /tmp/fresh_export.txt
    # extract the EVIDENCE_JSON= line into fresh_source.json (or .json.gz)
    python backend/scripts/fit_phase_status.py path/to/fresh_source.json[.gz]

With no argument, checks the existing frozen diagnostic snapshot as a
sanity check -- it MUST report zero Fit-phase predictions, since that
snapshot's own capture instant is the Fit phase's start boundary.
"""
import argparse
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'docs/evidence/pre_retraining_2026-09-20'

# The Fit phase's start boundary: the exact instant the diagnostic snapshot
# was captured (analysis.json / source.json.gz's own `captured_at`). Using
# this rather than a rounded date guarantees zero overlap with the archive
# already used to diagnose the calibration problem (scoping sec 1, sec 4).
FIT_PHASE_START = datetime.fromisoformat('2026-09-20T05:33:30.491115+00:00')
FIT_PHASE_TARGET_DAYS = 15
FIT_PHASE_TARGET_TOTAL = 300
FIT_PHASE_TARGET_TAIL = 60  # predictions with model_probability >= TAIL_THRESHOLD
TAIL_THRESHOLD = 0.65


def dt(s):
    return datetime.fromisoformat(s)


def load(path: Path):
    raw = path.read_bytes()
    data = json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw)
    return data


def analyze(data):
    preds = data['tables']['predictions']
    fit_window = [p for p in preds if p.get('created_at') and dt(p['created_at']) >= FIT_PHASE_START]
    tail = [p for p in fit_window
            if isinstance(p.get('model_probability'), (int, float)) and p['model_probability'] >= TAIL_THRESHOLD]
    days = {dt(p['created_at']).date().isoformat() for p in fit_window}
    now = datetime.now(timezone.utc)
    elapsed_days = (now - FIT_PHASE_START).total_seconds() / 86400
    return dict(
        fit_phase_start=FIT_PHASE_START.isoformat(),
        captured_at=data.get('captured_at'),
        elapsed_days=round(elapsed_days, 2),
        target_days=FIT_PHASE_TARGET_DAYS,
        n_predictions_in_fit_window=len(fit_window),
        n_predictions_in_tail=len(tail),
        distinct_days_with_data=len(days),
        target_total=FIT_PHASE_TARGET_TOTAL,
        target_tail=FIT_PHASE_TARGET_TAIL,
        meets_total_target=len(fit_window) >= FIT_PHASE_TARGET_TOTAL,
        meets_tail_target=len(tail) >= FIT_PHASE_TARGET_TAIL,
        meets_time_target=elapsed_days >= FIT_PHASE_TARGET_DAYS,
        ready_for_calibrate_phase=(len(fit_window) >= FIT_PHASE_TARGET_TOTAL
                                    and len(tail) >= FIT_PHASE_TARGET_TAIL
                                    and elapsed_days >= FIT_PHASE_TARGET_DAYS),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path, nargs='?',
                         default=FOLDER / 'source.json.gz',
                         help='fresh export to check; defaults to the frozen diagnostic snapshot (sanity check only)')
    args = parser.parse_args()
    data = load(args.source)
    result = analyze(data)
    if args.source == FOLDER / 'source.json.gz':
        assert result['n_predictions_in_fit_window'] == 0, \
            'sanity check failed: the frozen diagnostic snapshot must show zero Fit-phase predictions'
        print('[sanity check] frozen diagnostic snapshot correctly shows 0 Fit-phase predictions (expected).')
        print('Re-run this script against a FRESH export (see module docstring) to check real progress.\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
