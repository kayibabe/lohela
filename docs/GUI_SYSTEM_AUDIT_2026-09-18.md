# Lohela GUI system audit

Date: 2026-09-18  
Scope: React/Vite frontend in `frontend/`, with rendered login-shell review and static inspection of the authenticated workspace routes.  
Method: Inspect -> Plan -> Implement -> Test -> Review -> Correct -> Retest -> Improve.

## Executive result

Lohela has a coherent, category-appropriate research dashboard foundation: clear workspace modules, paper-research positioning, responsive navigation rules, loading/empty/error states, semantic landmarks, and a consistent design token system.

The audit found and remediated the highest-confidence GUI defects in scope:

- narrow-screen login layout could clip the form horizontally;
- keyboard focus styling was not consistently guaranteed across controls;
- icon-only theme/date controls depended on a tooltip/title instead of a robust accessible name;
- authentication network failures could escape as an unhandled promise and leave users without an actionable message.

The application is not certified as WCAG-conformant or production-ready solely by this audit. Automated accessibility tooling, authenticated browser journeys, real backend data, and deployment verification remain outside the evidence available in this run.

## Standards and category benchmark lens

The review used the following practical targets:

- WCAG 2.2 AA principles: keyboard access and visible focus, labels and status/error communication, reflow at narrow widths, and non-colour-only meaning.
- Nielsen usability heuristics: visibility of system status, match to the user's language, user control, consistency, error prevention/recovery, and recognition over recall.
- Mature football research/tracking products: persistent date/context navigation, distinct research versus personal tracking areas, evidence drill-down, explicit paper/research framing, responsive cards/tables, and trustworthy empty/error states.

These are benchmark criteria, not a claim of formal certification or a comparison based on a full competitor test.

## Findings and disposition

| Finding | Risk | Disposition | Evidence |
|---|---:|---|---|
| Login shell could overflow at approximately 390px viewport width | High UX | Fixed with mobile full-width shell, constrained grid, viewport-bounded form controls, and overflow protection | Verified by fresh Chrome headless 390x844 render after patch |
| Focus visibility was not defined globally for all interactive controls | Medium accessibility | Fixed with a shared `:focus-visible` rule using the design accent | Verified by source inspection and successful production build; keyboard traversal not independently exercised |
| Theme and daily date arrow controls had title text but no explicit accessible name | Medium accessibility | Fixed with `aria-label` values that describe the action/state | Verified by source inspection and successful TypeScript build |
| Auth bootstrap/login could fail outside the response-status path | Medium resilience | Fixed with catch handlers and actionable alert text; login error is assertive/live | Verified by source inspection and successful build/tests; live API outage journey not run |
| Full authenticated workspace and admin journeys | High evidence gap | Not falsely marked complete; requires a usable test account/backend and browser automation bridge | Untested in this run |
| Formal WCAG/axe audit and screen-reader pass | High evidence gap | Not falsely marked complete | Untested in this run |

## Changes implemented

- `frontend/src/App.tsx`
  - Added explicit accessible names for theme and previous/next day controls.
  - Added a focusable `main` landmark target for future skip-navigation and keyboard-flow improvements.

- `frontend/src/auth.tsx`
  - Added recovery for session-bootstrap network failure.
  - Added recovery for login/registration network failure with user-facing guidance.

- `frontend/src/pages/Login.tsx`
  - Marked authentication errors as assertive live alerts.

- `frontend/src/App.css`
  - Constrained the authentication shell and grid items at narrow widths.
  - Added mobile viewport-bounded form sizing and overflow protection.
  - Added consistent visible keyboard focus styling.

## Verification

- Verified: `frontend/npm.cmd run build` — TypeScript and Vite production build passed.
- Verified: `frontend/npm.cmd run test -- --run` — 2 test files, 13 tests passed.
- Verified: `git diff --check` — no whitespace errors reported; existing CRLF normalization warnings are non-fatal.
- Verified: desktop login render captured at 1440x900.
- Verified: cold mobile login render captured at 390x844 after remediation.
- Limited: the dedicated browser-control bridge was unavailable due to a request-header policy timeout, so interactive authenticated navigation, keyboard traversal, and live network-failure injection were not independently exercised.
- Preserved: pre-existing backend/data-lineage work in the working tree was not modified by this GUI remediation.

## Remaining work recommended for the next controlled pass

1. Run an authenticated browser journey for each supported role through Tickets, Tracker, Analytics, Tools, Upgrade, and Admin, including empty, API-error, loading, and permission-denied states.
2. Run axe or equivalent WCAG checks at desktop, tablet, and 320/390px mobile widths, then perform keyboard-only and screen-reader spot checks.
3. Validate real backend/session behavior against a disposable environment, including expired session, 401/403, rate-limit, and API outage recovery.
4. Add browser-level regression coverage for the mobile login reflow and authentication error announcement.
5. Treat formal international-standard compliance, competitor parity, and release readiness as open until those checks have evidence.

