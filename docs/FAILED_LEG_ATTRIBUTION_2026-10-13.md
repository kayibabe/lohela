# Failed-leg attribution — 2026-10-13

## Scope and version validation

Source: the local Lohela API after rebuilding and restarting only the backend.
For 6–12 October 2026, this review selects the highest published version for
each target date and ticket tier, then compares ticket publication time with
the earliest kickoff in that ticket. All P&L below is simulated at one unit per
ticket; it is not a record of real stakes or payouts.

The version-selector fix was verified on 12 October: `/tickets/daily` returned
ticket IDs 70, 71, and 72 at version 3, matching the highest versions in
immutable ticket history. The backend health endpoint returned `ready`.

## Exclude post-kickoff publications from predictive attribution

The 18 latest stored tickets show 7 wins and 11 losses, with +0.53 simulated
units. However, the three latest 12 October tickets (IDs 70–72) were published
**341–461 minutes after their earliest leg had kicked off**. Their outcomes
cannot be counted as pre-match predictions. Excluding them leaves 15 timely
tickets: 6 wins, 9 losses, and +1.533 simulated units. Those timely tickets
contain 39 leg appearances: 26 wins, 12 losses, and one void.

The timely tickets have 39 unique exact match-market-selection keys. A
one-copy-per-match-market cap removes zero legs and changes zero ticket
outcomes. One fixture appears in two different markets: Goias–Athletic Club
Under 2.5 lost and Double Chance 1X won. The exact-market cap does not remove
either.

## Lost legs in timely tickets

| Date | Ticket | Fixture | Market / pick | Model probability | Odds | Edge | Q grade | Quote age at publication |
|---|---|---|---|---:|---:|---:|---|---:|
| Oct 6 | 47 Balanced | Goias vs Athletic Club | Under 2.5 | 59.2% | 1.62 | −2.50 pp | REJECT | 0.1 min |
| Oct 8 | 50 Safe | Ceara vs Criciuma | Double Chance X2 | 60.4% | 1.62 | −1.31 pp | REJECT | 1.1 min |
| Oct 8 | 51 Balanced | Raków Częstochowa vs GKS Katowice | Double Chance 1X | 68.9% | 1.42 | −1.49 pp | REJECT | 1.0 min |
| Oct 9 | 55 Safe | SK Beveren vs Lommel United | Double Chance 1X | 76.2% | 1.28 | −1.89 pp | REJECT | 1.1 min |
| Oct 9 | 55 Safe | Eintracht Braunschweig vs Holstein Kiel | Over 2.5 | 61.8% | 1.57 | −1.92 pp | REJECT | 1.2 min |
| Oct 9 | 57 High Odds | Union Brescia vs PRO Vercelli | Over 2.5 | 55.4% | 1.78 | −0.77 pp | REJECT | 0.6 min |
| Oct 9 | 57 High Odds | Aldosivi vs Sarmiento Junin | Double Chance 1X | 68.4% | 1.42 | −2.06 pp | REJECT | 1.4 min |
| Oct 10 | 58 Safe | Wealdstone vs Yeovil Town | Under 4.5 | 80.6% | 1.22 | −1.35 pp | REJECT | 6.9 min |
| Oct 10 | 60 High Odds | Eldense vs Cordoba | Double Chance X2 | 58.6% | 1.72 | +0.49 pp | REJECT | 3.6 min |
| Oct 11 | 62 Balanced | Union St. Gilloise vs OH Leuven | Over 2.5 | 65.4% | 1.50 | −1.24 pp | REJECT | 1.4 min |
| Oct 11 | 63 High Odds | Sassuolo vs AC Milan | Over 2.5 | 54.2% | 1.87 | +0.77 pp | REJECT | 0.7 min |
| Oct 11 | 63 High Odds | Cerro Largo vs Albion FC | Double Chance 1X | 68.2% | 1.44 | −1.25 pp | REJECT | 0.9 min |

Quote age here is the recorded source-odds timestamp to ticket publication.
All 39 timely-ticket leg quotes were at most two hours old at publication. That
does not prove the price was still executable at a bookmaker or beat the
closing price.

## Patterns to investigate, not new gates

- **Market:** Over 2.5 had 3 wins and 4 losses (7 legs); Double Chance 1X had
  7 wins and 4 losses (11 legs). These small counts are leads for continued
  monitoring, not grounds for banning either market.
- **Probability:** the 55–64.9% band had 5 wins and 5 losses (10 legs). The
  65–74.9% band had 13 wins and 4 losses (17 legs); 75%+ had 4 wins and 2
  losses (6 legs). The 75%+ ticket-leg sample is too small to override the
  broader calibration review.
- **Quality grade:** all 12 timely losing legs were labelled `REJECT`, but 24
  of the 37 `REJECT` legs won. The label did not separate outcomes in this
  cohort; audit how these picks enter market-priced tickets before deciding
  whether it should gate publication.
- **Ticket tier:** Balanced was 3–3, Safe was 3–3, and High Odds was 0–3 among
  timely tickets. Three High Odds tickets are not enough to justify removing
  that tier.
- **Competition:** losing legs span many competitions, so this period does not
  support league-specific exclusions.

## Admission-path audit

Source review explains why `REJECT` grades and negative model edges can appear
in active public tiers. The active profile uses market-priced tickets:
`market_priced()` replaces the model probability, edge, and expected value
with values derived from the de-vigged bookmaker market. The market admission
gate checks price bands, market coverage, data quality, and quote freshness;
it does not gate on the model's Q-score or model edge. The Q-grade remains
attached as model audit context. Therefore a `REJECT` grade is not a failed
market gate, and a negative model edge is not the active tier's pricing basis.

This is the current selector design, not a policy change made in this review.
It also means these tickets should be evaluated as market-likelihood picks,
not as model-value selections. The live display should make that distinction
clear before the next publication-integrity review. No selection thresholds
were changed.

## Follow-up

1. Keep the frozen probability-calibration experiment unchanged through its
   prospective windows.
2. Verify actual accepted odds and collect strictly later pre-kickoff closes;
   missing closes must remain missing, not be scored as zero movement.
3. Audit why `REJECT`-graded and negative-edge legs are admitted to public
   market-priced tickets, then evaluate any proposed gate on a disjoint
   forward window.
4. Prevent post-kickoff ticket versions from being presented as actionable
   daily tickets or counted as pre-match performance.

No live selector or publication policy was changed in this review.
