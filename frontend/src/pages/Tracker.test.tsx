import { describe, expect, it } from 'vitest'
import { latestTicketCohorts, wilsonInterval } from '../components/PaperLedger'
import type { TicketHistoryItem } from '../lib/api'
import { groupJournalBets, groupMatchHistory, type Bet } from '../lib/trackerGrouping'

const historyRow = (ticketId: number, version: number, targetDate = '2026-08-30'): TicketHistoryItem => ({
  ticket_id: ticketId,
  target_date: targetDate,
  ticket_type: 'balanced',
  name: 'Balanced',
  status: 'settled',
  version,
  leg_count: 4,
  combined_odds: 8,
  adjusted_probability: .4,
  risk_score: 40,
  avg_q_score: 84,
  model_version: '0.2.1',
  published_at: `${targetDate}T08:00:00Z`,
  publication_hash: `${ticketId}`.repeat(64).slice(0, 64),
  relaxed_tier: false,
  internal_only: false,
  result: 'lost',
  stake: 1,
  return_amount: 0,
  profit_loss: -1,
  settled_at: `${targetDate}T20:00:00Z`,
})

const bet = (id: number, date: string): Bet => ({
  id,
  label: `Bet ${id}`,
  odds: 2,
  stake: 10,
  potential_return: 20,
  ticket_type: null,
  ticket_date: date,
  status: 'pending',
  actual_return: null,
  profit_loss: null,
  roi: null,
  notes: null,
  created_at: `${date}T08:00:00Z`,
  source_selection_id: null,
  match_id: null,
  market: null,
  selection: null,
})

describe('Tracker evidence helpers', () => {
  it('keeps only the latest displayed cohort while retaining prior versions for audit', () => {
    const rows = [historyRow(1, 1), historyRow(2, 2), historyRow(3, 1, '2026-08-29')]
    const latest = latestTicketCohorts(rows)
    expect(latest).toHaveLength(2)
    expect(latest.find(row => row.target_date === '2026-08-30')?.version).toBe(2)
  })

  it('reports a wide confidence interval for the current small ticket sample', () => {
    const [low, high] = wilsonInterval(3, 11)
    expect(low).toBeCloseTo(.0975, 3)
    expect(high).toBeCloseTo(.5656, 3)
  })

  it('groups journal rows once by year, month and date', () => {
    const groups = groupJournalBets([bet(1, '2026-08-30'), bet(2, '2026-08-29'), bet(3, '2025-12-31')])
    expect(groups.map(group => group.year)).toEqual(['2026', '2025'])
    expect(groups[0].months).toHaveLength(1)
    expect(groups[0].months[0].dates).toHaveLength(2)
  })

  it('does not repeat year and month headings for each match date', () => {
    const row = (id: number, date: string) => ({ match_id: id, target_date: date, kickoff_at: `${date}T12:00:00Z`, home_team: 'A', away_team: 'B', competition: 'League', status: 'finished', live_phase: null, elapsed_minutes: null, home_goals: 1, away_goals: 0, outcome: 'won', ticket_types: ['safe'], selections: ['home_win'], selection_evidence: [] })
    const groups = groupMatchHistory([row(1, '2026-08-30'), row(2, '2026-08-29')])
    expect(groups).toHaveLength(1)
    expect(groups[0].months).toHaveLength(1)
    expect(groups[0].months[0].matchesByDate).toHaveLength(2)
  })
})
