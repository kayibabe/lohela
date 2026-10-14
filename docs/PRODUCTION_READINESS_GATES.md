# Lohela production-readiness gates

Status: **release blocked — every gate below is unverified until its evidence is attached and reviewed.** This checklist defines product readiness; completing it does not establish that recommendations are profitable or guarantee outcomes.

Checklist created: 2026-10-14. No gate is marked passed by source changes alone.

Scope: customer-facing published recommendations only. Candidate exploration, rejected-candidate analysis, dynamic policy sweeps, backtests, model training/promotion, bet tracking, custom accumulators, operational controls, and system-wide validation remain internal until their user-ownership and release evidence requirements are met.

## Release gates

| Gate | Pass condition | Evidence required | Status |
|---|---|---|---|
| Customer/internal boundary | Ordinary users can reach only the approved customer workspace. Research, validation, model, tracking, accumulator, and operations screens are admin-only. | Authenticated browser journeys for anonymous, free, pro, and admin roles; direct-route and direct-API denial checks. | NOT VERIFIED |
| Recommendation timing | Every released decision is persisted before the earliest fixture kickoff. No post-kickoff or unknown-timing decision appears as current. | 30 consecutive prospective days with zero late or unknown-timing recommendations; immutable publication timestamps and decision traces. | NOT VERIFIED |
| Price and source evidence | Every released selection has a supported market mapping, valid decision-time quote, acceptable quote age, and source provenance. | 100% provenance on the release cohort; missing or stale evidence fails closed; provider coverage report. | NOT VERIFIED |
| Model validation | Evaluation uses frozen rules and untouched forward observations; results are compared with a decision-time market baseline and reported by claimed market. | At least 30 prospective days and 300 priced settled decisions overall, with at least 100 per market claimed at release. Extend the window where uncertainty remains material. Publish calibration, Brier/log-loss, fixed-unit return, drawdown, CLV, confidence intervals, and exclusions. | NOT VERIFIED |
| Settlement and ledger integrity | Supported markets settle consistently; void, push-sensitive, unsupported, and corrected results remain auditable. Historical versions are preserved. | Reconciliation of persisted outcomes against the canonical settlement rules; duplicate, revision, and correction audit. | NOT VERIFIED |
| Scheduled operations | Ingestion, generation, settlement, and refresh jobs complete within their defined windows and failures alert an operator. | At least 99% scheduled-job success across 30 days; one exercised failure alert and documented recovery. | NOT VERIFIED |
| Backup and recovery | A recoverable backup exists outside the primary runtime and can be restored without touching customer data. | Successful isolated restore rehearsal against agreed recovery-point and recovery-time objectives. | NOT VERIFIED |
| Security and privacy | Customer, internal, and administrator data boundaries are enforced by the API, not only by hidden navigation. Sessions, secrets, rate limits, and account lifecycle are reviewed. | Role matrix, direct endpoint authorization review, dependency/security scan, and documented privacy/data-retention review. | NOT VERIFIED |
| Account-owned ledgers | Bet and accumulator records are isolated by account before tracking or custom accumulators are exposed to customers. | Schema migration, ownership assignment policy for existing records, and cross-account read/write denial evidence. | NOT VERIFIED |
| Legal and data rights | The intended customer markets, prediction/odds data use, disclosures, and any regulated activities have been reviewed for the applicable jurisdictions. | Written jurisdiction-specific product and data-rights review. | NOT VERIFIED |

## Decision rule

Release is blocked while any required gate is `NOT VERIFIED`, `INCOMPLETE`, or `FAIL`. Evidence must identify the cohort, date range, code/model/policy versions, source, exclusions, reviewer, and decision. A gate changes to `PASS` only after that evidence is reviewed and recorded; a passing technical gate does not authorize real-money wagering or claims of guaranteed profit.

The sample floors above are minimum starting thresholds, not proof of statistical sufficiency. Increase them when market dependence, calibration uncertainty, or confidence intervals require more observations. Freeze model, selection, and publication rules for each prospective cohort; a rule change starts a new cohort.
