"""Frozen ablation retrospective diagnostic. No training, database access,
gate/weight changes, or production behavior change.

Scope: docs/ABLATION_SCOPING_2026-09-20.md, deliverable 1 of section 9
("A frozen-variant runner script ... that replays control + ablations
against fixed input snapshots"), restricted to the retrospective
(diagnostic) half of the experiment. The prospective, decision-bearing
half (section 5) is explicitly out of scope for this script: it needs a
forward window on fixtures this policy has not seen, plus the three open
items in section 7 (power/window sizing, calibrator fit/calibrate split,
Q-ablation B's exact weight) resolved first.

READ THIS BEFORE READING THE OUTPUT
====================================
1. Diagnostic only. Per scoping section 5, replaying an ablated policy
   against the archive that was used to DISCOVER the calibration problem
   is not evidence the ablation will do better going forward. This report
   must not be used to declare a winner or inform a promotion decision
   (scoping section 8).
2. Republished-picks-only, not full candidate-pool replay. Per scoping
   section 7 item 1 (citing the prior review, line 58): the snapshot
   lacks model-run/ticket-generation configuration and historical gate
   inputs, so the candidates that were gated OUT under control are not
   recoverable. This script can only re-score the 61 picks that WERE
   published (and, for probability-only recalibration, the 466-row "all
   live" pre-match forecast set). It cannot simulate which additional
   candidates an ablated Q-score policy would have newly admitted, so
   "gate crossing" below means "of the picks that were published, how
   many still clear the gate under the ablated weights" -- not full
   re-selection.
3. Relaxation state is a bool, not a step. `accumulator_tickets.relaxed_tier`
   (backend/app/models/research.py:234) only records whether the bounded
   relaxation ladder (accumulator_builder.py _RELAXATION_STEPS) applied at
   all, not which of its 3 steps, so the exact effective min_q_score for a
   relaxed ticket cannot be reconstructed. Gate-crossing counts are
   reported as [full-strength bound, maximally-relaxed-floor bound] rather
   than a single number for any row whose ticket was relaxed.
4. The calibration-aware variant reuses the diagnostic archive for both
   fitting and evaluation (out-of-fold by day, not by held-out period),
   because no disjoint fit/calibrate/test split exists yet (open item,
   scoping section 7.3, section 4). This is exactly the kind of
   same-sample tuning the scoping doc warns cannot be used as evidence of
   anything (section 1, "Retrospective vs. prospective") -- it is included
   only to show what an isotonic recalibration would have looked like on
   this data, not as a validated variant.

Usage: python backend/scripts/ablation_diagnostic.py
(no args -- hardcoded to the same frozen evidence folder as
review_selection_attribution.py, for the same reproducibility reason).
"""
import gzip
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'docs/evidence/pre_retraining_2026-09-20'
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pre_retraining_report import mean, metrics, bootstrap  # noqa: E402

COMPONENT_KEYS = (
    'model_probability', 'value_edge', 'xg_model', 'recent_form',
    'market_consensus', 'odds_stability', 'team_news', 'league_reliability',
    'data_quality',
)
Q_FIELD = {name: f'q_{name}' for name in COMPONENT_KEYS}

# Mirror of Q_SCORE_WEIGHTS in backend/app/services/models/ensemble.py:38-48,
# used only as a fallback if that module cannot be imported (it pulls in
# app.models, which needs a configured environment). Verified against the
# live source below when import succeeds, and against the weights recorded
# per-prediction in the archive itself either way -- the archive's own
# q_component_weights field is the source of truth used for every
# computation in this script, not this constant.
FALLBACK_CONTROL_WEIGHTS = {
    'model_probability': 25.0, 'value_edge': 20.0, 'xg_model': 15.0,
    'recent_form': 10.0, 'market_consensus': 10.0, 'odds_stability': 5.0,
    'team_news': 5.0, 'league_reliability': 5.0, 'data_quality': 5.0,
}

# Mirror of TICKET_SPECS in backend/app/services/accumulator_builder.py:55-59
# (ticket_type -> min_q_score at full strength) and the relaxation floor at
# accumulator_builder.py:75. Same fallback/verification approach as above.
FULL_STRENGTH_MIN_Q = {'SAFE': 85.0, 'BALANCED': 80.0, 'AGGRESSIVE': 75.0, 'BEST_VALUE': 85.0}
RELAXATION_FLOOR_MIN_Q = 60.0


def verify_constants_against_source():
    """Best-effort cross-check of the mirrored constants above against the
    live source modules. Not fatal if it can't run (see module docstring
    on why a DB-configured environment isn't assumed) -- prints a clear
    PASS/FAIL/SKIPPED line either way so the report's constants are never
    silently trusted."""
    try:
        sys.path.insert(0, str(ROOT / 'backend'))
        from app.services.models.ensemble import Q_SCORE_WEIGHTS  # type: ignore
        assert Q_SCORE_WEIGHTS == FALLBACK_CONTROL_WEIGHTS
        print('[verify] Q_SCORE_WEIGHTS matches ensemble.py: PASS')
    except AssertionError:
        print('[verify] Q_SCORE_WEIGHTS DOES NOT MATCH ensemble.py -- FAIL, mirrored constant is stale')
        raise
    except Exception as e:  # pragma: no cover - environment-dependent
        print(f'[verify] could not import ensemble.py to cross-check weights ({e.__class__.__name__}): SKIPPED')
    try:
        from app.services.accumulator_builder import TICKET_SPECS, _RELAXATION_FLOOR_Q_SCORE  # type: ignore
        live = {spec.ticket_type.value.upper(): spec.min_q_score for spec in TICKET_SPECS}
        assert live == FULL_STRENGTH_MIN_Q
        assert _RELAXATION_FLOOR_Q_SCORE == RELAXATION_FLOOR_MIN_Q
        print('[verify] TICKET_SPECS min_q_score / relaxation floor matches accumulator_builder.py: PASS')
    except AssertionError:
        print('[verify] TICKET_SPECS DOES NOT MATCH accumulator_builder.py -- FAIL, mirrored constant is stale')
        raise
    except Exception as e:  # pragma: no cover - environment-dependent
        print(f'[verify] could not import accumulator_builder.py to cross-check specs ({e.__class__.__name__}): SKIPPED')


def ablation_a_weights(base):
    """Zero market_consensus + league_reliability, renormalize the rest to 100."""
    dropped = base['market_consensus'] + base['league_reliability']
    remaining = 100.0 - dropped
    scale = 100.0 / remaining
    return {k: (0.0 if k in ('market_consensus', 'league_reliability') else v * scale)
            for k, v in base.items()}


def ablation_b_weights(base, new_model_probability_weight=15.0):
    """Rescale model_probability down (default 25 -> 15), redistribute the
    difference proportionally across the other 8 components (not zeroed --
    see scoping section 2, Q-ablation B: edge/EV are themselves derived
    from p, so a p-free ranking is not a validated baseline)."""
    others_sum = 100.0 - base['model_probability']
    new_others_sum = 100.0 - new_model_probability_weight
    scale = new_others_sum / others_sum
    return {k: (new_model_probability_weight if k == 'model_probability' else v * scale)
            for k, v in base.items()}


def component_scores(prediction):
    """Raw 0-1 component scores c_i, recovered from the stored weighted
    point contributions and that prediction's own recorded weights
    (q_component_weights) -- not the global constant, so this is correct
    even if weights ever varied across model versions (checked: they did
    not, across all 61 published rows, but this does not assume that)."""
    weights = prediction['q_component_weights']
    out = {}
    for name in COMPONENT_KEYS:
        w = weights[name]
        val = prediction[Q_FIELD[name]] or 0.0
        out[name] = 0.0 if w == 0 else max(0.0, min(1.0, val / w))
    return out, weights


def rescored_q(prediction, weights):
    c, _ = component_scores(prediction)
    return max(0.0, min(100.0, sum(c[name] * weights[name] for name in COMPONENT_KEYS)))


def gate_bounds(ticket_type, relaxed):
    full = FULL_STRENGTH_MIN_Q[ticket_type]
    if not relaxed:
        return full, full
    return RELAXATION_FLOOR_MIN_Q, full  # true relaxed threshold is unrecoverable (see docstring point 3)


def day_clustered_isotonic_oof(rows):
    """Out-of-fold isotonic recalibration of model_probability, held out by
    day (never fit on the day being predicted). Diagnostic only -- see
    module docstring point 4 on why this is not a validated calibrator."""
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        return None, 'scikit-learn not available in this environment -- skipped'
    by_day = {}
    for r in rows:
        by_day.setdefault(r['day'], []).append(r)
    days = sorted(by_day)
    if len(days) < 3:
        return None, f'only {len(days)} distinct days -- too few for out-of-fold calibration, skipped'
    calibrated = []
    for held_out in days:
        train = [r for d in days if d != held_out for r in by_day[d]]
        if len(train) < 10:
            calibrated.extend(dict(r, p_calibrated=r['p']) for r in by_day[held_out])
            continue
        iso = IsotonicRegression(out_of_bounds='clip', y_min=0.0, y_max=1.0)
        iso.fit([r['p'] for r in train], [r['y'] for r in train])
        for r in by_day[held_out]:
            calibrated.append(dict(r, p_calibrated=float(iso.predict([r['p']])[0])))
    return calibrated, None


def main():
    verify_constants_against_source()

    raw = (FOLDER / 'source.json.gz').read_bytes()
    source = json.loads(gzip.decompress(raw))['tables']
    analysis = json.loads((FOLDER / 'analysis.json').read_text())
    source_sha256 = hashlib.sha256(raw).hexdigest()
    assert source_sha256 == analysis['source_sha256'], 'archive does not match the frozen snapshot analysis.json was built from'

    predictions = {p['id']: p for p in source['predictions']}
    tickets = {t['id']: t for t in source['accumulator_tickets']}
    all_live = analysis['rows']
    pub = analysis['published_rows']

    control_weights = predictions[pub[0]['prediction_id']]['q_component_weights']
    assert all(predictions[r['prediction_id']]['q_component_weights'] == control_weights for r in pub), \
        'published rows use different Q-score weight sets -- per-row weights required, cannot use one constant'

    weight_variants = {
        'control': control_weights,
        'q_ablation_a': ablation_a_weights(control_weights),
        'q_ablation_b': ablation_b_weights(control_weights),
    }

    per_row = []
    for r in pub:
        p = predictions[r['prediction_id']]
        t = tickets[r['ticket_id']]
        lo, hi = gate_bounds(t['ticket_type'], t['relaxed_tier'])
        entry = dict(prediction_id=r['prediction_id'], match_id=r['match_id'], market=r['market'],
                     day=r['day'], p=r['p'], y=r['y'], ticket_type=t['ticket_type'],
                     relaxed=t['relaxed_tier'], gate_lower_bound=lo, gate_upper_bound=hi,
                     control_q=r['q'])
        for name, weights in weight_variants.items():
            entry[f'{name}_q'] = rescored_q(p, weights)
        per_row.append(entry)

    variant_reports = {}
    for name in ('control', 'q_ablation_a', 'q_ablation_b'):
        qs = [row[f'{name}_q'] for row in per_row]
        shift = [q - row['control_q'] for q, row in zip(qs, per_row)]
        clears_full = [row for row, q in zip(per_row, qs) if q >= row['gate_upper_bound']]
        clears_floor = [row for row, q in zip(per_row, qs) if q >= row['gate_lower_bound']]
        dropped_full_strength = [row['prediction_id'] for row, q in zip(per_row, qs)
                                  if q < row['gate_upper_bound']]
        variant_reports[name] = dict(
            weights=weight_variants[name],
            q_shift_mean=mean(shift), q_shift_min=min(shift), q_shift_max=max(shift),
            n_still_clear_full_strength_gate=len(clears_full),
            n_still_clear_relaxed_floor_gate=len(clears_floor),
            n_published=len(per_row),
            dropped_below_full_strength_gate_prediction_ids=dropped_full_strength,
            metrics_still_clearing_full_strength_gate=metrics(
                [dict(p=row['p'], y=row['y'], match_id=row['match_id'], day=row['day'],
                      odds=next(pr['odds'] for pr in pub if pr['prediction_id'] == row['prediction_id']),
                      quote_valid=next(pr['quote_valid'] for pr in pub if pr['prediction_id'] == row['prediction_id']))
                 for row in clears_full]),
        )

    calibrated_all_live, calib_skip_reason = day_clustered_isotonic_oof(all_live)
    calibration_report = {'skipped_reason': calib_skip_reason}
    if calibrated_all_live is not None:
        recalibrated_rows = [dict(r, p=r['p_calibrated']) for r in calibrated_all_live]
        calibration_report.update(
            method='out-of-fold isotonic regression on model_probability, held out by day',
            metrics_control_all_live=metrics(all_live),
            metrics_recalibrated_all_live=metrics(recalibrated_rows),
            bootstrap_gap_control_all_live=bootstrap(all_live, 'day'),
            bootstrap_gap_recalibrated_all_live=bootstrap(recalibrated_rows, 'day'),
        )
        by_pred_id = {r['prediction_id']: r for r in calibrated_all_live if 'prediction_id' in r}
        recal_pub = []
        missing_from_calibration = 0
        for r in pub:
            cal = by_pred_id.get(r['prediction_id'])
            if cal is None:
                missing_from_calibration += 1
                continue
            recal_pub.append(dict(r, p=cal['p_calibrated']))
        calibration_report.update(
            n_published_missing_from_all_live_calibration_set=missing_from_calibration,
            metrics_control_published=metrics(pub),
            metrics_recalibrated_published=metrics(recal_pub) if recal_pub else None,
        )

    report = dict(
        scope='RETROSPECTIVE DIAGNOSTIC ONLY -- non-decision-bearing, see docs/ABLATION_SCOPING_2026-09-20.md',
        source_sha256=source_sha256,
        n_published=len(pub), n_all_live=len(all_live),
        limitations=[
            'Republished-picks-only: cannot simulate candidates gated out under control (data gap, scoping section 7 item 1).',
            'Relaxed-ticket gate thresholds are bounded [floor, full-strength], not exact (relaxed_tier is a bool, not a step).',
            'Calibration-aware variant is fit and evaluated on the same archive (out-of-fold by day only) -- not a disjoint fit/calibrate/test split, so it is not validated evidence per scoping section 1 and section 4.',
        ],
        variants=variant_reports,
        calibration_aware=calibration_report,
        per_row=per_row,
    )

    out_path = FOLDER / 'ablation_diagnostic.json'
    out_path.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    summary = {k: v for k, v in report.items() if k not in ('per_row',)}
    print(json.dumps(summary, indent=2, default=str))
    print(f'\nFull report written to {out_path}')


if __name__ == '__main__':
    main()
