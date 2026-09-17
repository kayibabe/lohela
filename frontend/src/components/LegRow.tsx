import type { Leg } from '../types'
import { formatKickoff, formatOdds, formatPct, formatEV, gradeColor, marketLabel } from '../utils'

interface Props {
  leg: Leg
  index: number
}

export function LegRow({ leg, index }: Props) {
  return (
    <div className="leg-row">
      <div className="leg-index">{index + 1}</div>
      <div className="leg-body">
        <div className="leg-match">
          <span className="leg-teams">{leg.home_team} <span className="vs">v</span> {leg.away_team}</span>
          <span className="leg-meta">
            <span className="leg-comp">{leg.competition}</span>
            <span className="leg-ko kickoff-time">{formatKickoff(leg.kickoff_at)}</span>
          </span>
        </div>
        <div className="leg-market">
          <span className="market-chip">{marketLabel(leg.market)}</span>
        </div>
      </div>
      <div className="leg-stats">
        <div className="leg-stat">
          <span className="stat-label">Prob</span>
          <span className="stat-value">{leg.model_probability == null ? 'Pro only' : formatPct(leg.model_probability)}</span>
        </div>
        <div className="leg-stat">
          <span className="stat-label">Odds</span>
            <span className="stat-value odds-value">{leg.best_odds == null ? 'Pro only' : formatOdds(leg.best_odds)}</span>
        </div>
        {leg.edge != null && (
          <div className="leg-stat">
            <span className="stat-label">Edge</span>
            <span className={`stat-value ${leg.edge >= 0 ? 'positive' : 'negative'}`}>
              {formatEV(leg.edge)}
            </span>
          </div>
        )}
        <div className="leg-stat">
          <span className="stat-label">Q</span>
          <span className={`stat-value q-badge ${gradeColor(leg.q_grade ?? 'C')}`}>
            {leg.q_score == null ? 'Pro only' : leg.q_score.toFixed(1)}
          </span>
        </div>
      </div>
    </div>
  )
}
