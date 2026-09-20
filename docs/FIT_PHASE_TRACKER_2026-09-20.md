# Fit-phase tracker

Status: **in progress.** Started 2026-09-20T05:33:30.491115Z. This is the first of the two new-data phases required before the prospective ablation run can start (`ABLATION_SCOPING_2026-09-20.md` §4, §7 item 3; decided in `ABLATION_OPEN_ITEMS_PROPOSAL_2026-09-20.md` item 3).

## What "starting" this phase means

Nothing was deployed and no production code, gate, or weight changed — per scoping §8, this experiment may not touch production selection code, gates, weights, or ticket generation. The Fit phase's entire method is: **let the system keep running exactly as it already does, and log naturally-generated predictions for the window.** There is nothing to build or turn on. What *is* new as of this session:

- **A formal start boundary is now fixed and impossible to fudge later**: `FIT_PHASE_START = 2026-09-20T05:33:30.491115+00:00`, the exact `captured_at` instant of the frozen diagnostic snapshot (`source_sha256 = 388db18a93...`). Any prediction created at or after this instant counts as Fit-phase data; anything before it is part of the already-used diagnostic archive and must never be reused for fitting (scoping §1, §4).
- **A progress-check script**: [`backend/scripts/fit_phase_status.py`](../backend/scripts/fit_phase_status.py). Sanity-checked against the frozen archive (correctly reports 0 Fit-phase predictions, since that archive predates the boundary by construction). Not yet run against live data — that requires a fresh read-only export from the production database, which this coding session has no credentials for (see "How to check progress," below).

## Targets (from the accepted proposal)

| Target | Value | Rationale |
| --- | --- | --- |
| Minimum elapsed time | 15 days | `ABLATION_OPEN_ITEMS_PROPOSAL_2026-09-20.md` item 3 |
| Minimum total predictions logged | 300 | Same |
| Minimum predictions with `model_probability` ≥ 0.65 (the calibration tail) | 60 | The diagnosed miscalibration concentrates in the 70–90% band; the calibrator is fit only on this tail (§4), so this is the binding target, not the total |

All three must be met before moving to the Calibrate/validate phase — see `ready_for_calibrate_phase` in the script's output.

## How to check progress

This requires running inside the application's configured environment against the production database (Railway), which this session does not have credentials for:

```bash
python backend/scripts/export_pre_retraining.py > /tmp/fresh_export.txt
# extract the EVIDENCE_JSON= line's payload into fresh_source.json
python backend/scripts/fit_phase_status.py fresh_source.json
```

Re-run this periodically (e.g. weekly) rather than only at the 15-day mark, so a thin slate isn't discovered only after the window nominally closes.

## What happens when targets are met

1. Fit the Platt-scaling calibrator on the tail (p ≥ 0.65) slice of the Fit-phase data only.
2. Start the Calibrate/validate phase (~10 days, new data again, untouched by fitting) — not yet scripted; build its own progress-check script analogous to this one, with its own disjoint start boundary (the Fit phase's end instant).
3. Only after both phases clear their targets does the 21-day prospective Test window (scoping §5) begin.

## Requirements checklist

| Item | Status |
| --- | --- |
| Fix an unambiguous Fit-phase start boundary with zero archive overlap | Completed — `2026-09-20T05:33:30.491115Z` |
| Build a progress-check script | Completed — `backend/scripts/fit_phase_status.py` |
| Sanity-check the script against known data | Completed — frozen archive correctly shows 0 |
| Check progress against live production data | Blocked — no DB credentials in this session; needs to be run manually or from an environment with them (see above) |
| Fit the calibrator | Not started — gated on targets above |
