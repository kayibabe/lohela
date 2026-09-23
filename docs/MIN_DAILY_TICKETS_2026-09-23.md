# Minimum 3 public tickets per day — incident and fix (2026-09-23)

## Symptom
No public tickets from 2026-09-21 onward (and on several earlier weekdays).

## Root cause (verified)
Not the optimiser: fixture supply. 2026-09-21..10-01 is the September FIFA
international window. API-Football `/fixtures?date=` showed 203 / 203 / 103 /
242 / 1194 fixtures worldwide on 22-26 Sep, but only 3 / 1 / 1 / 1 / 5 in the
24 tracked leagues. With 0-5 matches a day no 3-leg accumulator can be built.
Replaying the builder on local data with odds-age held constant produced all 3
public tiers on every day that had a completed model run and a real slate.

Secondary causes found and fixed on the way:
- Season year assumed a July start, so calendar-year leagues (Brazil,
  Argentina, MLS, Ireland) would ingest nothing every January-June.
- Competition seeding was only-if-empty, so new leagues never reached an
  existing database.
- The relaxation fallback skipped the research restricted-market filter.
- Beam search kept its top 800 partials before applying the cross-tier
  match/market exposure rule, so a later tier could be reported unbuildable
  when enough unused legs existed. Relaxation also filled Aggressive first,
  which can starve Conservative on a thin pool.

## Fix
1. **Supply** — 16 break-resilient Tier 3 leagues (config `tier3_league_ids`,
   `seed.COMPETITIONS`), each checked for current-season odds coverage and
   for fixtures during this window and the Oct/Nov 2025 windows. Missing
   competitions are auto-seeded on startup and their last 400 days of results
   auto-backfilled (`pipeline.backfill_leagues`; manual re-queue:
   `POST /api/v1/admin/leagues/backfill`). Seasons are resolved per league
   from `/leagues`.
2. **Guarantee** — rolling horizon (`app/services/ticket_horizon.py`). If the
   target day still has fewer than `min_daily_public_tickets` after
   relaxation, the ticket stage prepares the following days one at a time,
   up to `ticket_horizon_max_days` (4). For each day it runs ingest, odds,
   enrich and model steps, then admits those legs for the missing tiers only,
   full strength first. Every per-leg gate still applies. Tickets record
   `horizon_days`, and the UI shows "Includes games to <day>" plus a date on
   each leg.

## Evidence
- Backend: 241 passed, 1 skipped (`tests/test_ticket_floor.py` adds 14).
  Disabling the beam pre-filter makes 2 of them fail. Frontend: 15/15 pass,
  and the production build succeeds.
- Local end-to-end run on 2026-09-23 (previously 0 tickets), with live API
  data: 16 leagues seeded, about 7,000 historical fixtures backfilled in 87 s,
  and pipeline 22 COMPLETED. It published 3 public tickets from a horizon of
  24-25 Sep (`horizon_days=2`). The ticket stage took 5.5 min.

## Known limitations
- The floor is still not *unconditional*. A 4-day horizon with no modelable,
  odds-covered club fixture anywhere would still fall short and raise the
  existing `min_daily_tickets` alert.
- Model overconfidence is system-wide and predates this change. Leg
  probabilities of 0.8-0.94 sit against market prices of 0.47-0.71, and
  claimed edges reach 20-30% in Tier 1-3 alike. Locally, 1 of 12 settled
  tickets won. More tickets means more exposure to that miscalibration until
  the Fit-phase calibrator lands.
- Fit phase (`FIT_PHASE_TRACKER_2026-09-20.md`) assumed ticket generation
  would stay unchanged. Weights and gates are untouched, but predictions now
  include Tier 3 leagues and horizon-day runs. Filter by competition tier when
  the calibrator is fitted.
- The models train on the most recent 2000 finished matches. With 40 leagues
  that covers about 5 weeks (16 Aug-22 Sep locally), so teams idle for longer
  lose Poisson/Bayes inputs.

## Rollback
`git revert <commit>` and redeploy the worker, then web. `alembic downgrade
d4e6f8a1b3c5` drops `accumulator_tickets.horizon_days` (guarded). The seeded
Tier 3 rows are inert once they are out of the config lists. Set them
`active=false` to hide them completely.
