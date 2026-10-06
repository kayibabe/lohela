# Ablation Validation Completion Audit — 2026-10-06

This is an evidence audit of the research-only objective. It does not approve a
production change.

| Requirement | Evidence | Status |
|---|---|---|
| Follow the frozen ablation protocol | `docs/ABLATION_SCOPING_2026-09-20.md`; runner uses the fixed Fit → Calibrate → Test boundaries | Verified |
| Resolve feasible open items | 21-day test, Platt tail calibrator, 15/10/21-day split, and Q-ablation B at 12.5% are recorded in the scoping docs and runner | Verified |
| Fit calibrator on disjoint forward data | 1,696 deduplicated labeled Fit-tail rows; Platt coefficients recorded in the report | Verified, fit-only |
| Evaluate calibrator out of sample | 48 settled rows exist in one calibration day; runner marks them `PARTIAL_NOT_DECISION_BEARING` | Incomplete |
| Run untouched prospective test | Test window begins 2026-10-15 and ends 2026-11-05; no labeled test rows yet | Not started |
| Run selector-level Q ablations | Current production ticket generations are all market-priced with Q gates at zero; no valid Q-score selector comparison exists | Not applicable to current selector |
| Preserve matched-comparison integrity | Runner refuses to reconstruct variant decisions from production selections without full gate traces | Verified fail-closed behavior |
| Run retrospective ablation diagnostics | Corrected runner passes live Q/ticket constant checks; Q-ablation B is 12.5%; output is labeled non-decision-bearing | Verified diagnostic |
| Avoid production mutation | No deployment, production weight, ticket, or database mutation was performed in this research pass | Verified for this pass |
| Produce evidence-labeled report | `docs/ABLATION_PROSPECTIVE_VALIDATION_2026-10-06.md` and corrected retrospective report | Verified |

## Completion decision

The objective is **not complete**. The missing decision-bearing evidence is the
full disjoint calibration/validation window followed by the untouched 21-day
test. The current selector-path evidence also means Q-score A/B cannot be
interpreted as an active production selector experiment unless a future frozen
model-priced shadow cohort is explicitly created with complete decision traces.
