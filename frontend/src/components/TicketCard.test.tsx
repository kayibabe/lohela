import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import TicketCard from './TicketCard'
import SelectionDetail from './SelectionDetail'
import type { Ticket } from '../lib/api'
import { accumulatorBlockers, SelectionPanel } from '../pages/DailyTickets'

const ticket: Ticket = {
  ticket_id: 42,
  ticket_type: 'aggressive',
  name: 'Aggressive',
  status: 'published',
  version: 3,
  combined_odds: 12.4,
  combined_probability: 0.08,
  adjusted_probability: 0.06,
  correlation_penalty: 0.25,
  expected_value: 0.18,
  risk_score: 82,
  confidence_score: 76,
  avg_q_score: 83,
  avg_edge: 0.05,
  model_version: '0.2.0',
  published_at: '2026-08-28T09:00:00Z',
  publication_hash: 'a'.repeat(64),
  high_risk_label: true,
  relaxed_tier: false,
  relaxation_level: 0,
  internal_only: false,
  result: null,
  profit_loss: null,
  legs: [{
    selection_id: 1,
    prediction_id: 7,
    match_id: 9,
    home_team: 'Alpha',
    away_team: 'Beta',
    competition: 'Test League',
    kickoff_at: '2026-08-28T12:00:00Z',
    market: 'over_2.5',
    selection: 'over_2.5',
    model_probability: 0.78,
    best_odds: 1.7,
    q_score: 83,
    q_grade: 'B+',
    edge: 0.05,
    expected_value: 0.12,
    source_odds_at: '2026-08-28T09:00:00Z',
    result: 'pending',
  }],
}

describe('audited ticket surfaces', () => {
  it('renders the mandatory aggressive risk label and publication audit data', () => {
    const html = renderToStaticMarkup(
      <TicketCard ticket={ticket} tierName="Aggressive" tierDesc="Q ≥75" color="red" />,
    )
    expect(html).toContain('High Risk / Low Hit Rate')
    expect(html).toContain('Hit probability')
    expect(html).toContain('Risk score')
    expect(html).toContain('Open · 1 pending')
    expect(html).toContain('v3')
    expect(html).toContain('Hash aaaaaaaaaa')
    expect(html).toContain('v3 · model 0.2.0')
  })

  it('flags a relaxed-tier ticket built on a thin match day', () => {
    const relaxedTicket: Ticket = { ...ticket, relaxed_tier: true, relaxation_level: 2 }
    const html = renderToStaticMarkup(
      <TicketCard ticket={relaxedTicket} tierName="Balanced" tierDesc="Q ≥80" color="blue" />,
    )
    expect(html).toContain('Relaxed · Thin Slate')
    expect(html).toContain('level 2')
  })

  it('labels a rolling-horizon ticket and dates its later legs', () => {
    const horizonTicket: Ticket = {
      ...ticket,
      horizon_days: 3,
      legs: [
        ticket.legs[0],
        { ...ticket.legs[0], selection_id: 2, prediction_id: 8, kickoff_at: '2026-08-31T14:00:00Z' },
      ],
    }
    const html = renderToStaticMarkup(
      <TicketCard ticket={horizonTicket} tierName="Balanced" tierDesc="Q ≥80" color="blue" />,
    )
    expect(html).toContain('Includes games to Mon 31 Aug')
    expect(html).toContain('Fri 28 Aug')
    expect(html).toContain('up to 3 day(s) later')
  })

  it('shows a fair-priced ticket as an honest chance with no value claims', () => {
    const fair: Ticket = {
      ...ticket,
      pricing: 'market',
      adjusted_probability: 0.5,
      expected_value: -0.041,
      legs: [{ ...ticket.legs[0], model_probability: 0.79, edge: -0.02, best_odds: 1.21 }],
    }
    const html = renderToStaticMarkup(
      <TicketCard ticket={fair} tierName="Conservative" tierDesc="" color="green" />,
    )
    expect(html).toContain('Chance to win')
    expect(html).toContain('about 1 in 2')
    expect(html).toContain('Expected return')
    expect(html).toContain('-4.1%')
    expect(html).toContain('79% chance')
    expect(html).toContain('Fair-priced')
    expect(html).toContain('expect to lose roughly the margin')
    for (const modelOnly of ['Avg Q-score', 'Avg edge', ' edge<', 'Spread ', 'Value review']) {
      expect(html).not.toContain(modelOnly)
    }
  })

  it('keeps model-priced (historical) tickets unchanged', () => {
    const html = renderToStaticMarkup(
      <TicketCard ticket={ticket} tierName="Aggressive" tierDesc="Q ≥75" color="red" />,
    )
    expect(html).toContain('Hit probability')
    expect(html).toContain('Avg Q-score')
    expect(html).not.toContain('Fair-priced')
    expect(html).not.toContain('Chance to win')
  })

  it('omits horizon labelling for a same-day ticket', () => {
    const html = renderToStaticMarkup(
      <TicketCard ticket={ticket} tierName="Balanced" tierDesc="Q ≥80" color="blue" />,
    )
    expect(html).not.toContain('Includes games to')
    expect(html).not.toContain('Fri 28 Aug')
  })

  it('renders a clear empty persisted-ticket state', () => {
    const html = renderToStaticMarkup(
      <TicketCard ticket={null} tierName="Conservative" tierDesc="Q ≥85" color="green" />,
    )
    expect(html).toContain('No ticket found')
    expect(html).toContain('No combination satisfied every conservative rule')
    expect(html).toContain('distinct-match, grade-mix, odds, and correlation limits')
  })

  it('shows rejected-selection reason codes', () => {
    const html = renderToStaticMarkup(
      <SelectionPanel
        title="Rejected matches"
        empty="None"
        rejected
        rows={[{
          prediction_id: 4,
          match_id: 5,
          home_team: 'Alpha',
          away_team: 'Beta',
          competition: 'Test League',
          kickoff_at: '2026-08-28T12:00:00Z',
          market: 'home_win',
          selection: 'home_win',
          q_score: 74,
          edge: -0.01,
          best_odds: 1.8,
          reason_codes: ['Q_SCORE_BELOW_TIER', 'NON_POSITIVE_EDGE'],
        }]}
      />,
    )
    expect(html).toContain('Q SCORE BELOW TIER')
    expect(html).toContain('NON POSITIVE EDGE')
  })

  it('keeps long tickets compact with an explicit expansion control', () => {
    const legs = Array.from({ length: 5 }, (_, index) => ({
      ...ticket.legs[0],
      selection_id: index + 1,
      prediction_id: index + 10,
      home_team: `Home ${index + 1}`,
    }))
    const html = renderToStaticMarkup(
      <TicketCard ticket={{ ...ticket, legs }} tierName="Aggressive" tierDesc="Q ≥75" color="red" onSelectLeg={() => undefined} />,
    )
    expect(html).toContain('View all 5 legs')
    expect(html).toContain('Home 4')
    expect(html).not.toContain('Home 5')
  })

  it('renders probability evidence and audit context in the selection drawer', () => {
    const html = renderToStaticMarkup(
      <SelectionDetail selection={ticket.legs[0]} modelVersion="0.2.1" publishedAt={ticket.published_at} onClose={() => undefined} />,
    )
    expect(html).toContain('Probability evidence')
    expect(html).toContain('Lohela model')
    expect(html).toContain('Market implied')
    expect(html).toContain('Probabilities are estimates, not guarantees')
    expect(html).toContain('0.2.1')
    expect(html).toContain('detail-bet-panel')
    expect(html).toContain('Stake amount')
    expect(html).toContain('Confirm individual bet')
    expect(html).toContain('Copy research summary')
  })

  it('labels a fair-priced leg as a fair chance, not a model estimate', () => {
    const html = renderToStaticMarkup(
      <SelectionDetail
        selection={{ ...ticket.legs[0], model_probability: 0.79, best_odds: 1.21, edge: -0.036, expected_value: -0.044 }}
        pricing="market"
        onClose={() => undefined}
      />,
    )
    expect(html).toContain('Fair chance (margin removed)')
    expect(html).toContain('Quoted price implies')
    expect(html).toContain('Price vs fair')
    expect(html).not.toContain('Lohela model')
    expect(html).not.toContain('No positive edge')
  })

  it('explains when individual-bet confirmation is unavailable', () => {
    const candidate = { ...ticket.legs[0], selection_id: undefined }
    const html = renderToStaticMarkup(
      <SelectionDetail selection={candidate} onClose={() => undefined} />,
    )
    expect(html).toContain('Available only for a published ticket leg')
    expect(html).toContain('aria-describedby="individual-bet-availability"')
    expect(html).toContain('disabled=""')
  })

  it('suppresses unverified edge and EV figures and labels learning provenance', () => {
    const reviewedTicket: Ticket = {
      ...ticket,
      status: 'settled',
      result: 'lost',
      expected_value: 84.422,
      avg_edge: 0.41,
      settled_at: '2026-08-28T18:00:00Z',
      settlement_source: 'pipeline',
      settlement_version: 2,
      legs: [{
        ...ticket.legs[0],
        edge: 0.41,
        result: 'lost',
        source_odds_at: '2026-08-28T09:00:00Z',
      }],
    }
    const html = renderToStaticMarkup(
      <TicketCard ticket={reviewedTicket} tierName="Balanced" tierDesc="Q ≥80" color="blue" />,
    )
    expect(html).toContain('Unverified')
    expect(html).not.toContain('+8442.2%')
    expect(html).toContain('settlement revision 2')
    expect(html).toContain('Learning evidence recorded')
    expect(html).toContain('published ticket remains immutable')
  })

  it('blocks stale and already-started selections from personal accumulators', () => {
    const base = {
      prediction_id: 4,
      match_id: 5,
      home_team: 'Alpha',
      away_team: 'Beta',
      competition: 'Test League',
      kickoff_at: '2026-08-28T12:00:00Z',
      market: 'home_win',
      selection: 'home_win',
      q_score: 90,
      edge: 0.1,
      best_odds: 1.8,
    }
    expect(accumulatorBlockers({ ...base, match_status: 'scheduled', reason_codes: ['STALE_ODDS'] })).toContain('STALE_ODDS')
    expect(accumulatorBlockers({ ...base, match_status: 'finished' })).toContain('MATCH_NOT_UPCOMING')
    expect(accumulatorBlockers({ ...base, match_status: 'scheduled' })).toEqual([])
  })
})
