"""Independent scoring and evidence reconciliation checks (standard library only)."""
import ast
import enum
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('report', ROOT/'backend/scripts/pre_retraining_report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class SelectionResult(enum.Enum):
    WON = 'won'
    LOST = 'lost'
    VOID = 'void'


class ReportTests(unittest.TestCase):
    def test_settlement_matches_application_on_score_grid(self):
        tree = ast.parse((ROOT/'backend/app/services/settlement.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'evaluate_selection')
        scope = {'SelectionResult': SelectionResult}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'settlement', 'exec'), scope)
        for market in ['home_win','away_win','draw','btts_yes','btts_no','double_chance_1x',
                       'double_chance_x2','double_chance_12','over_1.5','over_2.5','under_2.5',
                       'over_3.5','under_3.5','over_4.5','under_4.5']:
            for home in range(8):
                for away in range(8):
                    self.assertEqual(report.outcome(market,home,away),
                                     int(scope['evaluate_selection'](market,home,away)==SelectionResult.WON))

    def test_ambiguous_settlement_is_excluded(self):
        for market in ['dnb_home','under_2.0','over_2.25','unknown']:
            with self.assertRaises(ValueError):
                report.outcome(market,1,1)

    def test_scores_known_extremes_and_empty(self):
        self.assertEqual(report.metrics([]), {'n':0})
        rows=[dict(p=p,y=y,match_id=i,day='2026-09-20',quote_valid=True,odds=2)
              for i,(p,y) in enumerate([(0,0),(1,1)])]
        self.assertEqual(report.metrics(rows)['brier'],0)
        self.assertEqual(report.metrics(rows)['ece'],0)
        rows[0]['p']=1; rows[1]['p']=0
        self.assertEqual(report.metrics(rows)['brier'],1)
        self.assertGreater(report.metrics(rows)['log_loss'],30)

    def test_production_evidence_reconciles(self):
        d=json.loads((ROOT/'docs/evidence/pre_retraining_2026-09-20/analysis.json').read_text())
        raw=(ROOT/'docs/evidence/pre_retraining_2026-09-20/source.json.gz').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),d['source_sha256'])
        self.assertEqual(json.loads(gzip.decompress(raw))['read_only'],'on')
        self.assertEqual(d['table_counts']['predictions'],d['all_live']['n']+sum(d['exclusions'].values()))
        self.assertEqual(d['table_counts']['ticket_selections'],d['published']['n']+sum(d['published_exclusions'].values()))
        self.assertEqual(len({(r['match_id'],r['market']) for r in d['published_rows']}),len(d['published_rows']))
        self.assertEqual(d['settlement_mismatches'],[])
        gross=-sum(r['profit'] for r in d['ticket_rows'] if r['result']=='LOST')
        self.assertAlmostEqual(sum(d['gross_ticket_loss_allocation'].values()),gross)
        self.assertTrue(all(r['pre_match'] for r in d['ticket_rows']))
        self.assertAlmostEqual(sum(r['y']*r['odds']-1 for r in d['published_rows']),d['published']['snapshot_paper_profit'])

    def test_matched_review_uses_same_outcomes_and_pre_match_version(self):
        folder=ROOT/'docs/evidence/pre_retraining_2026-09-20'
        result=json.loads((folder/'selection_review.json').read_text())
        source=json.loads(gzip.decompress((folder/'source.json.gz').read_bytes()))['tables']
        predictions={p['id']:p for p in source['predictions']}
        matches={m['id']:m for m in source['matches']}
        self.assertEqual(result['matched_n'],61)
        self.assertEqual(result['missing'],[])
        for pair in result['pairs']:
            first=predictions[pair['first_prediction_id']]
            last=predictions[pair['latest_prediction_id']]
            match=matches[pair['match_id']]
            for key in ('match_id','market','model_version'):
                self.assertEqual(first[key],last[key])
            self.assertLess(report.dt(last['created_at']),report.dt(match['kickoff_at']))
            self.assertEqual(pair['y'],report.outcome(pair['market'],match['home_goals'],match['away_goals']))
        self.assertAlmostEqual(result['matched_latest_same_version']['mean_p'],
                               sum(p['latest_p'] for p in result['pairs'])/61)

    def test_calibrated_probability_threshold_counterexample(self):
        rows=[dict(p=p,y=int(i<wins),match_id=f'{p}-{i}',day='synthetic',odds=None,quote_valid=False)
              for p,wins in [(0.2,20),(0.8,80)] for i in range(100)]
        self.assertAlmostEqual(report.metrics(rows)['gap'],0)
        selected=[r for r in rows if r['p']>=.7]
        self.assertEqual(sum(r['y'] for r in selected),80)
        self.assertAlmostEqual(report.metrics(selected)['mean_p'],.8)
        self.assertAlmostEqual(report.metrics(selected)['gap'],0)


if __name__=='__main__':
    unittest.main()
