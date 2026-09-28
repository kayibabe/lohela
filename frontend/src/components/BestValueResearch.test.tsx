import { describe, expect, it } from 'vitest'
import { bestValueCoverageGaps } from './BestValueResearch'
import type { TicketHistoryItem } from '../lib/api'

function row(target_date: string): TicketHistoryItem {
  return {
    ticket_id: 1, target_date, ticket_type: 'best_value', name: 'Best Value', status: 'settled', version: 1,
    leg_count: 3, combined_odds: 3.2, adjusted_probability: 0.3, risk_score: 20, avg_q_score: 85,
    model_version: 'test', published_at: `${target_date}T00:00:00Z`, publication_hash: 'test', relaxed_tier: false,
    internal_only: true, result: 'won', stake: 1, return_amount: 3.2, profit_loss: 2.2, settled_at: null,
  }
}

describe('Best Value research coverage', () => {
  it('shows absent days as evidence gaps instead of retroactive tickets', () => {
    expect(bestValueCoverageGaps([row('2026-09-20'), row('2026-09-24')], '2026-09-24')).toEqual([
      '2026-09-21', '2026-09-22', '2026-09-23',
    ])
  })
})
