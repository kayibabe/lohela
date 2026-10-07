import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { CustomAccumulatorPanel } from './CustomAccumulatorPanel'
import type { CustomAccumulator } from './types'

const ticket: CustomAccumulator = {
  id: 1,
  name: 'My accumulator',
  date: '2026-10-07',
  status: 'placed',
  automatic: true,
  stake: 10,
  legs: [{
    prediction_id: 1,
    match_id: 2,
    home_team: 'Sport Recife',
    away_team: 'São Bernardo',
    competition: 'Brasileirão Série B',
    kickoff_at: '2026-10-07T00:30:00Z',
    market: 'home_win',
    selection: 'home_win',
    q_score: 62.4,
    edge: 0.02,
    best_odds: 1.26,
    result: null,
  }],
}

describe('CustomAccumulatorPanel', () => {
  it('shows each personal accumulator leg date, time, market, and odds distinctly', () => {
    const html = renderToStaticMarkup(
      <CustomAccumulatorPanel
        date="2026-10-07"
        tickets={[ticket]}
        onCreate={async () => undefined}
        onUpdate={async () => undefined}
        onDelete={async () => undefined}
        onNavigate={() => undefined}
      />,
    )

    expect(html).toContain('Wed, 7 Oct 2026')
    expect(html).toMatch(/\d{2}:30/)
    expect(html).toContain('Home Win')
    expect(html).toContain('1.26×')
    expect(html).toContain('class="custom-leg-date"')
    expect(html).toContain('class="custom-leg-time"')
    expect(html).toContain('class="custom-leg-market"')
    expect(html).toContain('class="custom-leg-odds"')
  })
})
