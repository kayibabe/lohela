import { useEffect, useState } from 'react'
import { fmt, fmtPnl } from '../utils/currency'
import { formatMarket, formatSelection, formatTicketType } from '../lib/api'
import GradeBadge from '../components/GradeBadge'

interface Summary { total_bets: number; settled: number; pending: number; won: number; lost: number; total_staked: number; total_returned: number; pnl: number; roi_pct: number; hit_rate_pct: number; avg_odds: number | null }
interface ByType { label: string; bets: number; won: number; staked: number; returned: number; pnl: number; roi_pct: number; hit_rate_pct: number }
interface ByMonth extends Omit<ByType, 'label'> { month: string }
interface ResearchBreakdown { ticket_type: string; tickets: number; wins: number; hit_rate: number; roi: number; profit_loss: number }
interface ResearchSummary {
  current_model_version: string
  published_tickets: number; settled_tickets: number; wins: number; losses: number
  hit_rate: number; hit_rate_confidence_interval_95: [number, number]
  stake: number; return: number; profit_loss: number; roi: number; yield: number
  max_drawdown_units: number; brier_score: number | null; calibration_error: number | null
  selection_sample_size: number; by_ticket_type: ResearchBreakdown[]
}
interface IndividualRow { label: string; selections: number; settled: number; wins: number; losses: number; voids: number; staked: number; returned: number; pnl: number; roi: number; hit_rate: number }
interface IndividualSummary { stake: number; unique_selections: number; settled_selections: number; eligible_predictions?: number; settled_predictions?: number; overall: IndividualRow; by_date: IndividualRow[]; by_month: IndividualRow[]; by_year: IndividualRow[]; by_market: IndividualRow[]; by_competition: IndividualRow[] }
interface ReliabilityRow { market: string; q_band: string; spread_band: string; selections: number; settled: number; wins: number; losses: number; hit_rate: number | null; roi: number | null; confidence: string }
interface ClvRow { legs: number; avg_clv_pct: number | null; beat_close_rate: number | null; avg_edge: number | null }
interface ClvBreakdown extends ClvRow { q_grade?: string; market?: string; month?: string }
interface ClvSummaryData { overall: ClvRow; by_q_grade: ClvBreakdown[]; by_market: ClvBreakdown[]; by_month: ClvBreakdown[] }
interface PickMetric { unique_picks: number; settled: number; pending: number; wins: number; losses: number; voids: number; settlement_coverage: number; hit_rate: number | null; priced_settled: number; missing_odds: number; staked: number; returned: number; profit_loss: number; roi: number | null }
interface RecommendationPick {
  pick_id: string; target_date: string; match_id: number; home_team: string; away_team: string; competition: string; kickoff_at: string
  market: string; selection: string; sources: Array<'published' | 'strongest'>; published_tiers: string[]; published_ticket_count: number
  strongest_rank: number | null; strongest_capture_sources: string[]; prediction_ids: number[]; model_run_ids: number[]; model_version: string; model_versions: string[]
  q_score: number; q_grade: string; model_probability: number; edge: number | null; model_spread: number | null; odds: number | null; odds_source: string | null
  source_odds_at: string | null; first_picked_at: string; selection_settled_at: string | null; match_status: string; home_goals: number | null; away_goals: number | null
  result: 'pending' | 'won' | 'lost' | 'void'; profit_loss: number | null; reconstructed: boolean; revised_pick: boolean; revision_count: number
}
interface PickLedgerData {
  stake: number; total_rows: number; returned_rows: number
  metrics: Record<'all' | 'published' | 'strongest' | 'overlap', PickMetric>
  filters: { markets: string[]; competitions: string[]; model_versions: string[]; date_min: string | null; date_max: string | null }
  rows: RecommendationPick[]
}
interface BacktestMetrics {
  sample_size: number; candidate_rows: number; leakage_rows_rejected: number; missing_closing_odds_rows: number
  ending_bankroll: number; profit_loss: number; roi: number; yield: number; hit_rate: number
  hit_rate_confidence_interval_95: [number, number]; brier_score: number | null; calibration_error: number | null; max_drawdown: number
  monte_carlo: { simulations: number; roi_p05: number | null; roi_median: number | null; roi_p95: number | null; probability_positive_roi?: number }
  by_market: Array<{ market: string; sample_size: number; hit_rate: number; roi: number }>
}
interface BacktestResult { backtest_run_id: number; status: string; leakage_checks_passed: boolean; error_details: string | null; metrics: BacktestMetrics }
interface PipelineRun { id: number; target_date: string; status: string; current_stage: string | null; completed_at: string | null; started_at: string | null }
type View = 'research' | 'individual' | 'auto' | 'backtest' | 'journal'

const pct = (value: number | null | undefined, digits = 1) => value == null ? '—' : `${(value * 100).toFixed(digits)}%`
const score = (value: number | null | undefined) => value == null ? '—' : value.toFixed(3)
const isoDate = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
function initialBacktestDates(modelVersion: string) { const end = new Date(); const start = new Date(end); start.setFullYear(start.getFullYear() - 1); return { period_start: isoDate(start), period_end: isoDate(end), model_version: modelVersion, simulations: '10000' } }

export default function AnalyticsPage() {
  const [view, setView] = useState<View>('research')
  const [research, setResearch] = useState<ResearchSummary | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [byType, setByType] = useState<ByType[]>([])
  const [byMonth, setByMonth] = useState<ByMonth[]>([])
  const [autoResearch, setAutoResearch] = useState<IndividualSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [latestRun, setLatestRun] = useState<PipelineRun | null>(null)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [modelVersion, setModelVersion] = useState('')

  useEffect(() => {
    Promise.all([
      fetch(`/api/v1/performance/summary?${new URLSearchParams({ ...(dateFrom ? { date_from: dateFrom } : {}), ...(dateTo ? { date_to: dateTo } : {}), ...(modelVersion ? { model_version: modelVersion } : {}) })}`).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch('/api/v1/analytics/summary').then(r => r.ok ? r.json() : null).catch(() => null),
      fetch('/api/v1/analytics/by-ticket-type').then(r => r.ok ? r.json() : []).catch(() => []),
      fetch('/api/v1/analytics/by-month').then(r => r.ok ? r.json() : []).catch(() => []),
      fetch('/api/v1/admin/pipeline/status').then(r => r.ok ? r.json() : null).catch(() => null),
    ]).then(([paper, manual, types, months, pipeline]) => {
      setResearch(paper); setSummary(manual); setByType(types); setByMonth(months); setLatestRun(pipeline?.runs?.[0] ?? null); setError(!paper && !manual)
    }).finally(() => setLoading(false))
  }, [dateFrom, dateTo, modelVersion])

  return (
    <div className="page-content analytics-page">
      <div className="analytics-hero">
        <div><span className="eyebrow">Evidence, not promises</span><h1>Model validation</h1><p>Paper-trading performance, calibration, drawdown, and strict closing-odds backtests.</p></div>
        <span className="ledger-chip"><span /> Immutable research ledger</span>
      </div>
      <div className="analytics-tabs" role="tablist" aria-label="Analytics views">
        {([['research', 'Published performance'], ['individual', 'Pick ledger'], ['auto', 'All-market research'], ['backtest', 'Backtest lab'], ['journal', 'Personal journal']] as [View, string][]).map(([id, label]) => <button key={id} role="tab" aria-selected={view === id} className={view === id ? 'active' : ''} onClick={() => setView(id)}>{label}</button>)}
      </div>
      <DataFreshness run={latestRun} />
      {view === 'research' && <div className="analytics-filters shared-analytics-filters" aria-label="Analytics scope filters"><span className="filter-scope-label">Scope</span><label>From<input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} /></label><label>To<input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} /></label><label>Model<input placeholder="All models" value={modelVersion} onChange={e => setModelVersion(e.target.value)} /></label>{(dateFrom || dateTo || modelVersion) && <button className="btn-ghost btn-sm" onClick={() => { setDateFrom(''); setDateTo(''); setModelVersion('') }}>Reset scope</button>}<span className="filter-summary">Applies to immutable published-ticket performance.</span></div>}
      {loading && <AnalyticsSkeleton />}
      {!loading && error && <AnalyticsEmpty title="Analytics are unavailable" body="Check the API connection and try again." />}
      {!loading && view === 'research' && <ResearchPerformance data={research} />}
      {!loading && view === 'individual' && <PickLedger />}
      {!loading && view === 'auto' && <AutoMarketResearch data={autoResearch} setData={setAutoResearch} />}
      {!loading && view === 'backtest' && <BacktestPanel currentModelVersion={research?.current_model_version ?? ''} />}
      {!loading && view === 'journal' && <ManualJournal summary={summary} byType={byType} byMonth={byMonth} />}
    </div>
  )
}

function AutoMarketResearch({ data, setData }: { data: IndividualSummary | null; setData: (value: IndividualSummary | null) => void }) {
  useEffect(() => { fetch('/api/v1/performance/all-markets?stake=1').then(response => response.ok ? response.json() : null).then(setData).catch(() => setData(null)) }, [setData])
  if (!data) return <AnalyticsEmpty title="No automatic research data" body="Eligible predictions with frozen odds will appear after model and result ingestion." />
  return <><div className="journal-note"><strong>Automatic all-market research</strong><span>Every latest prediction with a valid pre-kickoff odds snapshot is evaluated. This does not create bets or alter the paper ledger.</span></div><div className="research-kpis"><MetricCard label="Eligible predictions" value={data.eligible_predictions?.toString() ?? '—'} /><MetricCard label="Settled" value={data.settled_predictions?.toString() ?? '—'} /><MetricCard label="Net P&amp;L" value={fmtPnl(data.overall.pnl)} tone={data.overall.pnl >= 0 ? 'positive' : 'negative'} /><MetricCard label="ROI" value={pct(data.overall.roi)} tone={data.overall.roi >= 0 ? 'positive' : 'negative'} /></div><SortableIndividualTable title="By day" rows={data.by_date} /><SortableIndividualTable title="By month" rows={data.by_month} /><SortableIndividualTable title="By year" rows={data.by_year} /><SortableIndividualTable title="By market" rows={data.by_market} market /><SortableIndividualTable title="By league" rows={data.by_competition} /><ClvPanel /></>
}

function ClvPanel() {
  const [data, setData] = useState<ClvSummaryData | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => { fetch('/api/v1/performance/clv').then(r => r.ok ? r.json() : null).then(setData).catch(() => setData(null)).finally(() => setLoading(false)) }, [])
  if (loading) return null
  if (!data || data.overall.legs === 0) return <section className="analytics-card"><div className="section-header"><div><span className="eyebrow">Beating the market, not just the scoreboard</span><h2 className="section-title">Closing-line value</h2></div></div><AnalyticsEmpty title="No CLV data yet" body="Closing prices are captured automatically once a match's kickoff passes. Check back after today's fixtures kick off." compact /></section>
  const beatTone = (data.overall.beat_close_rate ?? 0) >= 0.5 ? 'positive' : 'negative'
  return <section className="analytics-card">
    <div className="section-header"><div><span className="eyebrow">Beating the market, not just the scoreboard</span><h2 className="section-title">Closing-line value</h2></div><span className="section-subtitle">Entry price vs. the market's price at kickoff — the standard signal of real model edge</span></div>
    <div className="research-kpis">
      <MetricCard label="Legs with known CLV" value={data.overall.legs.toString()} />
      <MetricCard label="Avg CLV" value={data.overall.avg_clv_pct == null ? '—' : pct(data.overall.avg_clv_pct)} note="Entry odds vs. closing odds" tone={data.overall.avg_clv_pct != null && data.overall.avg_clv_pct >= 0 ? 'positive' : 'negative'} />
      <MetricCard label="Beat the close" value={pct(data.overall.beat_close_rate)} note="Share of legs priced better than closing" tone={beatTone} />
      <MetricCard label="Avg edge (vig-inclusive)" value={data.overall.avg_edge == null ? '—' : pct(data.overall.avg_edge)} />
    </div>
    <ClvTable title="By Q-grade" rows={data.by_q_grade} labelKey="q_grade" />
    <ClvTable title="By market" rows={data.by_market} labelKey="market" />
    <ClvTable title="By month" rows={data.by_month} labelKey="month" />
    <p className="matrix-note">Positive CLV means our entry price was consistently better than where the market closed — the clearest sign the model is finding real value, independent of short-run win/loss variance.</p>
  </section>
}

function ClvTable({ title, rows, labelKey }: { title: string; rows: ClvBreakdown[]; labelKey: 'q_grade' | 'market' | 'month' }) {
  if (rows.length === 0) return null
  return <div className="analytics-table-wrap"><table className="analytics-table"><thead><tr><th>{title}</th><th>Legs</th><th>Avg CLV</th><th>Beat close</th></tr></thead><tbody>{rows.map(row => <tr key={String(row[labelKey])}><td><strong>{labelKey === 'market' ? formatMarket(String(row[labelKey])) : row[labelKey]}</strong></td><td>{row.legs}</td><td className={row.avg_clv_pct != null && row.avg_clv_pct >= 0 ? 'positive' : 'negative'}>{row.avg_clv_pct == null ? '—' : pct(row.avg_clv_pct)}</td><td>{pct(row.beat_close_rate)}</td></tr>)}</tbody></table></div>
}

function DataFreshness({ run }: { run: PipelineRun | null }) {
  if (!run) return <div className="data-freshness unknown"><span className="status-dot" /><span>Pipeline freshness unavailable</span><small>Results may not reflect the latest completed run.</small></div>
  const complete = ['completed', 'success', 'succeeded'].includes(run.status.toLowerCase())
  const when = run.completed_at ?? run.started_at
  return <div className={`data-freshness ${complete ? 'ready' : 'pending'}`}><span className="status-dot" /><strong>{complete ? 'Data current through completed pipeline' : 'Pipeline not yet complete'}</strong><span>Target date {run.target_date}</span>{when && <small>{complete ? 'Completed' : 'Started'} {new Date(when).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</small>}{run.current_stage && !complete && <small>Stage: {run.current_stage}</small>}</div>
}

function PickLedger() {
  const [data, setData] = useState<PickLedgerData | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [source, setSource] = useState<'all' | 'published' | 'strongest' | 'overlap'>('all')
  const [result, setResult] = useState('all')
  const [market, setMarket] = useState('all')
  const [competition, setCompetition] = useState('all')
  const [modelVersion, setModelVersion] = useState('all')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [stake, setStake] = useState('1')
  const [search, setSearch] = useState('')
  const [reliability, setReliability] = useState<ReliabilityRow[]>([])

  useEffect(() => {
    let active = true
    const refresh = () => {
      const params = new URLSearchParams({ stake: stake || '1', source, limit: '2000' })
      if (from) params.set('date_from', from)
      if (to) params.set('date_to', to)
      if (result !== 'all') params.set('result', result)
      if (market !== 'all') params.set('market', market)
      if (competition !== 'all') params.set('competition', competition)
      if (modelVersion !== 'all') params.set('model_version', modelVersion)
      fetch(`/api/v1/performance/recommendation-picks?${params}`)
        .then(response => response.ok ? response.json() : Promise.reject(new Error(`API ${response.status}`)))
        .then(value => { if (active) { setData(value); setFailed(false) } })
        .catch(() => { if (active) setFailed(true) })
        .finally(() => { if (active) setLoading(false) })
    }
    setLoading(true)
    refresh()
    const timer = window.setInterval(refresh, 300_000)
    return () => { active = false; window.clearInterval(timer) }
  }, [stake, source, result, market, competition, modelVersion, from, to])

  useEffect(() => {
    let active = true
    const params = new URLSearchParams()
    if (from) params.set('date_from', from)
    if (to) params.set('date_to', to)
    params.set('model_version', modelVersion)
    fetch(`/api/v1/performance/market-reliability?${params}`)
      .then(response => response.ok ? response.json() : [])
      .then(rows => { if (active) setReliability(rows) })
      .catch(() => { if (active) setReliability([]) })
    return () => { active = false }
  }, [from, to, modelVersion])

  if (loading && !data) return <AnalyticsSkeleton />
  if (failed && !data) return <AnalyticsEmpty title="Pick ledger unavailable" body="The recommendation ledger endpoint did not return data." />
  if (!data) return <AnalyticsEmpty title="No recommendation picks" body="Published and Strongest picks will appear after a completed model run." />

  const focused = data.metrics[source]
  const query = search.trim().toLowerCase()
  const visibleRows = query ? data.rows.filter(row => [row.home_team, row.away_team, row.competition, row.market, row.selection, ...row.sources].join(' ').toLowerCase().includes(query)) : data.rows
  const reconstructed = data.rows.filter(row => row.reconstructed).length
  const hasFilters = source !== 'all' || result !== 'all' || market !== 'all' || competition !== 'all' || modelVersion !== 'all' || from || to || search
  const reset = () => { setSource('all'); setResult('all'); setMarket('all'); setCompetition('all'); setModelVersion('all'); setFrom(''); setTo(''); setSearch('') }

  return <>
    <div className="journal-note pick-ledger-note"><strong>Deduplicated system picks</strong><span>Published recommendations and Strongest selections are merged once per date, match, market, and selected outcome. Flat-stake simulations never alter the immutable ticket ledger.</span><label>Stake per pick <input type="number" min="0.01" step="0.01" value={stake} onChange={event => setStake(event.target.value)} /></label></div>
    <div className="pick-source-grid" aria-label="Recommendation source comparison">
      {([['all', 'All unique'], ['published', 'Published'], ['strongest', 'Strongest'], ['overlap', 'Published + Strongest']] as Array<[typeof source, string]>).map(([id, label]) => <button type="button" key={id} className={`pick-source-card${source === id ? ' active' : ''}`} aria-pressed={source === id} onClick={() => setSource(id)}><span>{label}</span><strong>{data.metrics[id].unique_picks}</strong><small>{data.metrics[id].settled} settled · {pct(data.metrics[id].hit_rate)} hit · {pct(data.metrics[id].roi)} ROI</small></button>)}
    </div>
    <div className="analytics-filters pick-ledger-filters" aria-label="Pick ledger filters">
      <label>Source<select value={source} onChange={event => setSource(event.target.value as typeof source)}><option value="all">All sources</option><option value="published">Published</option><option value="strongest">Strongest</option><option value="overlap">Published + Strongest</option></select></label>
      <label>Result<select value={result} onChange={event => setResult(event.target.value)}><option value="all">All results</option><option value="pending">Pending</option><option value="won">Won</option><option value="lost">Lost</option><option value="void">Void</option></select></label>
      <label>From<input type="date" value={from} onChange={event => setFrom(event.target.value)} /></label>
      <label>To<input type="date" value={to} onChange={event => setTo(event.target.value)} /></label>
      <label>Market<select value={market} onChange={event => setMarket(event.target.value)}><option value="all">All markets</option>{data.filters.markets.map(value => <option key={value} value={value}>{formatMarket(value)}</option>)}</select></label>
      <label>League<select value={competition} onChange={event => setCompetition(event.target.value)}><option value="all">All leagues</option>{data.filters.competitions.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
      <label>Model<select value={modelVersion} onChange={event => setModelVersion(event.target.value)}><option value="all">All models</option>{data.filters.model_versions.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
      <label>Find match<input type="search" placeholder="Team or league" value={search} onChange={event => setSearch(event.target.value)} /></label>
      {hasFilters && <button className="btn-ghost btn-sm" onClick={reset}>Reset filters</button>}
    </div>
    <div className="filter-summary">Showing {visibleRows.length} of {data.total_rows} deduplicated picks · {source === 'all' ? 'all sources' : source === 'overlap' ? 'Published + Strongest overlap' : source} · {fmt(data.stake)} flat stake</div>
    <div className="research-kpis pick-ledger-kpis">
      <MetricCard label="Unique picks" value={focused.unique_picks.toString()} note={`${focused.settled} settled · ${focused.pending} pending`} />
      <MetricCard label="Settlement coverage" value={pct(focused.settlement_coverage)} note="Final results attached" />
      <MetricCard label="Won / lost / void" value={`${focused.wins} / ${focused.losses} / ${focused.voids}`} note={`${focused.priced_settled} priced outcomes`} />
      <MetricCard label="Hit rate" value={pct(focused.hit_rate)} note="Voids excluded" />
      <MetricCard label="Net P&L" value={fmtPnl(focused.profit_loss)} note={`${fmt(focused.staked)} simulated stake`} tone={focused.profit_loss >= 0 ? 'positive' : 'negative'} />
      <MetricCard label="ROI" value={pct(focused.roi)} note={focused.missing_odds ? `${focused.missing_odds} outcomes excluded: no frozen odds` : 'Frozen pick-time odds'} tone={(focused.roi ?? 0) >= 0 ? 'positive' : 'negative'} />
    </div>
    {focused.priced_settled < 30 && <div className="sample-warning">Small sample: only {focused.priced_settled} priced settled picks in this scope. Treat hit rate and ROI as directional evidence.</div>}
    {reconstructed > 0 && <div className="pick-backfill-note"><strong>{reconstructed} historical Strongest picks reconstructed</strong><span>Future lists are snapshotted immutably at model-run completion; reconstructed rows remain labelled in the table.</span></div>}
    <RecommendationPickTable rows={visibleRows} stake={data.stake} />
    <ReliabilityMatrix rows={reliability} />
  </>
}

type PickSortKey = 'kickoff_at' | 'match' | 'market' | 'source' | 'q_score' | 'odds' | 'result' | 'profit_loss'

interface PickDateGroup { key: string; label: string; rows: RecommendationPick[]; metrics: PickMetric }
interface PickMonthGroup { key: string; label: string; rows: RecommendationPick[]; metrics: PickMetric; dates: PickDateGroup[] }
interface PickYearGroup { key: string; label: string; rows: RecommendationPick[]; metrics: PickMetric; months: PickMonthGroup[] }

function recommendationMetricsForRows(rows: RecommendationPick[], stake: number): PickMetric {
  const settled = rows.filter(row => row.result !== 'pending')
  const effective = settled.filter(row => row.result === 'won' || row.result === 'lost')
  const priced = effective.filter(row => row.odds != null && row.odds > 1)
  const wins = effective.filter(row => row.result === 'won').length
  const losses = effective.filter(row => row.result === 'lost').length
  const voids = settled.filter(row => row.result === 'void').length
  const staked = priced.length * stake
  const returned = priced.reduce((sum, row) => sum + (row.result === 'won' ? stake * (row.odds ?? 0) : 0), 0)
  const profitLoss = returned - staked
  return {
    unique_picks: rows.length,
    settled: settled.length,
    pending: rows.length - settled.length,
    wins,
    losses,
    voids,
    settlement_coverage: rows.length ? settled.length / rows.length : 0,
    hit_rate: effective.length ? wins / effective.length : null,
    priced_settled: priced.length,
    missing_odds: effective.length - priced.length,
    staked: Number(staked.toFixed(2)),
    returned: Number(returned.toFixed(2)),
    profit_loss: Number(profitLoss.toFixed(2)),
    roi: staked ? profitLoss / staked : null,
  }
}

function groupRecommendationPicks(rows: RecommendationPick[], stake: number): PickYearGroup[] {
  const years = new Map<string, RecommendationPick[]>()
  for (const row of rows) {
    const year = row.target_date.slice(0, 4)
    years.set(year, [...(years.get(year) ?? []), row])
  }
  return [...years.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([year, yearRows]) => {
    const months = new Map<string, RecommendationPick[]>()
    for (const row of yearRows) {
      const month = row.target_date.slice(0, 7)
      months.set(month, [...(months.get(month) ?? []), row])
    }
    const monthGroups = [...months.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([month, monthRows]) => {
      const dates = new Map<string, RecommendationPick[]>()
      for (const row of monthRows) dates.set(row.target_date, [...(dates.get(row.target_date) ?? []), row])
      const dateGroups = [...dates.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([date, dateRows]) => ({
        key: date,
        label: new Date(`${date}T12:00:00`).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }),
        rows: dateRows,
        metrics: recommendationMetricsForRows(dateRows, stake),
      }))
      return {
        key: month,
        label: new Date(`${month}-01T12:00:00`).toLocaleDateString(undefined, { month: 'long', year: 'numeric' }),
        rows: monthRows,
        metrics: recommendationMetricsForRows(monthRows, stake),
        dates: dateGroups,
      }
    })
    return {
      key: year,
      label: year,
      rows: yearRows,
      metrics: recommendationMetricsForRows(yearRows, stake),
      months: monthGroups,
    }
  })
}

function PickPeriodSummary({ level, label, metrics }: { level: 'year' | 'month' | 'date'; label: string; metrics: PickMetric }) {
  const roiTone = metrics.roi == null ? '' : metrics.roi >= 0 ? 'positive' : 'negative'
  return <div className={`pick-period-summary ${level}`}>
    <div className="pick-period-title"><span>{level}</span><strong>{label}</strong></div>
    <div className="pick-period-stats">
      <span><small>Picks</small><strong>{metrics.unique_picks}</strong></span>
      <span><small>Settled / coverage</small><strong>{metrics.settled} · {pct(metrics.settlement_coverage)}</strong></span>
      <span><small>Won / lost / void</small><strong>{metrics.wins} / {metrics.losses} / {metrics.voids}</strong></span>
      <span><small>Hit rate</small><strong>{pct(metrics.hit_rate)}</strong></span>
      <span><small>P&amp;L</small><strong className={metrics.profit_loss >= 0 ? 'positive' : 'negative'}>{fmtPnl(metrics.profit_loss)}</strong></span>
      <span><small>ROI</small><strong className={roiTone}>{pct(metrics.roi)}</strong></span>
    </div>
  </div>
}

function RecommendationPickTable({ rows, stake }: { rows: RecommendationPick[]; stake: number }) {
  const [sort, setSort] = useState<{ key: PickSortKey; direction: 'asc' | 'desc' }>({ key: 'kickoff_at', direction: 'desc' })
  const sortValue = (row: RecommendationPick, key: PickSortKey): string | number => {
    if (key === 'match') return `${row.home_team} ${row.away_team}`
    if (key === 'market') return `${row.market} ${row.selection}`
    if (key === 'source') return row.sources.join(' ')
    return row[key] ?? -Infinity
  }
  const sorted = [...rows].sort((a, b) => { const av = sortValue(a, sort.key), bv = sortValue(b, sort.key); const compared = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv)); return sort.direction === 'asc' ? compared : -compared })
  const grouped = groupRecommendationPicks(sorted, stake)
  const change = (key: PickSortKey) => setSort(current => ({ key, direction: current.key === key && current.direction === 'desc' ? 'asc' : 'desc' }))
  const heading = (key: PickSortKey, label: string) => <button type="button" className="table-sort" onClick={() => change(key)}>{label} <span aria-hidden="true">{sort.key === key ? (sort.direction === 'asc' ? '▲' : '▼') : '↕'}</span></button>

  const table = (dateRows: RecommendationPick[]) => <div className="analytics-table-wrap"><table className="analytics-table pick-ledger-table"><thead><tr><th>{heading('kickoff_at', 'Kickoff / model')}</th><th>{heading('match', 'Match / league')}</th><th>{heading('market', 'Market / selection')}</th><th>{heading('source', 'Source')}</th><th>{heading('q_score', 'Decision evidence')}</th><th>{heading('result', 'Result')}</th><th>{heading('profit_loss', 'P&L')}</th></tr></thead><tbody>{dateRows.map(row => <tr key={row.pick_id}><td><strong className="kickoff-time">{new Date(row.kickoff_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</strong><small>Model {row.model_versions.join(', ')}</small></td><td><strong>{row.home_team} <span className="match-vs">vs</span> {row.away_team}</strong><small>{row.competition}</small></td><td><span className="market-chip">{formatMarket(row.market)}</span><strong>{formatSelection(row.selection)}</strong>{row.revised_pick && <span className="pick-flag revised">Selection revised · {row.revision_count}</span>}</td><td><div className="pick-source-badges">{row.sources.map(item => <span key={item} className={`pick-source-badge ${item}`}>{item === 'published' ? 'Published' : `Strongest${row.strongest_rank ? ` #${row.strongest_rank}` : ''}`}</span>)}</div>{row.published_tiers.length > 0 && <small>{row.published_tiers.map(formatTicketType).join(' · ')}</small>}{row.reconstructed && <span className="pick-flag reconstructed">Reconstructed</span>}</td><td><div className="pick-evidence"><GradeBadge grade={row.q_grade} /><strong>Q {row.q_score.toFixed(1)}</strong></div><small>{pct(row.model_probability)} probability · {row.edge == null ? 'No edge' : `${(row.edge * 100).toFixed(1)} pp edge`}</small><small>{row.model_spread == null ? 'Spread unavailable' : `${(row.model_spread * 100).toFixed(1)} pp spread`} · {row.odds ? `@ ${row.odds.toFixed(2)}` : 'Odds unavailable'}</small></td><td>{row.home_goals != null && row.away_goals != null ? <strong>{row.home_goals}–{row.away_goals}</strong> : <strong>—</strong>}<span className={`selection-outcome ${row.result}`}><strong>{row.result}</strong></span></td><td className={row.profit_loss == null ? '' : row.profit_loss >= 0 ? 'positive' : 'negative'}><strong>{row.profit_loss == null ? '—' : fmtPnl(row.profit_loss)}</strong><small>{row.result === 'pending' ? 'Awaiting result' : 'Flat-stake result'}</small></td></tr>)}</tbody></table></div>

  return <section className="analytics-card pick-ledger-table-card">
    <div className="section-header"><div><span className="eyebrow">Pick-level evidence</span><h2 className="section-title">Recommendation ledger</h2></div><span className="section-subtitle">Year → month → exact date · one deduplicated row per selection</span></div>
    {sorted.length === 0 ? <AnalyticsEmpty title="No picks match these filters" body="Reset the filters or choose another source." compact /> : <div className="pick-period-tree">
      {grouped.map(year => <details className="pick-period-group pick-year-group" key={year.key} open>
        <summary><PickPeriodSummary level="year" label={year.label} metrics={year.metrics} /></summary>
        <div className="pick-year-body">{year.months.map(month => <details className="pick-period-group pick-month-group" key={month.key} open>
          <summary><PickPeriodSummary level="month" label={month.label} metrics={month.metrics} /></summary>
          <div className="pick-month-body">{month.dates.map(date => <details className="pick-period-group pick-date-group" key={date.key} open>
            <summary><PickPeriodSummary level="date" label={date.label} metrics={date.metrics} /></summary>
            {table(date.rows)}
          </details>)}</div>
        </details>)}</div>
      </details>)}
    </div>}
  </section>
}

function ReliabilityMatrix({ rows }: { rows: ReliabilityRow[] }) {
  const sorted = [...rows].sort((a, b) => (b.roi ?? -999) - (a.roi ?? -999))
  return <section className="analytics-card reliability-card"><div className="section-header"><div><span className="eyebrow">Historical evidence</span><h2 className="section-title">Market Reliability Matrix</h2></div><span className="section-subtitle">Published picks · Market × Q-score × model spread</span></div>{sorted.length === 0 ? <AnalyticsEmpty title="No settled matrix data" body="The matrix appears after published selections have been settled." compact /> : <div className="analytics-table-wrap"><table className="analytics-table reliability-table"><thead><tr><th>Market</th><th>Q band</th><th>Spread</th><th>Settled</th><th>Won / lost</th><th>Hit rate</th><th>ROI</th><th>Evidence</th></tr></thead><tbody>{sorted.map((row, index) => <tr key={`${row.market}-${row.q_band}-${row.spread_band}-${index}`}><td><strong>{formatMarket(row.market)}</strong></td><td>{row.q_band}</td><td>{row.spread_band}</td><td>{row.settled}</td><td>{row.wins} / {row.losses}</td><td>{row.hit_rate == null ? '—' : pct(row.hit_rate)}</td><td className={row.roi != null && row.roi >= 0 ? 'positive' : 'negative'}>{row.roi == null ? '—' : pct(row.roi)}</td><td><span className={`evidence-pill ${row.confidence.toLowerCase()}`}>{row.confidence}</span></td></tr>)}</tbody></table></div>}<p className="matrix-note">Published-pick evidence only. Use it as historical context, not a guarantee. Prefer positive ROI cells with adequate sample size and low model spread; treat small samples as directional.</p></section>
}

function SortableIndividualTable({ title, rows, market = false }: { title: string; rows: IndividualRow[]; market?: boolean }) {
  const [sort, setSort] = useState<{ key: keyof IndividualRow; direction: 'asc' | 'desc' }>({ key: 'label', direction: 'desc' })
  const columns: Array<[keyof IndividualRow, string]> = [['label', market ? 'Market' : 'Period'], ['settled', 'Selections'], ['wins', 'Won / lost'], ['staked', 'Staked'], ['pnl', 'P&L'], ['roi', 'ROI']]
  const sorted = [...rows].sort((a, b) => { const av = a[sort.key], bv = b[sort.key]; const result = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv)); return sort.direction === 'asc' ? result : -result })
  function change(key: keyof IndividualRow) { setSort(current => ({ key, direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc' })) }
  return <section className="analytics-card journal-table-card"><div className="section-header"><h2 className="section-title">{title}</h2></div><div className="analytics-table-wrap"><table className="analytics-table"><thead><tr>{columns.map(([key, label]) => <th key={key}><button type="button" className="table-sort" onClick={() => change(key)}>{label} <span aria-hidden="true">{sort.key === key ? (sort.direction === 'asc' ? '▲' : '▼') : '↕'}</span></button></th>)}</tr></thead><tbody>{sorted.map(r => <tr key={r.label}><td><strong>{market ? formatMarket(r.label) : r.label}</strong></td><td>{r.settled}</td><td>{r.wins} / {r.losses}</td><td>{fmt(r.staked)}</td><td className={r.pnl >= 0 ? 'positive' : 'negative'}>{fmtPnl(r.pnl)}</td><td className={r.roi >= 0 ? 'positive' : 'negative'}>{pct(r.roi)}</td></tr>)}</tbody></table></div></section>
}

function ResearchPerformance({ data }: { data: ResearchSummary | null }) {
  if (!data) return <AnalyticsEmpty title="No paper ledger response" body="The settlement performance endpoint did not return data." />
  const hasSettled = data.settled_tickets > 0
  const coverage = data.published_tickets ? data.settled_tickets / data.published_tickets : 0
  const [ciLow, ciHigh] = data.hit_rate_confidence_interval_95
  return <>
    <div className="validation-strip"><div><span className={`validation-dot ${hasSettled ? 'ready' : ''}`} /><strong>{hasSettled ? 'Settlement evidence available' : 'Paper-trading evidence accumulating'}</strong></div><span>Model {data.current_model_version} · {data.settled_tickets} of {data.published_tickets} tickets settled · {pct(coverage)} coverage</span></div>
    <div className="research-kpis">
      <MetricCard label="Net P&L" value={fmtPnl(data.profit_loss)} note={`${fmt(data.stake)} staked`} tone={data.profit_loss >= 0 ? 'positive' : 'negative'} />
      <MetricCard label="ROI / yield" value={pct(data.roi)} note="Settled stakes only" tone={data.roi >= 0 ? 'positive' : 'negative'} />
      <MetricCard label="Hit rate" value={pct(data.hit_rate)} note={`95% CI ${pct(ciLow)}–${pct(ciHigh)}`} />
      <MetricCard label="Max drawdown" value={fmt(data.max_drawdown_units)} note="Peak-to-trough units" tone="warning" />
      <MetricCard label="Brier score" value={score(data.brier_score)} note="Lower is better" />
      <MetricCard label="Calibration error" value={score(data.calibration_error)} note={`${data.selection_sample_size} settled selections`} />
    </div>
    <div className="analytics-insight-grid">
      <section className="analytics-card">
        <div className="section-header"><div><span className="eyebrow">Portfolio lens</span><h2 className="section-title">By ticket type</h2></div><span className="section-subtitle">Latest settlement version</span></div>
        {data.by_ticket_type.length === 0 ? <AnalyticsEmpty title="No settled ticket cohorts yet" body="Breakdowns appear after result ingestion." compact /> : <div className="cohort-list">{data.by_ticket_type.map(row => <div className="cohort-row" key={row.ticket_type}><div><span className="badge badge-type">{formatTicketType(row.ticket_type)}</span><small>{row.wins}/{row.tickets} won</small></div><div><span>Hit rate</span><strong>{pct(row.hit_rate)}</strong></div><div><span>ROI</span><strong className={row.roi >= 0 ? 'positive' : 'negative'}>{pct(row.roi)}</strong></div><div><span>P&amp;L</span><strong className={row.profit_loss >= 0 ? 'positive' : 'negative'}>{fmtPnl(row.profit_loss)}</strong></div></div>)}</div>}
      </section>
      <section className="analytics-card methodology-card">
        <div className="section-header"><div><span className="eyebrow">Interpretation</span><h2 className="section-title">Evidence quality</h2></div></div>
        <EvidenceRow label="Settlement coverage" value={pct(coverage)} detail="Published tickets with an immutable result." />
        <EvidenceRow label="Calibration sample" value={data.selection_sample_size.toString()} detail="Settled selections used for scoring." />
        <EvidenceRow label="Uncertainty" value={`${pct(ciLow)}–${pct(ciHigh)}`} detail="95% Wilson interval around hit rate." />
        <p>Small samples can make ROI and hit rate look unusually strong or weak. Read confidence and drawdown alongside headline returns.</p>
      </section>
    </div>
  </>
}

function BacktestPanel({ currentModelVersion }: { currentModelVersion: string }) {
  const [form, setForm] = useState(() => initialBacktestDates(currentModelVersion))
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  async function runBacktest(event: React.FormEvent) {
    event.preventDefault(); setRunning(true); setError(null); setResult(null)
    try {
      const response = await fetch('/api/v1/backtests/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...form, simulations: Number(form.simulations) }) })
      if (!response.ok) throw new Error(response.status === 401 ? 'Research access is required to run backtests.' : `Backtest failed (${response.status}).`)
      setResult(await response.json())
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Backtest failed.') } finally { setRunning(false) }
  }
  return <>
    <section className="backtest-console analytics-card">
      <div><span className="eyebrow">Strict validation gate</span><h2>Closing-odds backtest</h2><p>No-lookahead checks, 5% slippage, quarter-Kelly sizing capped at 2%, walk-forward folds, and Monte Carlo resampling.</p></div>
      <form onSubmit={runBacktest}>
        <label>From<input type="date" required value={form.period_start} onChange={e => setForm(f => ({ ...f, period_start: e.target.value }))} /></label>
        <label>To<input type="date" required value={form.period_end} onChange={e => setForm(f => ({ ...f, period_end: e.target.value }))} /></label>
        <label>Model<input required value={form.model_version} onChange={e => setForm(f => ({ ...f, model_version: e.target.value }))} /></label>
        <label>Simulations<select value={form.simulations} onChange={e => setForm(f => ({ ...f, simulations: e.target.value }))}><option value="10000">10,000</option><option value="25000">25,000</option><option value="50000">50,000</option></select></label>
        <button className="btn-primary" disabled={running}>{running ? 'Running validation…' : 'Run backtest'}</button>
      </form>
    </section>
    {error && <div className="error-bar backtest-error" role="alert"><div><strong>Validation could not complete</strong><span>{error}</span></div></div>}
    {result ? <BacktestReport result={result} /> : !error && <div className="backtest-principles"><span>✓ Closing odds only</span><span>✓ Leakage rejection</span><span>✓ Walk-forward</span><span>✓ Monte Carlo interval</span></div>}
  </>
}

function BacktestReport({ result }: { result: BacktestResult }) {
  const metrics = result.metrics, monte = metrics.monte_carlo
  return <div className="backtest-report">
    <div className={`validation-strip ${result.leakage_checks_passed ? '' : 'failed'}`}><div><span className={`validation-dot ${result.leakage_checks_passed ? 'ready' : ''}`} /><strong>{result.leakage_checks_passed ? 'No-lookahead gate passed' : 'Leakage gate failed'}</strong></div><span>Run #{result.backtest_run_id} · {result.status}</span></div>
    {result.error_details && <AnalyticsEmpty title="Validation note" body={result.error_details} compact />}
    <div className="research-kpis">
      <MetricCard label="Eligible selections" value={metrics.sample_size.toString()} note={`${metrics.candidate_rows} candidates`} />
      <MetricCard label="Backtest ROI" value={pct(metrics.roi)} note={`${fmt(metrics.ending_bankroll)} ending bankroll`} tone={metrics.roi >= 0 ? 'positive' : 'negative'} />
      <MetricCard label="Hit rate" value={pct(metrics.hit_rate)} note={`95% CI ${pct(metrics.hit_rate_confidence_interval_95[0])}–${pct(metrics.hit_rate_confidence_interval_95[1])}`} />
      <MetricCard label="Max drawdown" value={fmt(metrics.max_drawdown)} note="100-unit starting bankroll" tone="warning" />
      <MetricCard label="Brier score" value={score(metrics.brier_score)} note="Probability accuracy" />
      <MetricCard label="Calibration error" value={score(metrics.calibration_error)} note={`${metrics.missing_closing_odds_rows} missing price rows`} />
    </div>
    <section className="analytics-card monte-card"><div className="section-header"><div><span className="eyebrow">Distribution, not point estimate</span><h2 className="section-title">Monte Carlo ROI range</h2></div><span className="section-subtitle">{monte.simulations.toLocaleString()} simulations</span></div><div className="monte-range"><div><span>5th percentile</span><strong>{pct(monte.roi_p05)}</strong></div><div className="median"><span>Median</span><strong>{pct(monte.roi_median)}</strong></div><div><span>95th percentile</span><strong>{pct(monte.roi_p95)}</strong></div><div><span>Positive ROI chance</span><strong>{pct(monte.probability_positive_roi)}</strong></div></div></section>
    {metrics.by_market.length > 0 && <section className="analytics-card"><div className="section-header"><h2 className="section-title">Performance by market</h2><span className="section-subtitle">Closing-odds sample</span></div><div className="cohort-list">{metrics.by_market.map(row => <div className="cohort-row market" key={row.market}><div><strong>{row.market.replace(/_/g, ' ')}</strong><small>{row.sample_size} selections</small></div><div><span>Hit rate</span><strong>{pct(row.hit_rate)}</strong></div><div><span>ROI</span><strong className={row.roi >= 0 ? 'positive' : 'negative'}>{pct(row.roi)}</strong></div></div>)}</div></section>}
  </div>
}

function ManualJournal({ summary, byType, byMonth }: { summary: Summary | null; byType: ByType[]; byMonth: ByMonth[] }) {
  if (!summary) return <AnalyticsEmpty title="Manual journal unavailable" body="The optional bet journal did not return data." />
  const hasBets = summary.settled > 0
  return <>
    <div className="journal-note"><strong>Personal journal</strong><span>Manual entries are separate from the immutable system-published paper-ticket ledger.</span></div>
    <div className="stat-row"><MetricCard label="Hit rate" value={hasBets ? `${summary.hit_rate_pct.toFixed(1)}%` : '—'} /><MetricCard label="Avg odds" value={summary.avg_odds?.toFixed(2) ?? '—'} /><MetricCard label="Total staked" value={fmt(summary.total_staked)} /><MetricCard label="Net P&L" value={fmtPnl(summary.pnl)} tone={summary.pnl >= 0 ? 'positive' : 'negative'} /><MetricCard label="ROI" value={hasBets ? `${summary.roi_pct.toFixed(1)}%` : '—'} tone={summary.roi_pct >= 0 ? 'positive' : 'negative'} /><MetricCard label="Won / lost" value={`${summary.won} / ${summary.lost}`} note={`${summary.pending} pending`} /></div>
    {!hasBets && <AnalyticsEmpty title="No settled journal entries" body="Settle manually logged bets in Tracker to populate this view." />}
    {hasBets && byType.length > 0 && <JournalTable title="By ticket type" rows={byType} />}
    {hasBets && byMonth.length > 0 && <><JournalTable title="Monthly breakdown" rows={[...byMonth].reverse().map(row => ({ ...row, label: row.month }))} /><div className="section-header spaced"><h2 className="section-title">P&amp;L by month</h2></div><PnlChart data={byMonth} /></>}
  </>
}

function JournalTable({ title, rows }: { title: string; rows: ByType[] }) {
  return <section className="analytics-card journal-table-card"><div className="section-header"><h2 className="section-title">{title}</h2></div><div className="analytics-table-wrap"><table className="analytics-table"><thead><tr><th>Group</th><th>Bets</th><th>Hit rate</th><th>Staked</th><th>P&amp;L</th><th>ROI</th></tr></thead><tbody>{rows.map(row => <tr key={row.label}><td><strong>{row.label}</strong></td><td>{row.bets}</td><td>{row.hit_rate_pct.toFixed(1)}%</td><td>{fmt(row.staked)}</td><td className={row.pnl >= 0 ? 'positive' : 'negative'}>{fmtPnl(row.pnl)}</td><td className={row.roi_pct >= 0 ? 'positive' : 'negative'}>{row.roi_pct.toFixed(1)}%</td></tr>)}</tbody></table></div></section>
}

function MetricCard({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: 'positive' | 'negative' | 'warning' }) { return <div className={`stat-card metric-card${tone ? ` ${tone}` : ''}`}><div className="kpi-label">{label}</div><div className="kpi-value">{value}</div>{note && <div className="kpi-note">{note}</div>}</div> }
function EvidenceRow({ label, value, detail }: { label: string; value: string; detail: string }) { return <div className="evidence-row"><div><strong>{label}</strong><span>{detail}</span></div><b>{value}</b></div> }
function AnalyticsEmpty({ title, body, compact = false }: { title: string; body: string; compact?: boolean }) { return <div className={`analytics-empty${compact ? ' compact' : ''}`}><strong>{title}</strong><span>{body}</span></div> }
function AnalyticsSkeleton() { return <div className="research-kpis" aria-busy="true" aria-label="Loading analytics">{Array.from({ length: 6 }, (_, index) => <div className="stat-card skeleton-metric" key={index}><span className="skeleton-block" /><span className="skeleton-block" /></div>)}</div> }
function PnlChart({ data }: { data: ByMonth[] }) { const max = Math.max(...data.map(d => Math.abs(d.pnl)), 1); return <div className="pnl-chart">{data.map(d => <div key={d.month} className="pnl-bar-wrap"><div className="pnl-bar-label">{d.month.slice(5)}</div><div className="pnl-bar-track"><div className={`pnl-bar ${d.pnl >= 0 ? 'positive' : 'negative'}`} style={{ width: `${Math.abs(d.pnl) / max * 80}%` }} /></div><div className={`pnl-bar-val ${d.pnl >= 0 ? 'positive' : 'negative'}`}>{fmtPnl(d.pnl)}</div></div>)}</div> }
