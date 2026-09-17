import { useEffect, useMemo, useState } from 'react'
import { fetchTicket, fetchTicketHistory, formatKickoff, formatMarket, formatTicketType, type Ticket, type TicketHistoryItem } from '../lib/api'
import GradeBadge from './GradeBadge'
import { fmt, fmtPnl } from '../utils/currency'

interface TicketTypePerformance { ticket_type: string; tickets: number; wins: number; stake: number; return: number; hit_rate: number; roi: number; profit_loss: number }
interface EvidenceGateSummary { official_latest_cohorts: number; official_settled_cohorts: number; published_days: number; settled_days: number; unique_official_selections: number; priced_selections: number; pre_kickoff_odds_selections: number }
interface PerformanceSummary {
  current_model_version: string; published_tickets: number; settled_tickets: number; wins: number; losses: number; hit_rate: number
  hit_rate_confidence_interval_95: [number, number]; profit_loss: number; roi: number; max_drawdown_units: number
  selection_sample_size: number; brier_score: number | null; calibration_error: number | null
  by_ticket_type: TicketTypePerformance[]; evidence_gate?: EvidenceGateSummary
}

const pct = (value: number) => `${(value * 100).toFixed(1)}%`
const cohortKey = (row: TicketHistoryItem) => `${row.target_date}:${row.ticket_type}`

export function latestTicketCohorts(rows: TicketHistoryItem[]) {
  const latest = new Map<string, TicketHistoryItem>()
  for (const row of rows) {
    const key = cohortKey(row)
    const current = latest.get(key)
    if (!current || row.version > current.version || (row.version === current.version && row.ticket_id > current.ticket_id)) latest.set(key, row)
  }
  return [...latest.values()].sort((a, b) => b.target_date.localeCompare(a.target_date) || a.ticket_type.localeCompare(b.ticket_type))
}

export function wilsonInterval(wins: number, total: number): [number, number] {
  if (!total) return [0, 0]
  const z = 1.96, rate = wins / total, denominator = 1 + z * z / total
  const centre = rate + z * z / (2 * total)
  const spread = z * Math.sqrt((rate * (1 - rate) + z * z / (4 * total)) / total)
  return [Math.max(0, (centre - spread) / denominator), Math.min(1, (centre + spread) / denominator)]
}

function drawdown(rows: TicketHistoryItem[]) {
  let cumulative = 0, peak = 0, maximum = 0
  for (const row of [...rows].sort((a, b) => a.target_date.localeCompare(b.target_date) || a.ticket_id - b.ticket_id)) {
    cumulative += row.profit_loss ?? 0; peak = Math.max(peak, cumulative); maximum = Math.max(maximum, peak - cumulative)
  }
  return maximum
}

export default function PaperLedger() {
  const [allVersions, setAllVersions] = useState<TicketHistoryItem[]>([])
  const [performance, setPerformance] = useState<PerformanceSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const [resultFilter, setResultFilter] = useState('all')
  const [modelFilter, setModelFilter] = useState('current')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [ticket, setTicket] = useState<Ticket | null>(null)
  const [compareTicket, setCompareTicket] = useState<Ticket | null>(null)
  const [selectedVersion, setSelectedVersion] = useState<TicketHistoryItem | null>(null)
  const [ticketLoading, setTicketLoading] = useState(false)
  const [stakeSize, setStakeSize] = useState('1')

  useEffect(() => {
    Promise.all([
      fetchTicketHistory(true),
      fetch('/api/v1/performance/summary').then(async response => {
        if (!response.ok) throw new Error(`Performance summary failed (${response.status})`)
        return response.json()
      }),
    ]).then(([history, summary]) => { setAllVersions(history); setPerformance(summary) })
      .catch((reason: Error) => setError(reason.message || 'Paper portfolio unavailable'))
      .finally(() => setLoading(false))
  }, [])

  const latestRows = useMemo(() => latestTicketCohorts(allVersions), [allVersions])
  const modelVersions = useMemo(() => [...new Set(latestRows.map(row => row.model_version))].sort().reverse(), [latestRows])
  const currentModel = performance?.current_model_version
  const scopeRows = useMemo(() => latestRows.filter(row =>
    (modelFilter === 'all' || row.model_version === (modelFilter === 'current' ? currentModel : modelFilter))
    && (!dateFrom || row.target_date >= dateFrom) && (!dateTo || row.target_date <= dateTo)
  ), [latestRows, modelFilter, currentModel, dateFrom, dateTo])
  const filtered = useMemo(() => scopeRows.filter(row =>
    (typeFilter === 'all' || row.ticket_type === typeFilter)
    && (resultFilter === 'all' || (row.result ?? 'pending') === resultFilter)
  ), [scopeRows, typeFilter, resultFilter])

  const groupedRows = useMemo(() => {
    const byDate = new Map<string, TicketHistoryItem[]>()
    for (const row of filtered) byDate.set(row.target_date, [...(byDate.get(row.target_date) ?? []), row])
    return [...byDate.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([date, dateRows]) => {
      const parsed = new Date(`${date}T00:00:00`)
      return { date, year: parsed.getFullYear(), month: parsed.toLocaleDateString(undefined, { month: 'long' }), label: parsed.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' }), rows: dateRows }
    })
  }, [filtered])

  const productionRows = scopeRows.filter(row => !row.internal_only)
  const internalRows = scopeRows.filter(row => row.internal_only)
  const productionSettled = productionRows.filter(row => row.result && row.result !== 'pending')
  const productionWins = productionSettled.filter(row => row.result === 'won').length
  const productionStaked = productionSettled.reduce((sum, row) => sum + (row.stake ?? 0), 0)
  const productionPnl = productionSettled.reduce((sum, row) => sum + (row.profit_loss ?? 0), 0)
  const productionInterval = wilsonInterval(productionWins, productionSettled.length)
  const internalPnl = internalRows.reduce((sum, row) => sum + (row.profit_loss ?? 0), 0)
  const distinctPublishedDays = new Set(productionRows.map(row => row.target_date)).size
  const distinctSettledDays = new Set(productionSettled.map(row => row.target_date)).size
  const firstDate = productionRows.length ? productionRows.map(row => row.target_date).sort()[0] : null
  const observedDays = firstDate ? Math.max(1, Math.floor((Date.now() - new Date(`${firstDate}T00:00:00`).getTime()) / 86_400_000) + 1) : 0
  const observationProgress = Math.min(100, observedDays / 56 * 100)
  const stake = Math.max(0, Number(stakeSize) || 0)
  const simulatedRows = filtered.filter(row => row.result && row.result !== 'pending')
  const simulatedStaked = simulatedRows.length * stake
  const simulatedReturned = simulatedRows.reduce((sum, row) => sum + (row.return_amount ?? 0) * stake, 0)
  const simulatedPnl = simulatedReturned - simulatedStaked
  const evidence = performance?.evidence_gate
  const pricedCoverage = evidence?.unique_official_selections ? evidence.priced_selections / evidence.unique_official_selections : null
  const preKickoffCoverage = evidence?.unique_official_selections ? evidence.pre_kickoff_odds_selections / evidence.unique_official_selections : null

  async function loadVersion(row: TicketHistoryItem) {
    setSelectedVersion(row); setTicket(null); setCompareTicket(null); setTicketLoading(true)
    try {
      const versions = allVersions.filter(item => cohortKey(item) === cohortKey(row))
      const previous = versions.find(item => item.version === row.version - 1)
      const [active, comparison] = await Promise.all([fetchTicket(row.ticket_id), previous ? fetchTicket(previous.ticket_id) : Promise.resolve(null)])
      setTicket(active); setCompareTicket(comparison)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Ticket version unavailable')
    } finally { setTicketLoading(false) }
  }

  async function toggleTicket(row: TicketHistoryItem) {
    if (expandedId === row.ticket_id) { setExpandedId(null); setTicket(null); setCompareTicket(null); setSelectedVersion(null); return }
    setExpandedId(row.ticket_id); await loadVersion(row)
  }

  if (loading) return <div className="paper-ledger-skeleton" aria-busy="true">Loading immutable paper portfolio…</div>
  if (error && !allVersions.length) return <div className="analytics-empty"><strong>Paper portfolio unavailable</strong><span>{error}</span></div>

  return <>
    {error && <div className="tracker-message error" role="alert">{error}</div>}
    <div className="paper-scope-heading">
      <div><span className="eyebrow">Production-tier evidence</span><strong>Conservative, Balanced and Aggressive only</strong><p>Internal Best Value research is reported separately and cannot carry the release headline.</p></div>
      <span className="sample-caution">Small sample · {productionWins} wins / {productionSettled.length} settled</span>
    </div>
    <div className="paper-kpis">
      <LedgerMetric label="Latest cohorts" value={productionRows.length.toString()} note={`${allVersions.length} immutable versions retained`} />
      <LedgerMetric label="Settled" value={productionSettled.length.toString()} note={`${productionRows.length - productionSettled.length} production cohorts pending`} />
      <LedgerMetric label="Hit rate" value={productionSettled.length ? pct(productionWins / productionSettled.length) : '—'} note={productionSettled.length ? `95% interval ${pct(productionInterval[0])}–${pct(productionInterval[1])}` : 'Settled production tickets only'} />
      <LedgerMetric label="Net P&L" value={productionSettled.length ? fmtPnl(productionPnl) : '—'} note="One-unit production paper stakes" tone={productionPnl >= 0 ? 'positive' : 'negative'} />
      <LedgerMetric label="ROI" value={productionStaked ? pct(productionPnl / productionStaked) : '—'} note={`${fmt(productionStaked)} settled stake · outlier-sensitive`} tone={productionPnl >= 0 ? 'positive' : 'negative'} />
      <LedgerMetric label="Drawdown" value={productionSettled.length ? fmt(drawdown(productionSettled)) : '—'} note="Peak-to-trough production units" tone="warning" />
    </div>

    {internalRows.length > 0 && <section className="internal-research-card"><div><span className="eyebrow">Internal research tier</span><strong>Best Value is excluded from release KPIs</strong><p>{internalRows.length} latest cohort{internalRows.length === 1 ? '' : 's'} · {fmtPnl(internalPnl)} paper P&amp;L. Treat this as exploratory evidence.</p></div><span className="internal-badge">Internal only</span></section>}

    <section className="validation-period-card evidence-readiness-card">
      <div><span className="eyebrow">Release evidence gate</span><strong>Evidence accumulating — no automatic pass</strong><p>{firstDate ? `Observation clock started ${new Date(`${firstDate}T00:00:00`).toLocaleDateString()} · day ${observedDays} of 56` : 'Begins with the first production-tier published cohort.'} Calendar time alone cannot approve release.</p><div className="validation-progress"><div><span>{Math.round(observationProgress)}%</span><small>{Math.max(0, 56 - observedDays)} calendar days remaining</small></div><div className="validation-progress-track"><span style={{ width: `${observationProgress}%` }} /></div></div></div>
      <div className="evidence-readiness-grid">
        <EvidenceCheck label="Published coverage" value={`${distinctPublishedDays} days`} note="Production ticket days" />
        <EvidenceCheck label="Settled coverage" value={`${distinctSettledDays} days`} note={`${productionSettled.length} production cohorts`} />
        <EvidenceCheck label="Valid priced picks" value={pricedCoverage == null ? '—' : pct(pricedCoverage)} note={`${evidence?.priced_selections ?? 0}/${evidence?.unique_official_selections ?? 0} unique picks`} />
        <EvidenceCheck label="Pre-kickoff odds" value={preKickoffCoverage == null ? '—' : pct(preKickoffCoverage)} note={`${evidence?.pre_kickoff_odds_selections ?? 0}/${evidence?.unique_official_selections ?? 0} audited picks`} />
        <EvidenceCheck label="Model scope" value={modelFilter === 'all' ? 'Mixed' : modelFilter === 'current' ? currentModel ?? 'Current' : modelFilter} note="Do not blend model releases silently" />
        <EvidenceCheck label="Calibration sample" value={(performance?.selection_sample_size ?? 0).toString()} note={performance?.calibration_error == null ? 'Calibration unavailable' : `${pct(performance.calibration_error)} calibration error`} />
      </div>
    </section>

    <section className="stake-simulator"><div><span className="eyebrow">What-if sizing</span><strong>Stake simulator · current filters</strong><p>Replays the settled rows currently shown. This does not change the immutable one-unit ledger.</p></div><label>Stake per ticket<input type="number" min="0" step="0.01" value={stakeSize} onChange={event => setStakeSize(event.target.value)} /></label><div className="stake-simulator-metrics"><div><span>Staked</span><strong>{fmt(simulatedStaked)}</strong></div><div><span>Return</span><strong>{fmt(simulatedReturned)}</strong></div><div><span>P&amp;L</span><strong className={simulatedPnl >= 0 ? 'positive' : 'negative'}>{fmtPnl(simulatedPnl)}</strong></div></div></section>

    <div className="paper-toolbar tracker-filter-panel">
      <div className="filter-tabs" aria-label="Ticket type filter">{['all', 'safe', 'balanced', 'aggressive', 'best_value'].map(value => <button key={value} className={`filter-tab${typeFilter === value ? ' active' : ''}`} onClick={() => setTypeFilter(value)}>{value === 'all' ? 'All tickets' : formatTicketType(value)}</button>)}</div>
      <label>Outcome<select aria-label="Settlement filter" value={resultFilter} onChange={event => setResultFilter(event.target.value)}><option value="all">All outcomes</option><option value="pending">Pending</option><option value="won">Won</option><option value="lost">Lost</option><option value="void">Void</option></select></label>
      <label>Model<select aria-label="Model version filter" value={modelFilter} onChange={event => setModelFilter(event.target.value)}><option value="current">Current · {currentModel ?? 'loading'}</option><option value="all">All model versions</option>{modelVersions.filter(version => version !== currentModel).map(version => <option key={version} value={version}>{version}</option>)}</select></label>
      <label>From<input aria-label="Tickets from date" type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)} /></label><label>To<input aria-label="Tickets to date" type="date" value={dateTo} onChange={event => setDateTo(event.target.value)} /></label>
    </div>

    {filtered.length === 0 ? <div className="analytics-empty"><strong>No tickets match these filters</strong><span>Try another tier, outcome, model version or date range.</span></div> : <div className="paper-ledger-list">{groupedRows.map((group, groupIndex) => <section className="paper-date-group" key={group.date}>
      {(groupIndex === 0 || groupedRows[groupIndex - 1].year !== group.year) && <h2 className="paper-year-heading">{group.year}</h2>}
      {(groupIndex === 0 || groupedRows[groupIndex - 1].month !== group.month || groupedRows[groupIndex - 1].year !== group.year) && <h3 className="paper-month-heading">{group.month}</h3>}
      <div className="paper-date-heading"><strong>{group.label}</strong><span>{group.rows.length} latest cohort{group.rows.length === 1 ? '' : 's'}</span></div>
      {group.rows.map(row => {
        const versions = allVersions.filter(item => cohortKey(item) === cohortKey(row)).sort((a, b) => b.version - a.version)
        return <article className={`paper-ledger-row${expandedId === row.ticket_id ? ' expanded' : ''}`} key={row.ticket_id}>
          <button className="paper-ticket-summary" onClick={() => toggleTicket(row)} aria-expanded={expandedId === row.ticket_id} aria-label={`${row.name} ticket for ${group.label}`}>
            <div className="paper-ticket-identity"><span className={`portfolio-tier ${row.ticket_type}`}>{row.name}</span><small>latest v{row.version} · model {row.model_version}{row.internal_only ? ' · internal' : ''}</small></div>
            <div><span>Odds</span><strong>{row.combined_odds == null ? 'Pro only' : `${row.combined_odds.toFixed(2)}×`}</strong></div><div><span>Legs</span><strong>{row.leg_count}</strong></div><div><span>Adjusted P</span><strong>{pct(row.adjusted_probability)}</strong></div><div><span>Risk</span><strong>{row.risk_score == null ? 'Pro only' : `${row.risk_score.toFixed(0)}/100`}</strong></div>
            <div className="paper-outcome"><span className={`settlement-pill ${row.result ?? 'pending'}`}>{row.result ?? 'pending'}</span>{row.profit_loss != null && <strong className={row.profit_loss >= 0 ? 'positive' : 'negative'}>{fmtPnl(row.profit_loss)}</strong>}</div><span className="paper-chevron" aria-hidden="true">{expandedId === row.ticket_id ? '⌃' : '⌄'}</span>
          </button>
          {expandedId === row.ticket_id && <div className="paper-ticket-detail"><div className="paper-version-toolbar"><div><strong>Immutable versions</strong><span>Select a version to inspect and compare with its predecessor.</span></div><div>{versions.map(version => <button key={version.ticket_id} className={selectedVersion?.ticket_id === version.ticket_id ? 'active' : ''} onClick={() => loadVersion(version)}>v{version.version}</button>)}</div></div>
            {ticketLoading && <div className="odds-state"><span className="spinner" />Loading published selections…</div>}
            {ticket && <><ul>{ticket.legs.map(leg => <li key={leg.selection_id}><div><strong>{leg.home_team} <span>vs</span> {leg.away_team}</strong><small>{leg.competition} · {formatKickoff(leg.kickoff_at)}</small><b className="paper-market-badge">{formatMarket(leg.market)}</b></div><div><strong>{leg.best_odds == null ? 'Pro only' : leg.best_odds.toFixed(2)}</strong><small>Q {leg.q_score == null ? 'Pro only' : leg.q_score.toFixed(1)} · {leg.q_grade && <GradeBadge grade={leg.q_grade} />} · <b className={`paper-leg-result ${leg.result}`}>{leg.result}</b></small></div></li>)}</ul><div className="paper-audit"><code title={ticket.publication_hash}>Hash {ticket.publication_hash}</code><span>Published {new Date(ticket.published_at).toLocaleString()}</span></div>{compareTicket ? <div className="paper-version-compare"><strong>Compared with v{compareTicket.version}:</strong> {ticket.legs.filter(a => !compareTicket.legs.some(b => b.match_id === a.match_id && b.market === a.market && b.selection === a.selection)).length} added · {compareTicket.legs.filter(a => !ticket.legs.some(b => b.match_id === a.match_id && b.market === a.market && b.selection === a.selection)).length} removed</div> : <div className="paper-version-compare"><strong>Original publication:</strong> no predecessor exists for this cohort.</div>}</>}
          </div>}
        </article>
      })}
    </section>)}</div>}
  </>
}

function LedgerMetric({ label, value, note, tone }: { label: string; value: string; note: string; tone?: 'positive' | 'negative' | 'warning' }) {
  return <div className={`stat-card metric-card${tone ? ` ${tone}` : ''}`}><div className="kpi-label">{label}</div><div className="kpi-value">{value}</div><div className="kpi-note">{note}</div></div>
}
function EvidenceCheck({ label, value, note }: { label: string; value: string; note: string }) { return <div><span>{label}</span><strong>{value}</strong><small>{note}</small></div> }
