import { useEffect, useMemo, useState } from 'react'
import { fetchCustomAccumulators, fetchTicket, fetchTicketHistory, formatDate, formatKickoff, formatMarket, formatTicketType, type CustomAccumulator, type Ticket, type TicketHistoryItem } from '../lib/api'
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
const sourceTicketLabel = (type: string | null | undefined) => ({ safe: 'Conservative', balanced: 'Balanced', high_odds: 'High Odds' }[type ?? ''] ?? 'Source ticket')

function accumulatorMetrics(row: CustomAccumulator) {
  const probabilities = row.legs.map(leg => leg.probability_snapshot)
  const combinedProbability = probabilities.every(value => value != null)
    ? probabilities.reduce((product, value) => product * (value as number), 1)
    : null
  const competitionCounts = new Map<string, number>()
  row.legs.forEach(leg => competitionCounts.set(leg.competition, (competitionCounts.get(leg.competition) ?? 0) + 1))
  const concentration = row.legs.length ? Math.max(...competitionCounts.values()) / row.legs.length : 0
  const riskScore = combinedProbability == null ? null : Math.max(0, Math.min(100, (1 - combinedProbability) * 70 + concentration * 10))
  return { combinedProbability, riskScore }
}

export interface AccumulatorPerformanceSummary {
  tickets: number
  open: number
  settled: number
  won: number
  lost: number
  void: number
  exposure: number
  settledStake: number
  returned: number
  profitLoss: number
}

export function accumulatorPerformance(rows: CustomAccumulator[]): AccumulatorPerformanceSummary {
  return rows.reduce((summary, row) => {
    const settled = row.status === 'won' || row.status === 'lost' || row.status === 'void'
    const stake = row.stake ?? 0
    summary.tickets += 1
    summary.exposure += !settled ? stake : 0
    if (!settled) summary.open += 1
    if (settled) {
      summary.settled += 1
      summary.settledStake += stake
      summary.returned += row.actual_return ?? 0
      if (row.status === 'won') summary.won += 1
      if (row.status === 'lost') summary.lost += 1
      if (row.status === 'void') summary.void += 1
      summary.profitLoss += (row.actual_return ?? 0) - stake
    }
    return summary
  }, { tickets: 0, open: 0, settled: 0, won: 0, lost: 0, void: 0, exposure: 0, settledStake: 0, returned: 0, profitLoss: 0 } as AccumulatorPerformanceSummary)
}

export function roiFromTotals(profitLoss: number, staked: number): number | null {
  return staked > 0 ? profitLoss / staked : null
}

export function simulatorTotals(rows: Array<{ settled: boolean; returnAmount: number | null | undefined }>, stake: number) {
  const settledRows = rows.filter(row => row.settled)
  const staked = settledRows.length * stake
  const returned = settledRows.reduce((sum, row) => sum + (row.returnAmount ?? 0) * stake, 0)
  return { staked, returned, profitLoss: returned - staked }
}

export function accumulatorReturnMultiplier(row: Pick<CustomAccumulator, 'status' | 'stake' | 'actual_return' | 'combined_odds'>): number {
  if (row.status === 'lost') return 0
  if (row.status === 'void') return 1
  if (row.status === 'won') {
    if (row.actual_return != null && row.stake != null && row.stake > 0) return row.actual_return / row.stake
    return row.combined_odds
  }
  return 0
}

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
  const [dailyAccumulators, setDailyAccumulators] = useState<CustomAccumulator[]>([])
  const [performance, setPerformance] = useState<PerformanceSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const [resultFilter, setResultFilter] = useState('all')
  const [modelFilter, setModelFilter] = useState('current')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [expandedAccumulatorId, setExpandedAccumulatorId] = useState<number | null>(null)
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
      fetchCustomAccumulators(),
    ]).then(([history, summary, accumulators]) => {
      setAllVersions(history)
      setPerformance(summary)
      setDailyAccumulators(accumulators.filter(row => row.automatic))
    })
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
  const settlementCounts = useMemo(() => {
    const counts = new Map<string, { won: number; lost: number; void: number }>()
    for (const row of scopeRows) {
      const current = counts.get(row.ticket_type) ?? { won: 0, lost: 0, void: 0 }
      if (row.result === 'won' || row.result === 'lost' || row.result === 'void') current[row.result] += 1
      counts.set(row.ticket_type, current)
    }
    return counts
  }, [scopeRows])
  const allSettlementCounts = useMemo(() => {
    return scopeRows.reduce((counts, row) => {
      if (row.result === 'won' || row.result === 'lost' || row.result === 'void') counts[row.result] += 1
      return counts
    }, { won: 0, lost: 0, void: 0 })
  }, [scopeRows])
  const filtered = useMemo(() => scopeRows.filter(row =>
    (typeFilter === 'all' || typeFilter === 'my_accumulator' || row.ticket_type === typeFilter)
    && (resultFilter === 'all' || (row.result ?? 'pending') === resultFilter)
  ), [scopeRows, typeFilter, resultFilter])

  const groupedRows = useMemo(() => {
    const years = new Map<string, Map<string, Map<string, TicketHistoryItem[]>>>()
    for (const row of filtered) {
      const [year, month, day] = row.target_date.split('-')
      if (!year || !month || !day) continue
      const months = years.get(year) ?? new Map<string, Map<string, TicketHistoryItem[]>>()
      const dates = months.get(month) ?? new Map<string, TicketHistoryItem[]>()
      dates.set(day, [...(dates.get(day) ?? []), row])
      months.set(month, dates)
      years.set(year, months)
    }
    return [...years.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([year, months]) => {
      const monthGroups = [...months.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([month, dates]) => {
        const dateGroups = [...dates.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([day, rows]) => {
          const date = `${year}-${month}-${day}`
          return { date, label: new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' }), rows }
        })
        return { month, monthLabel: new Date(`${year}-${month}-01T00:00:00`).toLocaleDateString(undefined, { month: 'long' }), dates: dateGroups }
      })
      return { year, months: monthGroups }
    })
  }, [filtered])

  const productionRows = scopeRows.filter(row => !row.internal_only)
  const productionSettled = productionRows.filter(row => row.result && row.result !== 'pending')
  const productionWins = productionSettled.filter(row => row.result === 'won').length
  const productionStaked = productionSettled.reduce((sum, row) => sum + (row.stake ?? 0), 0)
  const productionPnl = productionSettled.reduce((sum, row) => sum + (row.profit_loss ?? 0), 0)
  const productionInterval = wilsonInterval(productionWins, productionSettled.length)
  const orderedDailyAccumulators = [...dailyAccumulators].sort((a, b) => b.target_date.localeCompare(a.target_date))
  const visibleDailyAccumulators = orderedDailyAccumulators.filter(row =>
    (!dateFrom || row.target_date >= dateFrom) && (!dateTo || row.target_date <= dateTo)
    && (resultFilter === 'all' || (row.status === 'draft' ? 'pending' : row.status) === resultFilter)
  )
  const groupedDailyAccumulators = useMemo(() => {
    const years = new Map<string, Map<string, Map<string, CustomAccumulator[]>>>()
    for (const row of visibleDailyAccumulators) {
      const [year, month, day] = row.target_date.split('-')
      if (!year || !month || !day) continue
      const months = years.get(year) ?? new Map<string, Map<string, CustomAccumulator[]>>()
      const dates = months.get(month) ?? new Map<string, CustomAccumulator[]>()
      dates.set(day, [...(dates.get(day) ?? []), row])
      months.set(month, dates)
      years.set(year, months)
    }
    return [...years.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([year, months]) => {
      const monthGroups = [...months.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([month, dates]) => {
        const dateGroups = [...dates.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([day, rows]) => {
          const date = `${year}-${month}-${day}`
          return {
            date,
            label: new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' }),
            rows,
          }
        })
        return {
          month,
          monthLabel: new Date(`${year}-${month}-01T00:00:00`).toLocaleDateString(undefined, { month: 'long' }),
          dates: dateGroups,
        }
      })
      return { year, months: monthGroups }
    })
  }, [visibleDailyAccumulators])
  const accumulatorName = 'My Accumulators'
  const accumulatorSettlementCounts = orderedDailyAccumulators.reduce((counts, row) => {
    const result = row.status === 'draft' ? 'pending' : row.status
    if (result === 'won' || result === 'lost' || result === 'void') counts[result] += 1
    return counts
  }, { won: 0, lost: 0, void: 0 })
  const accumulatorSummary = accumulatorPerformance(orderedDailyAccumulators)
  const accumulatorRoi = roiFromTotals(accumulatorSummary.profitLoss, accumulatorSummary.settledStake)
  const distinctPublishedDays = new Set(productionRows.map(row => row.target_date)).size
  const distinctSettledDays = new Set(productionSettled.map(row => row.target_date)).size
  const firstDate = productionRows.length ? productionRows.map(row => row.target_date).sort()[0] : null
  const observedDays = firstDate ? Math.max(1, Math.floor((Date.now() - new Date(`${firstDate}T00:00:00`).getTime()) / 86_400_000) + 1) : 0
  const observationProgress = Math.min(100, observedDays / 56 * 100)
  const stake = Math.max(0, Number(stakeSize) || 0)
  const simulated = typeFilter === 'my_accumulator'
    ? simulatorTotals(
      visibleDailyAccumulators.map(row => ({
        settled: row.status === 'won' || row.status === 'lost' || row.status === 'void',
        returnAmount: accumulatorReturnMultiplier(row),
      })),
      stake,
    )
    : simulatorTotals(
      filtered.map(row => ({
        settled: Boolean(row.result && row.result !== 'pending'),
        returnAmount: row.return_amount,
      })),
      stake,
    )
  const simulatedStaked = simulated.staked
  const simulatedReturned = simulated.returned
  const simulatedPnl = simulated.profitLoss
  const simulatedRoi = roiFromTotals(simulatedPnl, simulatedStaked)
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

  function renderAccumulatorRow(row: CustomAccumulator) {
    const outcome = row.status === 'draft' ? 'pending' : row.status
    const metrics = accumulatorMetrics(row)
    const expanded = expandedAccumulatorId === row.id
    const pendingLegs = row.legs.filter(leg => leg.result === 'pending').length
    const statusTone = outcome === 'placed' && pendingLegs > 0 ? 'open' : outcome
    const statusLabel = outcome === 'placed' && pendingLegs > 0 ? `Open · ${pendingLegs} pending` : outcome
    return <article className={`paper-ledger-row daily-accumulator-inline${expanded ? ' expanded' : ''}`} key={`accumulator-${row.id}`}>
      <button className="paper-ticket-summary daily-accumulator-summary" onClick={() => setExpandedAccumulatorId(expanded ? null : row.id)} aria-expanded={expanded} aria-label={`${row.name} accumulator with ${row.legs.length} legs`}>
        <div className="paper-ticket-identity"><span className="portfolio-tier accumulator">{row.name}</span><small>daily merge · {row.legs.length} legs · model snapshots</small></div>
        <div><span>Odds</span><strong>{row.combined_odds.toFixed(2)}×</strong></div>
        <div><span>Legs</span><strong>{row.legs.length}</strong></div>
        <div title="Product of the immutable leg probability snapshots"><span>Adjusted P</span><strong>{metrics.combinedProbability == null ? '—' : pct(metrics.combinedProbability)}</strong></div>
        <div title="Transparent accumulator estimate; correlation haircut is not available for personal tickets"><span>Risk</span><strong>{metrics.riskScore == null ? '—' : `${metrics.riskScore.toFixed(0)}/100`}</strong></div>
        <div className="paper-outcome"><span className={`settlement-pill ${statusTone}`}>{statusLabel}</span>{row.actual_return != null && row.stake != null && <strong className={row.actual_return - row.stake >= 0 ? 'positive' : 'negative'}>{fmtPnl(row.actual_return - row.stake)}</strong>}</div>
        <span className="paper-chevron" aria-hidden="true">{expanded ? '⌃' : '⌄'}</span>
      </button>
      {expanded && <div className="paper-ticket-detail"><div className="paper-version-toolbar"><div><strong>Immutable selection snapshot</strong><span>Generated from the day&apos;s Conservative and Balanced tickets; selections cannot be edited here.</span></div><div><span className="custom-status-badge automatic">Auto merge</span></div></div>{row.source_ticket_snapshots && Object.keys(row.source_ticket_snapshots).length > 0 && <div className="source-ticket-history"><strong>Source ticket versions</strong>{Object.entries(row.source_ticket_snapshots).map(([type, snapshot]) => <span key={type} title={snapshot.publication_hash ? `Publication hash ${snapshot.publication_hash}` : undefined}>{sourceTicketLabel(type)} · v{snapshot.version ?? '—'}{snapshot.model_version ? ` · model ${snapshot.model_version}` : ''}</span>)}</div>}
        <ul>{row.legs.map(leg => <li key={leg.id}><div><strong>{leg.home_team} <span>vs</span> {leg.away_team}</strong><small><span className="paper-kickoff-date">{formatDate(leg.kickoff_at)}</span> · {formatKickoff(leg.kickoff_at)}</small><b className="paper-market-badge">{formatMarket(leg.market)}</b>{leg.source_ticket_type && <b className="source-ticket-badge">{leg.source_conflict ? 'Conservative priority' : sourceTicketLabel(leg.source_ticket_type)}{leg.source_ticket_version ? ` · v${leg.source_ticket_version}` : ''}</b>}</div><div><strong>{leg.odds_snapshot.toFixed(2)}</strong><small>Q {leg.q_score_snapshot == null ? '—' : leg.q_score_snapshot.toFixed(1)} · {leg.home_goals != null && leg.away_goals != null && <><b className="paper-match-score">{leg.home_goals}–{leg.away_goals}</b> · </>}<b className={`paper-leg-result ${leg.result}`}>{leg.result}</b></small></div></li>)}</ul>
        <div className="paper-audit"><span>Auto merge · {row.name}</span><span>Generated {new Date(row.created_at).toLocaleString()}</span></div><div className="paper-version-compare"><strong>Merge policy:</strong> Conservative selections have priority when both source tickets contain the same match. Chance and risk are derived from the immutable leg snapshots.</div>
      </div>}
    </article>
  }

  if (loading) return <div className="paper-ledger-skeleton" aria-busy="true">Loading immutable paper portfolio…</div>
  if (error && !allVersions.length) return <div className="analytics-empty"><strong>Paper portfolio unavailable</strong><span>{error}</span></div>

  return <>
    {error && <div className="tracker-message error" role="alert">{error}</div>}
    <div className="paper-scope-heading">
      <div><span className="eyebrow">Production-tier evidence</span><strong>Conservative, Balanced and High Odds</strong><p>Only active public ticket tiers are included in this release view.</p></div>
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

    {dailyAccumulators.length > 0 && <section className="accumulator-performance-card"><div className="accumulator-performance-heading"><div><span className="eyebrow">Personal accumulator record</span><strong>My accumulator performance</strong><p>Separate from production evidence. Open tickets remain exposure until every leg settles.</p></div><span className="accumulator-performance-count">{accumulatorSummary.tickets} ticket{accumulatorSummary.tickets === 1 ? '' : 's'}</span></div><div className="accumulator-performance-grid"><LedgerMetric label="Open" value={accumulatorSummary.open.toString()} note={accumulatorSummary.exposure ? `${fmt(accumulatorSummary.exposure)} exposed` : 'No open exposure'} /><LedgerMetric label="Settled" value={accumulatorSummary.settled.toString()} note={`${accumulatorSummary.won} won · ${accumulatorSummary.lost} lost · ${accumulatorSummary.void} void`} /><LedgerMetric label="Settled P&L" value={accumulatorSummary.settled ? fmtPnl(accumulatorSummary.profitLoss) : '—'} note={accumulatorSummary.settled ? `${fmt(accumulatorSummary.returned)} returned` : 'Settles after all legs finish'} tone={accumulatorSummary.profitLoss >= 0 ? 'positive' : 'negative'} /><LedgerMetric label="Settled ROI" value={accumulatorRoi == null ? '—' : pct(accumulatorRoi)} note={accumulatorSummary.settled ? `${fmt(accumulatorSummary.settledStake)} settled stake` : 'No settled stake yet'} tone={accumulatorRoi == null ? undefined : accumulatorRoi >= 0 ? 'positive' : 'negative'} /></div></section>}


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

    <section className="stake-simulator"><div className="stake-simulator-copy"><span className="eyebrow">What-if sizing</span><strong>Stake simulator · current filters</strong><p>Replays the settled rows currently shown. This does not change the immutable one-unit ledger.</p><div className="paper-ticket-type-filter" aria-label="Ticket type filter">{['all', 'safe', 'balanced', 'high_odds', 'my_accumulator'].map(value => {
      const counts = value === 'all' ? allSettlementCounts : value === 'my_accumulator' ? accumulatorSettlementCounts : settlementCounts.get(value) ?? { won: 0, lost: 0, void: 0 }
      const label = value === 'all' ? 'All tickets' : value === 'my_accumulator' ? accumulatorName : formatTicketType(value)
      return <button key={value} className={`filter-tab${typeFilter === value ? ' active' : ''}`} onClick={() => setTypeFilter(value)} title={`${label}: ${counts.won} won, ${counts.lost} lost, ${counts.void} void`}><span>{label}</span><small className="filter-tab-outcomes" aria-label={`${counts.won} won, ${counts.lost} lost, ${counts.void} void`}><b className="won">W {counts.won}</b><b className="lost">L {counts.lost}</b><b className="void">V {counts.void}</b></small></button>
    })}</div></div><div className="stake-simulator-filter-controls" aria-label="Ledger filters"><label>Outcome<select aria-label="Settlement filter" value={resultFilter} onChange={event => setResultFilter(event.target.value)}><option value="all">All outcomes</option><option value="pending">Pending</option><option value="won">Won</option><option value="lost">Lost</option><option value="void">Void</option></select></label><label>Model<select aria-label="Model version filter" value={modelFilter} onChange={event => setModelFilter(event.target.value)}><option value="current">Current · {currentModel ?? 'loading'}</option><option value="all">All model versions</option>{modelVersions.filter(version => version !== currentModel).map(version => <option key={version} value={version}>{version}</option>)}</select></label><label>From<input aria-label="Tickets from date" type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)} /></label><label>To<input aria-label="Tickets to date" type="date" value={dateTo} onChange={event => setDateTo(event.target.value)} /></label></div><label>Stake per ticket<input type="number" min="0" step="0.01" value={stakeSize} onChange={event => setStakeSize(event.target.value)} /></label><div className="stake-simulator-metrics"><div><span>Staked</span><strong>{fmt(simulatedStaked)}</strong></div><div><span>Return</span><strong>{fmt(simulatedReturned)}</strong></div><div><span>P&amp;L</span><strong className={simulatedPnl >= 0 ? 'positive' : 'negative'}>{fmtPnl(simulatedPnl)}</strong></div><div><span>ROI</span><strong className={simulatedRoi == null ? '' : simulatedRoi >= 0 ? 'positive' : 'negative'}>{simulatedRoi == null ? '—' : pct(simulatedRoi)}</strong></div></div></section>

    {typeFilter === 'my_accumulator' ? <div className="paper-ledger-list">{visibleDailyAccumulators.length === 0 ? <div className="analytics-empty"><strong>No accumulator matches these filters</strong><span>Try another outcome or date range.</span></div> : groupedDailyAccumulators.map(yearGroup => <details className="paper-year-group" key={yearGroup.year} open>
      <summary className="paper-year-heading">{yearGroup.year}</summary>
      <div className="paper-year-body">{yearGroup.months.map(monthGroup => <details className="paper-month-group" key={`${yearGroup.year}-${monthGroup.month}`} open>
        <summary className="paper-month-heading">{monthGroup.monthLabel}</summary>
        <div className="paper-month-body">{monthGroup.dates.map(dayGroup => <section className="paper-date-group" key={dayGroup.date}>
          <div className="paper-date-heading"><strong>{dayGroup.label}</strong><span>{dayGroup.rows.length} accumulator{dayGroup.rows.length === 1 ? '' : 's'}</span></div>
          {dayGroup.rows.map(row => renderAccumulatorRow(row))}
        </section>)}</div>
      </details>)}</div>
    </details>)}</div> : filtered.length === 0 ? <div className="analytics-empty"><strong>No tickets match these filters</strong><span>Try another tier, outcome, model version or date range.</span></div> : <div className="paper-ledger-list">{groupedRows.map(yearGroup => <details className="paper-year-group" key={yearGroup.year} open>
      <summary className="paper-year-heading">{yearGroup.year}</summary>
      <div className="paper-year-body">{yearGroup.months.map(monthGroup => <details className="paper-month-group" key={`${yearGroup.year}-${monthGroup.month}`} open>
        <summary className="paper-month-heading">{monthGroup.monthLabel}</summary>
        <div className="paper-month-body">{monthGroup.dates.map(dayGroup => <section className="paper-date-group" key={dayGroup.date}>
          <div className="paper-date-heading"><strong>{dayGroup.label}</strong><span>{dayGroup.rows.length} latest cohort{dayGroup.rows.length === 1 ? '' : 's'}</span></div>
          {typeFilter === 'all' && visibleDailyAccumulators.filter(row => row.target_date === dayGroup.date).map(row => renderAccumulatorRow(row))}
          {dayGroup.rows.map(row => {
        const versions = allVersions.filter(item => cohortKey(item) === cohortKey(row)).sort((a, b) => b.version - a.version)
        return <article className={`paper-ledger-row${expandedId === row.ticket_id ? ' expanded' : ''}`} key={row.ticket_id}>
          <button className="paper-ticket-summary" onClick={() => toggleTicket(row)} aria-expanded={expandedId === row.ticket_id} aria-label={`${row.name} ticket for ${dayGroup.label}`}>
            <div className="paper-ticket-identity"><span className={`portfolio-tier ${row.ticket_type}`}>{row.name}</span><small>latest v{row.version} · model {row.model_version}{row.internal_only ? ' · internal' : ''}</small></div>
            <div><span>Odds</span><strong>{row.combined_odds == null ? 'Pro only' : `${row.combined_odds.toFixed(2)}×`}</strong></div><div><span>Legs</span><strong>{row.leg_count}</strong></div><div><span>Adjusted P</span><strong>{pct(row.adjusted_probability)}</strong></div><div><span>Risk</span><strong>{row.risk_score == null ? 'Pro only' : `${row.risk_score.toFixed(0)}/100`}</strong></div>
            <div className="paper-outcome"><span className={`settlement-pill ${row.result ?? 'pending'}`}>{row.result ?? 'pending'}</span>{row.profit_loss != null && <strong className={row.profit_loss >= 0 ? 'positive' : 'negative'}>{fmtPnl(row.profit_loss)}</strong>}</div><span className="paper-chevron" aria-hidden="true">{expandedId === row.ticket_id ? '⌃' : '⌄'}</span>
          </button>
          {expandedId === row.ticket_id && <div className="paper-ticket-detail"><div className="paper-version-toolbar"><div><strong>Immutable versions</strong><span>Select a version to inspect and compare with its predecessor.</span></div><div>{versions.map(version => <button key={version.ticket_id} className={selectedVersion?.ticket_id === version.ticket_id ? 'active' : ''} onClick={() => loadVersion(version)}>v{version.version}</button>)}</div></div>
            {ticketLoading && <div className="odds-state"><span className="spinner" />Loading published selections…</div>}
            {ticket && <><ul>{ticket.legs.map(leg => <li key={leg.selection_id}><div><strong>{leg.home_team} <span>vs</span> {leg.away_team}</strong><small><span className="paper-kickoff-date">{formatDate(leg.kickoff_at)}</span> · {formatKickoff(leg.kickoff_at)}</small><b className="paper-market-badge">{formatMarket(leg.market)}</b></div><div><strong>{leg.best_odds == null ? 'Pro only' : leg.best_odds.toFixed(2)}</strong><small>Q {leg.q_score == null ? 'Pro only' : leg.q_score.toFixed(1)} · {leg.q_grade && <GradeBadge grade={leg.q_grade} />} · {leg.home_goals != null && leg.away_goals != null && <><b className="paper-match-score">{leg.home_goals}–{leg.away_goals}</b> · </>}<b className={`paper-leg-result ${leg.result}`}>{leg.result}</b></small></div></li>)}</ul><div className="paper-audit"><code title={ticket.publication_hash}>Hash {ticket.publication_hash}</code><span>Published {new Date(ticket.published_at).toLocaleString()}</span></div>{compareTicket ? <div className="paper-version-compare"><strong>Compared with v{compareTicket.version}:</strong> {ticket.legs.filter(a => !compareTicket.legs.some(b => b.match_id === a.match_id && b.market === a.market && b.selection === a.selection)).length} added · {compareTicket.legs.filter(a => !ticket.legs.some(b => b.match_id === a.match_id && b.market === a.market && b.selection === a.selection)).length} removed</div> : <div className="paper-version-compare"><strong>Original publication:</strong> no predecessor exists for this cohort.</div>}</>}
          </div>}
        </article>
          })}
        </section>)}</div>
      </details>)}</div>
    </details>)}</div>}
  </>
}

function LedgerMetric({ label, value, note, tone }: { label: string; value: string; note: string; tone?: 'positive' | 'negative' | 'warning' }) {
  return <div className={`stat-card metric-card${tone ? ` ${tone}` : ''}`}><div className="kpi-label">{label}</div><div className="kpi-value">{value}</div><div className="kpi-note">{note}</div></div>
}
function EvidenceCheck({ label, value, note }: { label: string; value: string; note: string }) { return <div><span>{label}</span><strong>{value}</strong><small>{note}</small></div> }
