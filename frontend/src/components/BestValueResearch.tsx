import { useEffect, useMemo, useState } from 'react'
import { fetchTicket, fetchTicketHistory, formatKickoff, formatMarket, type Ticket, type TicketHistoryItem } from '../lib/api'
import { fmtPnl } from '../utils/currency'

const CAT = 'Africa/Blantyre'

function catToday() {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: CAT, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts()
  const part = (type: string) => parts.find(item => item.type === type)?.value ?? ''
  return `${part('year')}-${part('month')}-${part('day')}`
}

export function bestValueCoverageGaps(rows: TicketHistoryItem[], through = catToday()) {
  const dates = [...new Set(rows.map(row => row.target_date))].sort()
  if (!dates.length) return []
  const gaps: string[] = []
  for (let day = new Date(`${dates[0]}T00:00:00Z`); day.toISOString().slice(0, 10) <= through; day.setUTCDate(day.getUTCDate() + 1)) {
    const value = day.toISOString().slice(0, 10)
    if (!dates.includes(value)) gaps.push(value)
  }
  return gaps
}

const labelDate = (date: string) => new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })

export default function BestValueResearch() {
  const [rows, setRows] = useState<TicketHistoryItem[]>([])
  const [expanded, setExpanded] = useState<number | null>(null)
  const [ticket, setTicket] = useState<Ticket | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    fetchTicketHistory(true, true)
      .then(history => setRows(history.filter(row => row.ticket_type === 'best_value')))
      .catch(reason => setError(reason instanceof Error ? reason.message : 'Best Value research is unavailable'))
      .finally(() => setLoading(false))
  }, [])

  const latestRows = useMemo(() => {
    const latest = new Map<string, TicketHistoryItem>()
    for (const row of rows) {
      const current = latest.get(row.target_date)
      if (!current || row.version > current.version || (row.version === current.version && row.ticket_id > current.ticket_id)) latest.set(row.target_date, row)
    }
    return [...latest.values()].sort((a, b) => b.target_date.localeCompare(a.target_date))
  }, [rows])
  const gaps = useMemo(() => bestValueCoverageGaps(latestRows), [latestRows])

  async function toggle(row: TicketHistoryItem) {
    if (expanded === row.ticket_id) { setExpanded(null); setTicket(null); return }
    setExpanded(row.ticket_id); setTicket(null)
    try { setTicket(await fetchTicket(row.ticket_id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Ticket details are unavailable') }
  }

  if (loading) return <div className="paper-ledger-skeleton" aria-busy="true">Loading protected Best Value research…</div>
  if (error && !latestRows.length) return <div className="analytics-empty"><strong>Best Value research unavailable</strong><span>{error}</span></div>

  return <section className="best-value-research" aria-labelledby="best-value-research-title">
    {error && <div className="tracker-message error" role="alert">{error}</div>}
    <div className="paper-scope-heading">
      <div><span className="eyebrow">Protected internal research</span><strong id="best-value-research-title">Best Value accumulator history</strong><p>Exploratory paper research only. It is excluded from public recommendations and release KPIs.</p></div>
      <span className="internal-badge">Internal only</span>
    </div>
    {gaps.length > 0 && <section className="publication-diagnostics best-value-gap-notice" role="status">
      <div><span className="generation-status-badge">Evidence gap</span><h3>No auditable Best Value ticket for {gaps.map(labelDate).join(', ')}.</h3></div>
      <p>These dates are retained as missing evidence. They are not reconstructed after the fact because their required pre-kickoff prediction and odds snapshots are unavailable.</p>
    </section>}
    {!latestRows.length ? <div className="analytics-empty"><strong>No Best Value research has been captured yet.</strong><span>New protected research tickets will appear here after publication.</span></div> : <div className="paper-ledger-list">
      {latestRows.map(row => <article className={`paper-ledger-row${expanded === row.ticket_id ? ' expanded' : ''}`} key={row.ticket_id}>
        <button className="paper-ticket-summary" onClick={() => toggle(row)} aria-expanded={expanded === row.ticket_id}>
          <div className="paper-ticket-identity"><span className="portfolio-tier best_value">Best Value</span><small>{labelDate(row.target_date)} · v{row.version} · model {row.model_version}</small></div>
          <div><span>Odds</span><strong>{row.combined_odds == null ? '—' : `${row.combined_odds.toFixed(2)}×`}</strong></div><div><span>Legs</span><strong>{row.leg_count}</strong></div><div><span>Outcome</span><strong className={row.result === 'won' ? 'positive' : row.result === 'lost' ? 'negative' : ''}>{row.result ?? 'pending'}</strong></div><div><span>P&amp;L</span><strong className={(row.profit_loss ?? 0) >= 0 ? 'positive' : 'negative'}>{row.profit_loss == null ? '—' : fmtPnl(row.profit_loss)}</strong></div><span className="paper-chevron" aria-hidden="true">{expanded === row.ticket_id ? '⌃' : '⌄'}</span>
        </button>
        {expanded === row.ticket_id && <div className="paper-ticket-detail">{ticket ? <ul>{ticket.legs.map(leg => <li key={leg.selection_id}><div><strong>{leg.home_team} <span>vs</span> {leg.away_team}</strong><small>{leg.competition} · {formatKickoff(leg.kickoff_at)}</small><b className="paper-market-badge">{formatMarket(leg.market)} · {leg.selection}</b></div><div><strong>{leg.best_odds?.toFixed(2) ?? '—'}</strong><small className={`paper-leg-result ${leg.result}`}>{leg.result}</small></div></li>)}</ul> : <div className="odds-state">Loading immutable selections…</div>}</div>}
      </article>)}
    </div>}
  </section>
}
