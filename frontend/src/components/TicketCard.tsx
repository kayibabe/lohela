import { useState } from 'react'
import type { Leg, Ticket } from '../lib/api'
import { formatKickoff, formatMarket } from '../lib/api'
import GradeBadge from './GradeBadge'
import { fmt } from '../utils/currency'

interface Props {
  ticket: Ticket | null
  tierName: string
  tierDesc: string
  color: string
  research?: boolean
  onSelectLeg?: (leg: Leg, ticket: Ticket) => void
  suggestedStake?: number | null
}

const PCT = (n: number) => `${n >= 0 ? '+' : ''}${(n * 100).toFixed(1)}%`

const formatLegDay = (iso: string) =>
  new Date(iso).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })

/** Latest leg's kickoff day, e.g. "Sat 26 Sep" — how far a horizon ticket reaches. */
const formatHorizonEnd = (kickoffs: string[]) =>
  formatLegDay(kickoffs.reduce((latest, iso) => (Date.parse(iso) > Date.parse(latest) ? iso : latest)))

const LIVE_PHASE_LABELS: Record<string, string> = {
  '1st_half': '1st Half', half_time: 'Half Time', '2nd_half': '2nd Half',
  extra_time: 'Extra Time', penalties: 'Penalties', suspended: 'Suspended',
  interrupted: 'Interrupted', live: 'Live',
}

function matchStateLabel(leg: Leg): string {
  const status = (leg.match_status ?? '').toLowerCase()
  if (status === 'live' || leg.live_phase) {
    const phase = leg.live_phase ? LIVE_PHASE_LABELS[leg.live_phase] ?? 'Live' : 'Live'
    return `${phase}${leg.elapsed_minutes != null ? ` · ${leg.elapsed_minutes}'` : ''}`
  }
  if (status === 'finished') return 'Finished'
  if (status === 'postponed') return 'Postponed'
  if (status === 'cancelled') return 'Cancelled'
  if (status === 'scheduled') return 'Upcoming'
  return 'Pending'
}

export default function TicketCard({ ticket, tierName, tierDesc, color, research, onSelectLeg, suggestedStake }: Props) {
  const [expanded, setExpanded] = useState(false)
  if (!ticket) {
    return (
      <div className="ticket-card">
        <div className="ticket-header">
          <div className="ticket-tier-row">
            <div className="tier-dot" style={{ background: color }} />
            <span className="ticket-name" style={{ color }}>{tierName}</span>
            {research && <span className="research-badge">Research</span>}
          </div>
        </div>
        <div className="empty-card" style={{ border: 'none', borderRadius: 0 }}>
          <div className="empty-title">No ticket found</div>
          <div className="empty-body">
            No combination satisfied every {tierName.toLowerCase()} rule ({tierDesc}),
            including distinct-match, odds, and correlation limits.
          </div>
          <span className="empty-hint">Review strongest selections or try another date</span>
        </div>
      </div>
    )
  }

  const marketPriced = ticket.pricing === 'market'
  const evPositive = (ticket.expected_value ?? 0) > 0
  // Value review flags model/odds gaps; a market-priced leg has no model gap.
  const flaggedLegs = marketPriced ? [] : ticket.legs.filter(leg => leg.edge != null && leg.edge > 0.30)
  const visibleLegs = expanded ? ticket.legs : ticket.legs.slice(0, 4)
  const resultCounts = ticket.legs.reduce<Record<string, number>>((counts, leg) => { const result = leg.result || 'pending'; counts[result] = (counts[result] ?? 0) + 1; return counts }, {})
  const settlementPending = ticket.legs.some(leg => leg.home_goals != null && leg.away_goals != null && (leg.result === 'pending' || !leg.result))
  const pendingLegs = resultCounts.pending ?? 0
  const ticketResult = ticket.result?.toLowerCase()
  const ticketStatusTone = ticketResult === 'won' || ticketResult === 'lost' || ticketResult === 'void'
    ? ticketResult
    : ticket.status === 'settled'
      ? 'settled'
      : 'open'
  const ticketStatusLabel = ticketResult === 'won'
    ? 'Ticket won'
    : ticketResult === 'lost'
      ? 'Ticket lost'
      : ticketResult === 'void'
        ? 'Ticket void'
        : ticket.status === 'settled'
          ? 'Settled'
          : pendingLegs > 0
            ? `Open · ${pendingLegs} pending`
            : 'Published'
  const riskTone = ticket.risk_score == null ? 'moderate' : ticket.risk_score >= 65 ? 'high' : ticket.risk_score >= 45 ? 'moderate' : 'low'
  const learningReady = ticket.status === 'settled' || ['won', 'lost', 'void'].includes(ticketResult ?? '')
  const roiLearningReady = learningReady && ticket.legs.every(leg => {
    if (!leg.source_odds_at) return false
    return new Date(leg.source_odds_at).getTime() < new Date(leg.kickoff_at).getTime()
  })

  return (
    <div className="ticket-card">
      <div className="ticket-header">
        <div className="ticket-tier-row">
          <div className="ticket-tier-identity">
            <div className="tier-dot" style={{ background: color }} />
            <span className="ticket-name" style={{ color }}>{tierName}</span>
            {research && <span className="research-badge">Research</span>}
            {ticket.high_risk_label && <span className="risk-badge">High Risk / Low Hit Rate</span>}
            {ticket.relaxed_tier && (
              <span
                className="relaxed-badge"
                title={`Thin match day: tier thresholds were relaxed (level ${ticket.relaxation_level}) to publish a ticket instead of none. Treat grade-mix and Q-score expectations as looser than usual.`}
              >
                Relaxed · Thin Slate
              </span>
            )}
            {(ticket.horizon_days ?? 0) > 0 && ticket.legs.length > 0 && (
              <span
                className="relaxed-badge horizon-badge"
                title={`Too few fixtures on the ticket date (e.g. an international break), so this ticket also uses games up to ${ticket.horizon_days} day(s) later. Check each leg's kickoff time.`}
              >
                Includes games to {formatHorizonEnd(ticket.legs.map(leg => leg.kickoff_at))}
              </span>
            )}
          </div>
          <div className="ticket-tier-badges">
            <span className={`ticket-status-badge ${ticketStatusTone}`}>{ticketStatusLabel}</span>
            <span className="version-badge" title="Latest published ticket version">v{ticket.version}</span>
            {marketPriced ? <span className="fair-price-badge">Fair-priced</span> : <GradeBadge grade={ticket.legs[0]?.q_grade ?? 'C'} />}
            {flaggedLegs.length > 0 && <span className="review-badge">Value review · {flaggedLegs.length}</span>}
          </div>
        </div>
        <div className="ticket-stats">
          <div className="stat" title="Combined decimal odds for every leg">
            <span className="stat-label">Ticket odds</span>
            <span className="stat-value neutral">{ticket.combined_odds == null ? 'Pro only' : `${ticket.combined_odds.toFixed(2)}×`}</span>
          </div>
          <div className="stat" title={marketPriced ? "Chance every leg wins, from the bookmaker's own odds with its margin removed" : 'Estimated ticket probability after correlation adjustment'}>
            <span className="stat-label">{marketPriced ? 'Chance to win' : 'Hit probability'}</span>
            <span className="stat-value neutral">
              {ticket.adjusted_probability == null
                ? 'Pro only'
                : `${(ticket.adjusted_probability * 100).toFixed(1)}%`}
              {marketPriced && ticket.adjusted_probability != null && ticket.adjusted_probability > 0 && (
                <small className="stat-sub">about 1 in {Math.max(1, Math.round(1 / ticket.adjusted_probability))}</small>
              )}
            </span>
          </div>
          <div className="stat" title="Higher scores indicate a riskier accumulator">
            <span className="stat-label">Risk score</span>
            <span className={`stat-value risk-${riskTone}`}>{ticket.risk_score == null ? 'Pro only' : `${ticket.risk_score.toFixed(0)}/100`}</span>
          </div>
          {marketPriced ? (
            <div className="stat" title="Average long-run return per unit staked at these odds. Negative means the bookmaker's margin outweighs any price advantage.">
              <span className="stat-label">Expected return</span>
              {/* Never styled as a win: any small positive figure is price noise, not claimed value. */}
              <span className="stat-value neutral">
                {ticket.expected_value == null ? 'Pro only' : PCT(ticket.expected_value)}
              </span>
            </div>
          ) : (<>
          <div className="stat" title="Average Q-score across all ticket legs">
            <span className="stat-label">Avg Q-score</span>
            <span className="stat-value neutral">{ticket.avg_q_score == null ? 'Pro only' : ticket.avg_q_score.toFixed(1)}</span>
          </div>
          <div className="stat" title="Average model edge across all ticket legs">
            <span className="stat-label">Avg edge</span>
            <span className={flaggedLegs.length > 0 ? 'stat-value review' : ticket.avg_edge == null ? 'stat-value neutral' : 'stat-value positive'}>{flaggedLegs.length > 0 ? 'Review' : ticket.avg_edge == null ? '—' : PCT(ticket.avg_edge)}</span>
          </div>
          <div className="stat" title={flaggedLegs.length > 0 ? 'Provisional expected value: one or more legs require a value review' : 'Expected value based on the recorded model probabilities and odds'}>
            <span className="stat-label">Ticket EV</span>
            <span className={`stat-value ${flaggedLegs.length > 0 ? 'review' : evPositive ? 'positive' : 'neutral'}`}>
              {flaggedLegs.length > 0 ? 'Unverified' : ticket.expected_value == null ? 'Pro only' : PCT(ticket.expected_value)}
            </span>
          </div>
          </>)}
        </div>
        {marketPriced && (
          <p className="fair-price-note">
            Built for the best chance of winning at these odds, not for value. Chances come from bookmaker prices
            with their margin removed. Over many tickets, expect to lose roughly the margin shown above.
          </p>
        )}
        {(flaggedLegs.length > 0 || settlementPending) && (
          <div className="ticket-alerts">
            {flaggedLegs.length > 0 && <div className="value-review-note"><strong>Value check</strong><span>{flaggedLegs.length} leg{flaggedLegs.length === 1 ? '' : 's'} show unusually large model/odds gaps. Treat edge and EV as provisional until market mapping and odds freshness are checked.</span></div>}
            {settlementPending && <div className="settlement-pending-note"><strong>Settlement action</strong><span>A final score is available, but at least one market outcome still awaits result ingestion.</span></div>}
          </div>
        )}
        <div className="ticket-summary">
          <strong>{ticket.legs.length} legs</strong>
          <div className="ticket-result-counts" aria-label="Leg outcomes">
            {(['won', 'lost', 'void', 'pending'] as const).filter(result => resultCounts[result]).map(result => (
              <span className={`ticket-result-chip ${result}`} key={result}><b>{resultCounts[result]}</b> {result}</span>
            ))}
          </div>
        </div>
        {ticket.settled_at && <div className="ticket-settlement-meta">Settled {new Date(ticket.settled_at).toLocaleString()} · {ticket.settlement_source ?? 'system'} · settlement revision {ticket.settlement_version ?? '—'}{ticket.settlement_version && ticket.settlement_version > 1 && <span className="settlement-correction">Corrected settlement</span>}</div>}
        {learningReady && (
          <div className={`ticket-learning-status ${roiLearningReady ? 'eligible' : 'limited'}`}>
            <strong>Learning evidence recorded</strong>
            <span>{roiLearningReady
              ? 'Probability and pre-kickoff ROI evidence can enter future offline challenger windows; this published ticket remains immutable.'
              : 'Probability evidence can enter future offline challenger windows; ROI learning is excluded where pre-kickoff odds provenance is incomplete.'}</span>
          </div>
        )}
        {suggestedStake != null && !marketPriced && <div className="ticket-stake-suggestion"><div><span>What-if · half Kelly</span><strong>{suggestedStake > 0 && ticket.combined_odds != null ? `${fmt(suggestedStake)} stake` : 'No positive edge'}</strong></div>{suggestedStake > 0 && ticket.combined_odds != null && <div className="ticket-stake-outcomes"><span>Potential return <b>{fmt(suggestedStake * ticket.combined_odds)}</b></span><span>Potential P&amp;L <b className="positive">+{fmt(suggestedStake * (ticket.combined_odds - 1))}</b></span></div>}</div>}
      </div>

      <ul className="leg-list">
        {visibleLegs.map((leg) => (
          <li key={leg.prediction_id} className="leg-item">
            <button className="leg-detail-trigger" onClick={() => onSelectLeg?.(leg, ticket)} disabled={!onSelectLeg}>
              <div>
                <div className="leg-match">
                  {leg.home_team}
                  <span className="leg-match-vs"> vs </span>
                  {leg.away_team}
                </div>
                <div className="leg-meta">
                  <span className="leg-comp">{leg.competition}</span>
                  <span className="leg-time kickoff-time">
                    {(ticket.horizon_days ?? 0) > 0 && <>{formatLegDay(leg.kickoff_at)} </>}
                    {formatKickoff(leg.kickoff_at)}
                  </span>
                  <span className={`match-state-badge ${(leg.match_status ?? 'pending').toLowerCase()}`}>
                    {matchStateLabel(leg)}
                  </span>
                  {leg.home_goals != null && leg.away_goals != null && <span className="live-score-badge" aria-label={`Score ${leg.home_goals} to ${leg.away_goals}`}>{leg.home_goals}–{leg.away_goals}</span>}
                  <span className="leg-market">{formatMarket(leg.market)}</span>
                </div>
                <div className={`leg-outcome ${leg.result}`}>
                  <span className="leg-outcome-label">Pick</span>
                  <strong>{leg.result === 'pending' ? (leg.home_goals != null && leg.away_goals != null ? 'Settlement pending' : 'Pending') : leg.result === 'won' ? 'Won' : leg.result === 'lost' ? 'Lost' : 'Void'}</strong>
                </div>
              </div>
              <div className="leg-right">
                <span className="leg-odds">{leg.best_odds == null ? 'Pro only' : `${leg.best_odds.toFixed(2)}×`}</span>
                {marketPriced ? (
                  leg.model_probability != null && <span className="leg-chance">{(leg.model_probability * 100).toFixed(0)}% chance</span>
                ) : (<>
                {leg.edge != null && (
                  <span className="leg-edge">{PCT(leg.edge)} edge</span>
                )}
                {leg.edge != null && leg.edge > 0.30 && leg.best_odds != null && leg.best_odds > 1 && <span className="leg-review">Implied {(100 / leg.best_odds).toFixed(0)}% · review</span>}
                <span className="leg-q">Q {leg.q_score == null ? 'Pro only' : leg.q_score.toFixed(1)}</span>
                </>)}
                {!marketPriced && <span className={`leg-spread ${leg.model_agreement == null ? 'unknown' : leg.model_agreement <= 0.05 ? 'strong' : leg.model_agreement <= 0.10 ? 'acceptable' : leg.model_agreement <= 0.15 ? 'caution' : 'high'}`}>Spread {leg.model_agreement == null ? '—' : `${(leg.model_agreement * 100).toFixed(1)} pp`}</span>}
              </div>
              <span className="leg-open-icon" aria-hidden="true">›</span>
            </button>
          </li>
        ))}
      </ul>
      {ticket.legs.length > 4 && (
        <button className="ticket-expand" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>
          {expanded ? 'Show fewer legs' : `View all ${ticket.legs.length} legs`}
          <span aria-hidden="true">{expanded ? '↑' : '↓'}</span>
        </button>
      )}
      <div className="ticket-audit">
        <span>v{ticket.version} · model {ticket.model_version}</span>
        <span title={ticket.publication_hash}>Hash {ticket.publication_hash.slice(0, 10)}…</span>
      </div>
    </div>
  )
}
