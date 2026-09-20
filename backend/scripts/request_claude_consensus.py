"""Ask installed Claude Code for a tools-disabled review of supplied evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT/'docs/evidence/pre_retraining_2026-09-20'
review = json.loads((FOLDER/'selection_review.json').read_text())
review.pop('pairs')
prompt = '''The user explicitly asks Codex and Claude Code to review the analysis until they reach a general consensus. You are a NEW independent Claude Code review, not the original session. Do not claim you inspected anything outside the evidence supplied here. No tools, code changes, fitting, or external actions. Challenge Codex if wrong; do not agree merely to achieve consensus. Return a concise verdict on each disagreement and a defensible shared conclusion, with unresolved items and recommended next action.

The user pasted an earlier Claude review claiming:
1. Published picks are overconfident (61 picks, 79.9% mean p vs 50.8% wins, Brier .3163); underlying all-market forecasts are 'essentially'/'almost perfectly' calibrated (1963, Brier .2223, pooled gap -.6pp).
2. Q score uses raw p at 25%; tier Q thresholds are 75-85, top-30 pool sorted by (Q, EV, p). This CONFIRMS selection as the mechanism of the +29pp gap, even absent totals bias. 'Selecting on predicted probability ... will always inflate ... probability versus win rate, even if ... near-perfectly calibrated.' Winner's curse is the claimed explanation.
3. Several Q components are often missing/zero. This noisy composite explains selection bias.
4. Under market loss allocation and calibration are 'two independent angles'; home-win +12.26 units from four picks was invoked alongside ticket P&L +2.05.
5. Fix selection first: remove/greatly reduce p's Q weight or switch ranking to edge/EV or isotonic-shrunk probabilities, without retraining. Then tackle totals bias.

Codex assessment to challenge:
A. Code facts verify confidence-sensitive selection, not the causal magnitude or a guaranteed defect. If E[Y|P]=P and inclusion S is a threshold determined solely by P, E[Y-P|S]=0. General Q/EV/top-k selection can reveal subgroup miscalibration; marginal calibration alone does not ensure calibration conditional on all selection variables. Winner's curse is plausible, not proven by sorting code.
B. Brier mixes resolution and reliability. Opposite-market biases cancel in the pooled signed gap. All-market ECE=6.00pp; Under2.5 gap +13.17pp, Under3.5 +8.95pp, BTTS No +11.66pp, 70-80% and 80-90% bins overconfident by ~9pp. Not near-perfect calibration. See https://scikit-learn.org/1.8/modules/calibration.html .
C. Current local selector also has prior-period league/market calibration gates (sample>=20), odds/edge/data quality/freshness gates, bounded Q relaxation, partial-beam EV ranking and EV-based final objectives. It selects within a completed model run/date, not all 11k prediction rows. Deployed SHA and historical gate configurations remain unverified.
D. NEW frozen-snapshot matched comparison: all 61 first-published picks match to latest pre-kickoff forecasts for the same fixture/market/model version; same outcomes. First mean p=.7989658936, latest=.8000193727, hit=.5081967213; gap .290769 -> .291823; Brier .316343 -> .316821. Thus forecast timing barely explains the gap. Published snapshots exactly equal their linked predictions (0 discrepancies). This supports concentration of existing forecast error rather than numerical inflation during publication. All latest p>=.7 records: n466, mean .825828, wins .738197, gap .087630. Not a matched counterfactual against published picks.
E. All 61 published records mark all nine Q components 'available'. Stored metadata does not prove input validity but does not establish missing-component causation. xG input is explicitly rolling-goal-derived expected-goals proxy, not measured xG.
F. Edge=p-implied and EV=p*odds-1 still reuse p, so EV-only selection is not inherently a cure. Isotonic needs a distinct calibration fit/holdout. Removing 25 Q points while retaining thresholds radically changes eligibility. Recommend frozen, offline matched ablations after reconstructing historical candidate pools/gates, not immediate live Q changes. Retain training/promotion hold.
G. The two diagnostics reuse outcomes; complementary, not independent confirmations. Home-win +12.26 belongs to the 61-pick singles simulation, not the 15-ticket ledger. Quote linkage absent proves absent now, not necessarily that it once existed. Five dates/39 published fixtures cannot prove causation or eliminate variance.

Please state explicitly whether you accept/reject A-G, what is actually established, and whether immediate Q-weight changes are justified. Also identify errors in the supplied report or new comparison. Your answer will be preserved and attributed as a fresh Claude Code reconciliation, not the original author's reply.

NEW COMPUTED EVIDENCE:
'''+json.dumps(review,indent=2)
for name, spans in {
    'backend/app/services/models/ensemble.py':[(36,49),(131,162),(187,220)],
    'backend/app/services/accumulator_builder.py':[(30,77),(313,420),(426,455),(548,577),(610,670)],
    'backend/app/services/ticket_publisher.py':[(205,223)],
}.items():
    lines=(ROOT/name).read_text(encoding='utf-8').splitlines()
    for start,end in spans:
        prompt+='\n\nSOURCE '+name+':'+str(start)+'\n'+'\n'.join(f'{i+1}: {lines[i]}' for i in range(start-1,end))
prompt+='\n\nORIGINAL REPORT:\n'+(ROOT/'docs/LOSS_ATTRIBUTION_CALIBRATION_2026-09-20.md').read_text(encoding='utf-8')
compact='--compact' in sys.argv
followup='--followup' in sys.argv
suffix='_followup' if followup else '_compact' if compact else ''
if compact:
    prompt=prompt.split('NEW COMPUTED EVIDENCE:')[0]+'''\nReply in at most 450 words. No tools. This is a conceptual reconciliation of the numerical evidence and code facts supplied above, not a fresh inspection. Do you accept the corrected conclusion or identify a remaining substantive objection? Be explicit about which earlier claims you retract or qualify. Do not assume independent verification of the supplied calculations.'''
if followup:
    prompt='''Follow-up on a user-authorized Codex/Claude reconciliation. You are a fresh tools-disabled reviewer with the previous Claude answer supplied below, not the original user-pasted session. We seek evidence-based consensus, not forced agreement. Reply within 300 words.

Your previous answer accepted A-G but concluded '(3) Selection-on-P mechanically contributes [to miscalibration]'. Codex objects that this remains unproven and contradicts acceptance of the calibrated-threshold counterexample. Selection demonstrably changes cohort composition/preference for high model-derived scores, but neither sorting code nor unmatched cohort gaps establishes a nonzero causal contribution to miscalibration. Will you replace that clause with 'selection concentrates observed forecast errors in this sample; a causal amplification mechanism and magnitude remain unproven'?

You requested actual gate code for C; exact CURRENT LOCAL excerpts follow. We agree that historical/deployed gate activation is still unverified, so this code proves gates exist locally, not that they operated for these records. For D, 'timing barely explains the gap' refers only to the exact 61-record descriptive comparison: p mean changes by +0.10535pp, same outcomes. It is not a general causal or population claim and needs no claim about a CI beyond this frozen sample. This matches the same fixture/market/model version, not different sets.

Proposed shared conclusion: The archive shows substantial published-cohort overconfidence and underlying market/high-probability miscalibration. Current local selection favors probability-derived scores and includes calibration/quality gates. Published probabilities match linked predictions; later forecasts for the same picks retain the ~29pp gap. Selection causation and the best remedy remain unresolved. Do not immediately remove Q weights, switch to EV-only, fit calibration on these same outcomes, or retrain for deployment. Reconstruct historical pools/settings, freeze fair offline ablations and validate a frozen policy on later untouched fixtures. Metadata availability is not input validity; the two outcome-based analyses are not independent and singles/ticket P&L must stay separate.

State agreement or the specific remaining objection. Do not claim direct inspection outside supplied excerpts. Previous answer:\n'''+(FOLDER/'claude_review_response_compact.md').read_text(encoding='utf-8')
    lines=(ROOT/'backend/app/services/accumulator_builder.py').read_text(encoding='utf-8').splitlines()
    for start,end in [(332,366),(426,453),(610,620),(632,636)]:
        prompt+='\nSOURCE backend/app/services/accumulator_builder.py\n'+'\n'.join(f'{i+1}: {lines[i]}' for i in range(start-1,end))
(FOLDER/f'claude_review_request{suffix}.txt').write_text(prompt,encoding='utf-8')
exe=Path(os.environ['APPDATA'])/'npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe'
try:
    command=[str(exe),'-p','--safe-mode','--tools','','--strict-mcp-config',
             '--no-session-persistence','--output-format','json','--effort','medium']
    use_stdin=len(prompt)>20000
    if not use_stdin:
        command.append(prompt)
    result=subprocess.run(command,input=prompt if use_stdin else None,
                          text=True,encoding='utf-8',capture_output=True,timeout=120 if compact or followup else 240,cwd=ROOT)
except subprocess.TimeoutExpired:
    (FOLDER/f'claude_review_status{suffix}.json').write_text(json.dumps({'status':'timeout','verdict_received':False})+'\n')
    print('Claude review timed out; no verdict received.')
    raise SystemExit(2)
(FOLDER/f'claude_review_response{suffix}.json').write_text(result.stdout,encoding='utf-8')
if result.returncode:
    print(result.stderr[:2000])
    print(result.stdout[:2000])
    raise SystemExit(result.returncode)
response=json.loads(result.stdout)
(FOLDER/f'claude_review_response{suffix}.md').write_text(response.get('result',''),encoding='utf-8')
print(response.get('result','No result text'))
