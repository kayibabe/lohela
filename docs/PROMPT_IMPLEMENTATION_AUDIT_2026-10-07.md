# Prompt Implementation Audit

## Scope

This pass translates the supplied Bet App Prompt into a safe first product slice for Lohela. The prompt is treated as a product requirements document. Existing paper-trading, provenance, settlement, and deployment controls remain authoritative.

## System audit summary

Lohela already contains the core evidence chain: ingestion and validation, model runs, published ticket versions, priced selections, accumulator generation, settlement, historical ledgers, calibration, closing-line value, and backtest safeguards. The existing frontend also separates Today, Track, and Validate, which is a sound decision loop.

The main usability gap was not absence of data. It was prioritisation. The Today page led with ticket tiers and operational context, while the user still had to assemble the answer to “what should I do?” from several metrics and a separate selection detail view.

## Implemented in this pass

- Added a decision-first workspace to Today with a ranked shortlist of priced selections.
- Added explicit BET, WATCH, and PASS states. BET requires a high quality score and positive priced edge; WATCH covers qualified but non-decisive evidence; PASS is shown when no priced opportunity survives the current gates.
- Added a compact decision table with match, market, model probability, odds, edge, quality grade, and decision.
- Added a primary “Why this?” action that opens the existing evidence detail surface.
- Added visible definitions for Grade, Confidence, and Risk.
- Exposed information-set timestamp, odds timestamp, active model set, and data-quality status through the qualified-selection API.
- Added persisted-traceability evidence to Selection Detail, including data-quality score, model set, reasons to consider, and visible risks.
- Added a Passed / why workspace backed by the rejection ledger.
- BET now requires positive edge, Q-score >=85, good data quality, and a non-empty active model set; otherwise the opportunity remains WATCH.
- Extended automatic research breakdowns with odds-band, probability-band, and grade cohorts alongside market and league cohorts.
- Added date-range controls to automatic research so period comparisons do not silently mix all history.
- Added an append-only prediction decision contract (`BET`, `WATCH`, or `PASS`) with policy version, reasons, risks, and migration coverage. New model runs and point-in-time backfills capture it at prediction creation time.
- Preserved the paper-only disclaimer and the existing immutable publication and accumulator flows.
- Added responsive styling so the decision table remains usable on small screens through horizontal scrolling and progressive detail.

## Final information architecture

- Today: current published recommendations, ranked decision shortlist, ticket tiers, best-mix research, odds-policy research, and personal accumulators.
- Track: personal journal, open and settled bets, and evidence-linked history.
- Validate: immutable performance, calibration, CLV, backtests, and research breakdowns.
- Tools: supporting calculators and utilities.
- Admin: authenticated operational controls.

## Deliberately not claimed as complete

The supplied prompt also describes a large future analytics terminal: dedicated league and odds-band cohorts, richer match intelligence, bookmaker movement, data-quality dashboards, and a broader model comparison environment. Existing validation endpoints cover part of this already, but this pass does not invent missing historical data or present unsupported cohorts as production evidence.

## Verification

- Frontend TypeScript and production build: passed.
- Frontend tests: 30 passed across 4 test files.
- Backend tests: 311 passed, 1 skipped.
- Existing backend and deployment functionality was not modified by this UI slice and requires the repository’s normal full-suite and live-environment checks before release.

## Remaining risks

- Historical sample-size warnings remain primarily in Validate; the Today shortlist intentionally stays compact.
- Existing predictions created before the migration have no decision evidence and are shown conservatively as WATCH until regenerated or explicitly backfilled.

## Recommended next phase

1. Add a persisted recommendation decision contract with explicit reason and risk evidence.
2. Build a match intelligence route around the same immutable prediction snapshot and pre-kickoff odds.
3. Add a reusable global date/filter model to the analytics APIs, then implement league, market, and odds-band cohort views with minimum-sample gates.
