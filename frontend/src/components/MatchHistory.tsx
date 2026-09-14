import { useEffect, useMemo, useState } from 'react'
import { formatMarket, formatSelection, formatTicketType } from '../lib/api'
import { groupMatchHistory, type MatchHistoryRow } from '../lib/trackerGrouping'

export default function MatchHistory() {
  const [rows, setRows] = useState<MatchHistoryRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [outcome, setOutcome] = useState('all')
  const [market, setMarket] = useState('all')
  const [selection, setSelection] = useState('all')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')

  useEffect(() => {
    fetch('/api/v1/tickets/matches/history')
      .then(async response => {
        if (!response.ok) throw new Error(`Match history failed (${response.status})`)
        return response.json()
      })
      .then(setRows)
      .catch((reason: Error) => setError(reason.message || 'Match history unavailable'))
      .finally(() => setLoading(false))
  }, [])

  const markets = useMemo(() => [...new Set(rows.flatMap(row => row.selection_evidence.map(item => item.market)))].sort(), [rows])
  const selections = useMemo(() => [...new Set(rows.flatMap(row => row.selection_evidence.map(item => item.selection)))].sort(), [rows])
  const visible = useMemo(() => rows.filter(row =>
    (outcome === 'all' || (row.outcome ?? 'pending') === outcome)
    && (market === 'all' || row.selection_evidence.some(item => item.market === market))
    && (selection === 'all' || row.selection_evidence.some(item => item.selection === selection))
    && (!dateFrom || row.target_date >= dateFrom)
    && (!dateTo || row.target_date <= dateTo)
  ), [rows, outcome, market, selection, dateFrom, dateTo])
  const groups = useMemo(() => groupMatchHistory(visible), [visible])

  if (loading) return <div className="paper-ledger-skeleton" aria-busy="true">Loading published match evidence…</div>
  if (error) return <div className="analytics-empty"><strong>Match history unavailable</strong><span>{error}</span></div>
  if (!rows.length) return <div className="analytics-empty"><strong>No published match history</strong><span>Matches will appear after tickets are published.</span></div>

  return <>
    <div className="match-history-intro"><div><span className="eyebrow">Selection-level audit</span><strong>Published match evidence</strong><p>Market describes the model category; pick describes the exact selection that was published.</p></div><span>{visible.length} of {rows.length} matches</span></div>
    <div className="match-history-toolbar tracker-filter-panel">
      <label>Outcome<select value={outcome} onChange={event => setOutcome(event.target.value)}><option value="all">All outcomes</option><option value="won">Won</option><option value="lost">Lost</option><option value="void">Void</option><option value="pending">Pending</option><option value="mixed">Mixed</option></select></label>
      <label>Market<select value={market} onChange={event => setMarket(event.target.value)}><option value="all">All markets</option>{markets.map(value => <option key={value} value={value}>{formatMarket(value)}</option>)}</select></label>
      <label>Pick<select value={selection} onChange={event => setSelection(event.target.value)}><option value="all">All picks</option>{selections.map(value => <option key={value} value={value}>{formatSelection(value)}</option>)}</select></label>
      <label>From<input type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)} /></label>
      <label>To<input type="date" value={dateTo} onChange={event => setDateTo(event.target.value)} /></label>
    </div>

    {!visible.length ? <div className="analytics-empty"><strong>No matches meet these filters</strong><span>Try another outcome, market, pick or date range.</span></div> : groups.map(year => <section className="match-history-year" key={year.year}>
      <h2 className="paper-year-heading">{year.year}</h2>
      {year.months.map(month => <section className="match-history-month" key={`${year.year}-${month.label}`}>
        <h3 className="paper-month-heading">{month.label}</h3>
        {month.matchesByDate.map(group => <section className="match-history-group" key={group.date}>
          <div className="paper-date-heading"><strong>{group.label}</strong><span>{group.matches.length} match{group.matches.length === 1 ? '' : 'es'}</span></div>
          <div className="match-history-list">{group.matches.map(match => <article className="match-history-row" key={match.match_id}>
            <div><strong>{match.home_team} <span>vs</span> {match.away_team}</strong><small className={`kickoff-time ${match.live_phase ? 'live-kickoff' : ''}`}>{match.live_phase ? `${({ '1st_half': '1st Half', half_time: 'Half Time', '2nd_half': '2nd Half', extra_time: 'Extra Time', penalties: 'Penalties', suspended: 'Suspended', interrupted: 'Interrupted', live: 'Live' } as Record<string, string>)[match.live_phase] ?? 'Live'}${match.elapsed_minutes != null ? ` · ${match.elapsed_minutes}'` : ''}` : 'Kickoff · '}{new Date(match.kickoff_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</small><small>{match.competition}</small></div>
            <div className="match-history-meta"><span>{match.ticket_types.map(formatTicketType).join(' · ')}</span><small className="market-badges">{match.selections.map(value => <b key={value}>{formatSelection(value)}</b>)}</small></div>
            <div className="match-history-evidence"><span className="history-evidence-heading">Projected advisory</span>{match.selection_evidence.map(item => <div key={`${item.market}:${item.selection}`}>
              <strong className="advisory-market"><span>{formatMarket(item.market)}</span>{formatSelection(item.selection) !== formatMarket(item.market) && formatSelection(item.selection)} <b className="history-metric odds">Odds {item.odds == null ? '—' : item.odds.toFixed(2)}</b></strong>
              <span className="history-advisory-metrics"><b className="history-metric q">Q {item.q_score.toFixed(1)}</b><b className="history-metric probability">{(item.model_probability * 100).toFixed(1)}% model</b><b className="history-metric spread">{item.model_spread == null ? '—' : `${(item.model_spread * 100).toFixed(1)} pp`} spread</b><b className={`history-metric ${item.edge != null && item.edge > 0 ? 'edge-positive' : 'edge-neutral'}`}>{item.edge == null ? '—' : `${(item.edge * 100).toFixed(1)}%`} edge</b></span>
              <small className={`match-outcome ${item.result}`}>{item.result}</small>
            </div>)}</div>
            <div className="match-history-score">{match.home_goals == null ? '—' : `${match.home_goals} - ${match.away_goals}`}<small className={`match-outcome ${match.outcome ?? 'pending'}`}>{match.outcome ?? 'pending'}</small></div>
          </article>)}</div>
        </section>)}
      </section>)}
    </section>)}
  </>
}
