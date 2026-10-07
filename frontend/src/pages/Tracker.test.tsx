import { describe, expect, it } from 'vitest'
import { accumulatorPerformance, latestTicketCohorts, roiFromTotals, simulatorTotals, wilsonInterval } from '../components/PaperLedger'
import type { CustomAccumulator, TicketHistoryItem } from '../lib/api'
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
  it('keeps open accumulator exposure separate from settled performance', () => {
    const row = (id: number, status: CustomAccumulator['status'], stake: number, actualReturn: number | null): CustomAccumulator => ({
      id, name: `Accu-${id}`, target_date: '2026-10-06', status, stake, combined_odds: 2,
      potential_return: stake * 2, actual_return: actualReturn, created_at: '2026-10-06T08:00:00Z',
      placed_at: '2026-10-06T08:01:00Z', settled_at: status === 'placed' ? null : '2026-10-06T20:00:00Z', automatic: true,
      legs: [],
    })
    const summary = accumulatorPerformance([row(1, 'placed', 5000, null), row(2, 'won', 1, 2.5), row(3, 'lost', 1, 0)])
    expect(summary).toMatchObject({ tickets: 3, open: 1, settled: 2, won: 1, lost: 1, exposure: 5000, settledStake: 2, returned: 2.5, profitLoss: 0.5 })
  })

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

  it('calculates simulator ROI from simulated P&L and stake', () => {
    expect(roiFromTotals(5.87, 17)).toBeCloseTo(0.345294, 5)
    expect(roiFromTotals(-2, 10)).toBe(-0.2)
    expect(roiFromTotals(0, 0)).toBeNull()
  })

  it('calculates accumulator simulator totals from accumulator rows, not ticket rows', () => {
    const totals = simulatorTotals([
      { settled: true, returnAmount: 43.21 },
    ], 1)
    expect(totals).toEqual({ staked: 1, returned: 43.21, profitLoss: 42.21 })
  })

  it('groups journal rows once by year, month and date', () => {
    const groups = groupJournalBets([bet(1, '2026-08-30'), bet(2, '2026-08-29'), bet(3, '2025-12-31')])
    expect(groups.map(group => group.year)).toEqual(['2026', '2025'])
    expect(groups[0].months).toHaveLength(1)
    expect(groups[0].months[0].dates).toHaveLength(2)
  })

  it('groups match history by newest year, month and date without repeated headings', () => {
    const row = (id: number, date: string) => ({ match_id: id, target_date: date, kickoff_at: `${date}T12:00:00Z`, home_team: 'A', away_team: 'B', competition: 'League', status: 'finished', live_phase: null, elapsed_minutes: null, home_goals: 1, away_goals: 0, outcome: 'won', ticket_types: ['safe'], selections: ['home_win'], selection_evidence: [] })
    const groups = groupMatchHistory([row(1, '2025-12-31'), row(2, '2026-08-29'), row(3, '2026-09-01'), row(4, '2026-08-30')])
    expect(groups.map(group => group.year)).toEqual([2026, 2025])
    expect(groups[0].months.map(month => month.month)).toEqual(['2026-09', '2026-08'])
    expect(groups[0].months[1].matchesByDate.map(date => date.date)).toEqual(['2026-08-30', '2026-08-29'])
  })
})
