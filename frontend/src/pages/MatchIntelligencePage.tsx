import { useEffect, useMemo, useState } from 'react'
import { formatKickoff, formatMarket, formatSelection, type MatchIntelligence } from '../lib/api'

interface Props {
  matchId: number
  onBack: () => void
}

const pct = (value: number | null | undefined, signed = false) => {
  if (value == null) return 'Unavailable'
  return `${signed && value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`
}

function statusLabel(status: string) {
  return status.replace(/_/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase())
}

export default function MatchIntelligencePage({ matchId, onBack }: Props) {
  const [data, setData] = useState<MatchIntelligence | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    fetch(`/api/v1/selections/match-intelligence/${matchId}`)
      .then(response => response.ok ? response.json() : response.json().then(body => Promise.reject(new Error(body.detail ?? `API error ${response.status}`))))
      .then(body => { if (active) setData(body) })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : 'Could not load match intelligence') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [matchId])

  const lead = useMemo(() => data?.predictions[0] ?? null, [data])

  if (loading) return <section className="analytics-page"><div className="analytics-empty"><strong>Loading match intelligence</strong><span>Reading the stored prediction, price, and pre-match context.</span></div></section>
  if (error || !data) return <section className="analytics-page"><div className="analytics-empty"><strong>Match intelligence unavailable</strong><span>{error ?? 'No persisted intelligence was found for this match.'}</span><button className="btn-secondary" onClick={onBack}>Back to Today</button></div></section>

  const score = data.home_goals == null || data.away_goals == null ? 'Unplayed' : `${data.home_goals}–${data.away_goals}`

  return (
    <section className="analytics-page match-intelligence-page">
      <div className="page-intro match-intelligence-intro">
        <div><button className="text-button" onClick={onBack}>← Back to Today</button><span className="eyebrow">Match intelligence</span><h1>{data.home_team} <span>vs</span> {data.away_team}</h1><p>{data.competition} · {formatKickoff(data.kickoff_at)} · {statusLabel(data.status)}</p></div>
        <div className="match-intelligence-verdict"><span>Primary stored verdict</span><strong className={`recommendation-badge ${(lead?.recommendation_status ?? 'WATCH').toLowerCase()}`}>{lead?.recommendation_status ?? 'WATCH'}</strong><small>{lead ? `${formatMarket(lead.market)} · ${formatSelection(lead.selection)}` : 'No prediction snapshot'}</small></div>
      </div>

      <div className="detail-metrics match-intelligence-metrics">
        <Metric label="Model probability" value={pct(lead?.model_probability)} />
        <Metric label="Best captured odds" value={lead?.best_odds?.toFixed(2) ?? 'Unavailable'} />
        <Metric label="Edge" value={pct(lead?.edge, true)} />
        <Metric label="Q score" value={lead?.q_score.toFixed(1) ?? 'Unavailable'} />
        <Metric label="Data quality" value={`${data.data_quality_score.toFixed(0)}/100`} note={statusLabel(data.data_quality_status)} />
        <Metric label="Score" value={score} />
      </div>

      <section className="evidence-card match-intelligence-evidence"><div className="evidence-title"><h2>Executive decision summary</h2><span>Snapshot-backed · no recalculation</span></div><div className="explainability-grid"><div><strong>Why consider it</strong>{lead?.recommendation_reasons.length ? <ul>{lead.recommendation_reasons.slice(0, 5).map(reason => <li key={reason}>{statusLabel(reason)}</li>)}</ul> : <span>No positive reason was persisted.</span>}</div><div><strong>Risks and unknowns</strong>{lead?.recommendation_risks.length ? <ul>{lead.recommendation_risks.slice(0, 5).map(risk => <li key={risk}>{statusLabel(risk)}</li>)}</ul> : <span>No additional risk code was persisted.</span>}</div></div></section>

      <section className="evidence-card"><div className="evidence-title"><h2>Ranked market probabilities</h2><span>{data.predictions.length} stored market snapshots</span></div>{data.predictions.length === 0 ? <div className="detail-empty-note">No prediction snapshot exists for this match.</div> : <div className="match-intelligence-table-wrap"><table className="match-intelligence-table"><thead><tr><th>Market</th><th>Probability</th><th>Odds</th><th>Edge</th><th>Q</th><th>Decision</th><th>Evidence</th></tr></thead><tbody>{data.predictions.map(row => <tr key={row.prediction_id}><td><strong>{formatMarket(row.market)}</strong><small>{formatSelection(row.selection)}</small></td><td>{pct(row.model_probability)}</td><td>{row.best_odds?.toFixed(2) ?? 'Unavailable'}</td><td>{pct(row.edge, true)}</td><td>{row.q_score.toFixed(1)} · {row.q_grade}</td><td><span className={`recommendation-badge ${row.recommendation_status.toLowerCase()}`}>{row.recommendation_status}</span></td><td><small>{row.active_models.length ? `${row.active_models.length} active models` : 'Model set unavailable'}</small></td></tr>)}</tbody></table></div>}</section>

      <div className="match-intelligence-columns"><section className="evidence-card"><div className="evidence-title"><h2>Pre-match form</h2><span>Finished matches before kickoff</span></div><div className="form-teams"><TeamForm team={data.context.home} /><span className="form-vs">vs</span><TeamForm team={data.context.away} /></div></section><section className="evidence-card"><div className="evidence-title"><h2>Traceability</h2><span>Reproducibility fields</span></div><div className="audit-panel"><div><span>Match ID</span><code>{data.match_id}</code></div><div><span>Kickoff</span><code>{new Date(data.kickoff_at).toLocaleString()}</code></div><div><span>Information set</span><code>{lead?.as_of_at ? new Date(lead.as_of_at).toLocaleString() : 'Unavailable'}</code></div><div><span>Odds timestamp</span><code>{lead?.source_odds_at ? new Date(lead.source_odds_at).toLocaleString() : 'Unavailable'}</code></div></div><p className="detail-disclaimer">{data.evidence_note}</p></section></div>

      <section className="evidence-card"><div className="evidence-title"><h2>Head-to-head</h2><span>Persisted finished meetings</span></div>{data.context.h2h.length ? <div className="h2h-list">{data.context.h2h.map(game => <div key={game.date + game.score}><span>{game.date}</span><strong>{game.home_team} {game.score} {game.away_team}</strong></div>)}</div> : <div className="detail-empty-note">No previous meetings found in the persisted data.</div>}</section>
    </section>
  )
}

function Metric({ label, value, note }: { label: string; value: string; note?: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</div>
}

function TeamForm({ team }: { team: MatchIntelligence['context']['home'] }) {
  return <div className="team-form"><strong>{team.team}</strong><div className="form-dots">{team.results.length ? team.results.map((result, index) => <span key={`${result}-${index}`} className={result.toLowerCase()}>{result}</span>) : <span className="empty">No history</span>}</div><small>{team.matches} matches · {team.points_per_game.toFixed(2)} points/game · {team.goals_for}–{team.goals_against}</small></div>
}
