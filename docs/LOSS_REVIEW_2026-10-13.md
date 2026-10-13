# Match-leg loss review — 2026-10-13

## Scope and evidence labels

This is a read-only retrospective of the local Lohela API records for 6–12
October 2026, plus the all-market research cohort for 1–12 October. It does not
represent the other apps in Portfolio or verified real-money betting.

- **Verified:** values returned by `/api/v1/performance/*` and saved ticket
  detail endpoints during this review.
- **Simulated:** flat one-unit returns calculated by the research/performance
  endpoints. These are not actual stakes or payouts.
- **Inferred:** patterns worth testing; they do not establish why a specific
  match was lost.
- **Untested:** any selection, calibration, or publication-policy change.

No selection thresholds, weights, or publication rules were changed in this
review. The working tree already contained unrelated in-progress changes; those
were preserved.

## Settled recommendation and ticket outcomes

The deduplicated recommendation ledger contained 89 unique picks: 88 settled,
55 won, 33 lost, and one pending. At one unit per settled pick, the simulated
return was -0.25 units (-0.28% ROI).

| Source | Settled | Won | Lost | Hit rate | Simulated ROI |
|---|---:|---:|---:|---:|---:|
| Published picks | 56 | 37 | 19 | 66.1% | +1.61% |
| Strongest snapshots | 33 | 19 | 14 | 57.6% | -0.73% |
| Combined, deduplicated | 88 | 55 | 33 | 62.5% | -0.28% |

The daily ticket summary contained 18 settled tickets: 7 won and 11 lost. All
four High Odds tickets lost, but four tickets are too few to establish a tier
rule.

On 12 October, the recommendation ledger had 12 wins and 8 losses. The ticket
For the current ticket cohort on 12 October, one of three tickets won and two
lost, with three losing legs across three matches. The wider recommendation
ledger also contains picks from superseded ticket versions, so its list of
ticket IDs must not be read as simultaneous exposure.

## One-copy shadow comparison

I selected the highest published ticket version for each date and public tier
from 6–12 October, matching the version logic in the performance summary. The
18 settled tickets contain 47 leg appearances and 47 unique exact
match-market-selection picks. There are **zero repeated exact picks** across
those current tickets. A one-copy-per-match-market cap therefore removes zero
legs and changes zero ticket outcomes in this cohort: the shadow remains 7
wins, 11 losses, and +0.53 simulated units at one unit per ticket.

There is one shared fixture across current tickets: Goias–Athletic Club on 6
October, with Under 2.5 lost and Double Chance 1X won. These are different
markets, so the proposed exact-market cap does not remove either pick. This
small sample cannot tell us whether a broader one-pick-per-fixture rule would
help.

At review time, the local `/tickets/daily` response returned the minimum ticket
version, while the performance summary selected the maximum. The shadow uses
the maximum version from immutable ticket history, consistent with the
performance summary. The workspace source already selects maximum versions
for daily tickets, ticket history, and match history. I rebuilt the local
backend from that source and verified that `/tickets/daily` now returns the
same maximum versions. The service reports healthy. This rebuild was local
and was not deployed remotely.

## Probability calibration and market results

For model version 0.3.0, the research endpoint scored 4,158 latest priced
predictions in the date range. The simulated flat-unit ROI was -4.32%. The
model Brier score was 0.2183 versus 0.2028 for market probabilities.

| Model probability | Sample | Observed hit rate | Mean model probability | Gap |
|---|---:|---:|---:|---:|
| 75–80% | 209 | 64.1% | 77.5% | -13.4 pp |
| 80–85% | 216 | 76.4% | 82.3% | -5.9 pp |
| 85–90% | 172 | 76.2% | 87.3% | -11.1 pp |
| 90–95% | 153 | 81.1% | 92.5% | -11.4 pp |
| 95–100% | 44 | 88.6% | 96.6% | -8.0 pp |

This supports investigating overconfidence above 75%. It does not justify
fitting and judging a calibrator on the same retrospective outcomes. The
pre-registered fit/calibration/test protocol remains the appropriate shadow
path; its calibration window ends 15 October and its untouched test window
ends 5 November. No fresh export was captured for this review, so the previous
partial-window row count is not presented as current.

In the all-market research cohort, flat-unit simulated ROI was -19.35% for Away
Win, -10.15% for Double Chance X2, and -6.55% for BTTS No (297 predictions per
market). These include predictions that were not published as picks; use them
to prioritize market-level diagnosis, not to impose market bans.

Competition results are especially sparse in the seven-day recommendation
slice: the 56 settled published picks span 23 competitions, and the largest
single competition has seven picks. The all-source ledger has 89 picks across
27 competitions, with at most ten settled picks in one competition. The
short-term league swings are therefore unsuitable for league exclusions.

The same research endpoint excluded 1,788 predictions because their quote
timestamps were post-kickoff. Those rows are unavailable for valid pre-match
evaluation and were not counted as wins or losses.

## Quote age and closing-price audit

For the 89 deduplicated recommendations, the entry quote was a median 612
minutes before kickoff. The median was 709 minutes for winners and 580 minutes
for losers; 25 winners and 9 losers had quotes older than 12 hours. This slice
does not show older quotes clustering among losses. It does show why recording
an entry timestamp alone is insufficient: quote movement and the accepted
price at decision time need separate evidence.

The existing CLV endpoint returned 4,130 legs with average CLV 0 and a 0% rate
beating the close. Code review found that capture could select the same
pre-kickoff quote used at entry, so those values do not establish that the
closing price was independently observed. The capture now requires a later
pre-kickoff quote; the report now excludes missing, same-time, pre-entry, or
post-kickoff close timestamps and reports close coverage. Historical values
remain unverified until a fresh, valid later quote is captured.

## Next actions

1. Keep the incumbent live rules unchanged while the existing frozen calibration
   experiment completes; compare the shadow calibrator only on its disjoint
   windows.
2. Reconcile ticket-overlap totals against ticket IDs and report both unique
   match/pick quality and repeated ticket exposure.
3. Capture only later pre-kickoff prices for CLV; report coverage and compare
   entry prices with the best available close. Do not treat missing closes as
   zero CLV.
4. Continue market and competition analysis with sample counts. The published
   league cohorts are too small for reliable league exclusions from this
   period.

For the next review, continue tracking unique match-market outcomes separately
from ticket exposure. This first one-copy shadow found no duplicate exact
market picks in current ticket versions, so it provides no evidence that such
a cap would reduce losses. Reconcile the daily endpoint's version selection
and collect more cohorts before considering a broader same-fixture cap.

The CLV code correction is active in the local backend image after the 13
October rebuild. No stored CLV rows were changed or captured as part of this
review.

Detailed latest-ticket attribution is in
[`FAILED_LEG_ATTRIBUTION_2026-10-13.md`](FAILED_LEG_ATTRIBUTION_2026-10-13.md).
