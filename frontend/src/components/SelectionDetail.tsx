import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchSelectionOdds, formatKickoff, formatMarket, formatSelection, type OddsQuote } from '../lib/api'
import GradeBadge from './GradeBadge'

export interface DetailSelection {
  selection_id?: number
  prediction_id: number
  match_id: number
  home_team: string
  away_team: string
  competition: string
  kickoff_at: string
  market: string
  selection: string
  model_probability?: number | null
  best_odds: number | null
  q_score: number | null
  q_grade?: string | null
  edge: number | null
  expected_value?: number | null
  source_odds_at?: string | null
  result?: string
  bookmaker_count?: number | null
  avg_implied?: number | null
  model_agreement?: number | null
  home_goals?: number | null
  away_goals?: number | null
  selection_settled_at?: string | null
}

interface Props {
  selection: DetailSelection
  modelVersion?: string
  publishedAt?: string
  /** 'market': the leg's probability is the bookmaker's fair price, not a model estimate. */
  pricing?: 'market' | 'model'
  onClose: () => void
}

const pct = (value: number | null | undefined, signed = false) => {
  if (value == null) return '—'
  return `${signed && value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`
}

function spreadLabel(value: number | null | undefined) {
  if (value == null) return 'Unavailable'
  if (value <= 0.05) return 'Strong alignment'
  if (value <= 0.10) return 'Acceptable'
  if (value <= 0.15) return 'Caution'
  return 'High disagreement'
}

function freshness(iso?: string | null) {
  if (!iso) return { label: 'Timestamp unavailable', state: 'unknown' }
  const ageHours = Math.max(0, (Date.now() - new Date(iso).getTime()) / 3_600_000)
  if (ageHours <= 2) return { label: 'Fresh price snapshot', state: 'fresh' }
  if (ageHours <= 8) return { label: `${Math.floor(ageHours)}h-old snapshot`, state: 'aging' }
  return { label: 'Historical price snapshot', state: 'stale' }
}

function marketDecisionStrength(selection: DetailSelection, priceState: ReturnType<typeof freshness>) {
  // Market-priced legs claim no edge: judge the price (margin) and its freshness.
  const cautions: string[] = []
  if (selection.expected_value != null && selection.expected_value < -0.05) cautions.push('Bookmaker margin above 5%')
  if (priceState.state === 'stale' || priceState.state === 'unknown') cautions.push('Odds are not fresh')
  const strong = priceState.state === 'fresh' && (selection.expected_value ?? -1) >= -0.03
  const caution = cautions.length > 0
  return { label: strong ? 'Strong' : caution ? 'Review' : 'Acceptable', tone: strong ? 'strong' : caution ? 'caution' : 'acceptable', cautions }
}

function decisionStrength(selection: DetailSelection, priceState: ReturnType<typeof freshness>) {
  const cautions: string[] = []
  if (selection.edge == null) cautions.push('No captured market edge')
  else if (selection.edge <= 0) cautions.push('No positive edge')
  if (selection.model_agreement == null) cautions.push('Model spread unavailable')
  else if (selection.model_agreement > 0.15) cautions.push('High model disagreement')
  else if (selection.model_agreement > 0.10) cautions.push('Model spread needs review')
  if (priceState.state === 'stale' || priceState.state === 'unknown') cautions.push('Odds are not fresh')
  if (selection.q_score == null) cautions.push('Q-score unavailable')
  else if (selection.q_score < 80) cautions.push('Below balanced-ticket threshold')
  const strong = (selection.q_score ?? 0) >= 85 && (selection.edge ?? 0) > 0 && (selection.model_agreement ?? 1) <= 0.05 && priceState.state === 'fresh'
  const caution = cautions.length > 0
  return { label: strong ? 'Strong' : caution ? 'Review' : 'Acceptable', tone: strong ? 'strong' : caution ? 'caution' : 'acceptable', cautions }
}

export default function SelectionDetail({ selection, modelVersion, publishedAt, pricing, onClose }: Props) {
  const market = pricing === 'market'
  const label = market
    ? { prob: 'Fair chance', edge: 'Price vs fair', probRow: 'Fair chance (margin removed)', impliedRow: 'Quoted price implies', subtitle: 'Fair price vs quoted price', ev: 'Long-run return at this price against the fair chance. Negative values are the bookmaker margin.' }
    : { prob: 'Calibrated probability', edge: 'Positive edge', probRow: 'Lohela model', impliedRow: 'Market implied', subtitle: 'Model vs market', ev: 'Theoretical value based on the model probability and captured odds. It is not a guaranteed return or win probability.' }
  const [activeTab, setActiveTab] = useState<'overview' | 'stats' | 'probability' | 'h2h' | 'signals' | 'odds'>('probability')
  const [copied, setCopied] = useState(false)
  const [stake, setStake] = useState('10')
  const [betMessage, setBetMessage] = useState('')
  const [betting, setBetting] = useState(false)
  const [quotes, setQuotes] = useState<OddsQuote[]>([])
  const [quotesLoading, setQuotesLoading] = useState(true)
  const [quotesError, setQuotesError] = useState(false)
  const [form, setForm] = useState<{ home: TeamForm; away: TeamForm; h2h: H2H[] } | null>(null)
  const drawerRef = useRef<HTMLElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const implied = selection.avg_implied ?? (selection.best_odds ? 1 / selection.best_odds : null)
  const priceState = useMemo(() => freshness(selection.source_odds_at), [selection.source_odds_at])
  const strength = useMemo(() => (market ? marketDecisionStrength : decisionStrength)(selection, priceState), [selection, priceState, market])
  const stakeValue = Number(stake)
  const hasPublishedBetSource = Boolean(selection.selection_id && selection.best_odds)
  const canConfirmBet = hasPublishedBetSource && Number.isFinite(stakeValue) && stakeValue > 0

  useEffect(() => {
    const priorOverflow = document.body.style.overflow
    const previouslyFocused = document.activeElement as HTMLElement | null
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()
    const handleKeys = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key !== 'Tab' || !drawerRef.current) return
      const focusable = [...drawerRef.current.querySelectorAll<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
        .filter(element => !element.hasAttribute('disabled'))
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', handleKeys)
    return () => {
      document.body.style.overflow = priorOverflow
      window.removeEventListener('keydown', handleKeys)
      previouslyFocused?.focus()
    }
  }, [onClose])

  useEffect(() => {
    let active = true
    setQuotesLoading(true)
    setQuotesError(false)
    fetchSelectionOdds(selection.prediction_id)
      .then(rows => { if (active) setQuotes(rows) })
      .catch(() => { if (active) setQuotesError(true) })
      .finally(() => { if (active) setQuotesLoading(false) })
    return () => { active = false }
  }, [selection.prediction_id])

  useEffect(() => { fetch(`/api/v1/selections/match-context/${selection.match_id}`).then(response => response.ok ? response.json() : Promise.reject()).then(setForm).catch(() => setForm(null)) }, [selection.match_id])

  async function copySummary() {
    const summary = [
      `${selection.home_team} vs ${selection.away_team}`,
      `${formatMarket(selection.market)} @ ${selection.best_odds?.toFixed(2) ?? 'n/a'}`,
      market
        ? `fair chance ${pct(selection.model_probability)} · return at price ${pct(selection.expected_value, true)}`
        : `Q ${selection.q_score == null ? 'Pro only' : selection.q_score.toFixed(1)} · model ${pct(selection.model_probability)} · edge ${pct(selection.edge, true)}`,
      'Lohela research / paper trading only',
    ].join('\n')
    await navigator.clipboard?.writeText(summary)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  async function confirmIndividualBet() {
    if (!selection.selection_id) { setBetMessage('This selection cannot be confirmed from the current view.'); return }
    setBetting(true); setBetMessage('')
    try {
      const response = await fetch('/api/v1/bets/individual', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ selection_id: selection.selection_id, stake: Number(stake) }) })
      const body = await response.json()
      if (!response.ok) throw new Error(body.detail || 'Could not record the bet')
      setBetMessage(`Recorded as pending bet #${body.id}.`)
    } catch (error) { setBetMessage(error instanceof Error ? error.message : 'Could not record the bet') } finally { setBetting(false) }
  }

  return (
    <div className="detail-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
      <aside ref={drawerRef} className="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="selection-detail-title">
        <div className="detail-header">
          <div>
            <span className="detail-eyebrow">Selection evidence</span>
            <h2 id="selection-detail-title">{selection.home_team} <span>vs</span> {selection.away_team}</h2>
            <p>{selection.competition} · <span className="kickoff-time">{formatKickoff(selection.kickoff_at)}</span></p>
          </div>
          <button ref={closeRef} className="detail-close" onClick={onClose} aria-label="Close selection details">×</button>
        </div>

        <div className="detail-status-row">
          <span className={`freshness-chip ${priceState.state}`}><span />{priceState.label}</span>
          <span className={`decision-strength-chip ${strength.tone}`}><span />Decision strength: <strong>{strength.label}</strong></span>
          {selection.result && <span className={`result-chip ${selection.result}`}>{selection.result === 'pending' && selection.home_goals != null ? 'Settlement pending' : selection.result}</span>}
        </div>

        <section className="decision-strength-card" aria-label="Decision strength review">
          <div className="evidence-title"><h3>Decision strength</h3><span>Leg-level review · advisory</span></div>
          <div className="decision-strength-grid">
            <Metric label={label.prob} value={pct(selection.model_probability)} />
            <Metric label={label.edge} value={pct(selection.edge, true)} />
            <Metric label="Model spread" value={selection.model_agreement == null ? '—' : `${(selection.model_agreement * 100).toFixed(1)} pp`} note={spreadLabel(selection.model_agreement)} />
            <Metric label="Historical evidence" value="Analytics" note="Use settled market/Q-score samples; not an individual-match guarantee." />
          </div>
          {strength.cautions.length > 0 && <p className="decision-strength-note"><strong>Review before including:</strong> {strength.cautions.join(' · ')}.</p>}
          {strength.cautions.length === 0 && <p className="decision-strength-note positive"><strong>Passes the visible checks.</strong> Confirm the published odds before treating this as an acca leg.</p>}
        </section>

        <section className="detail-pick">
          <div><span>Market</span><strong>{formatMarket(selection.market)}</strong></div>
          <div><span>Snapshot odds</span><strong>{selection.best_odds?.toFixed(2) ?? '—'}</strong></div>
          <div><span>Q grade</span><strong>{selection.q_grade ? <GradeBadge grade={selection.q_grade} /> : `Q ${selection.q_score == null ? 'Pro only' : selection.q_score.toFixed(1)}`}</strong></div>
          <div><span>Match result</span><strong>{selection.home_goals != null && selection.away_goals != null ? `${selection.home_goals}–${selection.away_goals}` : 'Unplayed'}</strong></div>
          <div><span>Market settlement</span><strong>{selection.result === 'pending' && selection.home_goals != null ? 'Pending' : selection.result ?? 'Pending'}</strong></div>
        </section>

        <div className="detail-tabs" role="tablist" aria-label="Match analysis"><button className={activeTab === 'overview' ? 'active' : ''} onClick={() => setActiveTab('overview')}>Overview</button><button className={activeTab === 'stats' ? 'active' : ''} onClick={() => setActiveTab('stats')}>Stats</button><button className={activeTab === 'probability' ? 'active' : ''} onClick={() => setActiveTab('probability')}>Probability</button><button className={activeTab === 'h2h' ? 'active' : ''} onClick={() => setActiveTab('h2h')}>H2H</button><button className={activeTab === 'signals' ? 'active' : ''} onClick={() => setActiveTab('signals')}>Signals</button><button className={activeTab === 'odds' ? 'active' : ''} onClick={() => setActiveTab('odds')}>Odds</button></div>
        {form && activeTab === 'overview' && <section className="form-evidence-card"><div className="evidence-title"><h3>Recent form</h3><span>Last {Math.max(form.home.matches, form.away.matches)} finished matches</span></div><div className="form-teams"><TeamForm team={form.home} /><span className="form-vs">vs</span><TeamForm team={form.away} /></div></section>}
        {form && activeTab === 'stats' && <section className="form-evidence-card"><div className="evidence-title"><h3>Recent team statistics</h3><span>Last five finished matches</span></div><div className="team-stats-grid"><TeamStats team={form.home} /><TeamStats team={form.away} /></div></section>}
        {form && activeTab === 'h2h' && <section className="form-evidence-card"><div className="evidence-title"><h3>Head-to-head</h3><span>Previous finished meetings</span></div>{form.h2h.length ? <div className="h2h-list">{form.h2h.map(game => <div key={game.date + game.score}><span>{game.date}</span><strong>{game.home_team} {game.score} {game.away_team}</strong></div>)}</div> : <div className="detail-empty-note">No previous meetings found in the persisted data.</div>}</section>}
        {activeTab === 'signals' && <section className="form-evidence-card"><div className="evidence-title"><h3>Lohela signal</h3><span>Current published selection</span></div><div className="signal-summary"><div><span>Market</span><strong>{formatMarket(selection.market)}</strong></div><div><span>Selection</span><strong>{formatSelection(selection.selection)}</strong></div><div><span>{market ? label.prob : 'Model probability'}</span><strong>{pct(selection.model_probability)}</strong></div><div><span>{market ? label.edge : 'Estimated edge'}</span><strong>{pct(selection.edge, true)}</strong></div><div><span>Q-score</span><strong>{selection.q_score == null ? 'Pro only' : `${selection.q_score.toFixed(1)} · ${selection.q_grade ? 'graded' : '—'}`}</strong></div><div><span>Expected value</span><strong>{pct(selection.expected_value, true)}</strong></div></div></section>}

        {activeTab === 'probability' && <section className="evidence-card">
          <div className="evidence-title"><h3>Probability evidence</h3><span>{label.subtitle}</span></div>
          <ProbabilityRow label={label.probRow} value={selection.model_probability ?? null} tone="model" />
          <ProbabilityRow label={label.impliedRow} value={implied} tone="market" />
          <div className="edge-callout">
            <span>{market ? label.edge : 'Estimated edge'}</span>
            <strong>{pct(selection.edge, true)}</strong>
          </div>
        </section>}

        {activeTab === 'odds' && <OddsComparison quotes={quotes} loading={quotesLoading} failed={quotesError} />}

        {activeTab === 'probability' && <section className="detail-metrics">
          <Metric label="Q score" value={selection.q_score == null ? 'Pro only' : selection.q_score.toFixed(1)} />
          <Metric label="Expected value" value={pct(selection.expected_value, true)} hint={label.ev} />
          <Metric label="Model spread" value={selection.model_agreement == null ? '—' : `${(selection.model_agreement * 100).toFixed(1)} pp`} note={spreadLabel(selection.model_agreement)} hint="Standard deviation between the active model probabilities. Up to 5 pp is strong alignment; above 15 pp triggers a confidence downgrade." />
          <Metric label="Bookmakers" value={(quotes.length || selection.bookmaker_count)?.toString() ?? 'Snapshot'} />
        </section>}

        <section className="audit-panel">
          <div><span>Match ID</span><code>{selection.match_id}</code></div>
          <div><span>Odds captured</span><code>{selection.source_odds_at ? new Date(selection.source_odds_at).toLocaleString() : 'Unavailable'}</code></div>
          <div><span>Published</span><code>{publishedAt ? new Date(publishedAt).toLocaleString() : 'Selection pool'}</code></div>
          <div><span>Model</span><code>{modelVersion ?? 'Latest completed run'}</code></div>
          <div><span>Market settled</span><code>{selection.selection_settled_at ? new Date(selection.selection_settled_at).toLocaleString() : 'Pending'}</code></div>
        </section>

        <div className="detail-disclaimer">Probabilities are estimates, not guarantees. Expected value is theoretical, not a guaranteed return, and price movement after publication can materially change the calculation.</div>
        <section className="detail-bet-panel" aria-labelledby="individual-bet-title">
          <div className="detail-bet-heading">
            <strong id="individual-bet-title">Individual bet</strong>
            <p>Record this published selection separately for settlement and P&amp;L tracking.</p>
          </div>
          <div className="detail-bet-controls">
            <label className="detail-stake-field">
              <span>Stake amount</span>
              <input
                type="number"
                min="0.01"
                step="0.01"
                inputMode="decimal"
                value={stake}
                onChange={event => setStake(event.target.value)}
                aria-describedby={!hasPublishedBetSource ? 'individual-bet-availability' : undefined}
              />
            </label>
            <button className="copy-summary-btn detail-bet-confirm" disabled={betting || !canConfirmBet} onClick={confirmIndividualBet}>
              {betting ? 'Recording…' : 'Confirm individual bet'}
            </button>
          </div>
          {!hasPublishedBetSource && <p className="detail-bet-availability" id="individual-bet-availability">Available only for a published ticket leg with immutable odds provenance.</p>}
          {betMessage && <p className="detail-bet-message" role="status">{betMessage}</p>}
        </section>
        <div className="detail-footer-actions">
          <button className="copy-summary-btn secondary" onClick={copySummary}>{copied ? 'Copied' : 'Copy research summary'}</button>
        </div>
      </aside>
    </div>
  )
}

interface TeamForm { team: string; results: string[]; wins: number; draws: number; losses: number; matches: number; goals_for: number; goals_against: number; goal_difference: number; avg_goals_for: number; avg_goals_against: number; points_per_game: number }
interface H2H { date: string; home_team: string; away_team: string; score: string }
function TeamForm({ team }: { team: TeamForm }) { return <div className="team-form"><strong>{team.team}</strong><div className="form-pills">{team.results.length ? team.results.map((result, index) => <b className={`form-pill ${result.toLowerCase()}`} key={`${result}-${index}`}>{result}</b>) : <span>Form unavailable</span>}</div><small>{team.wins}W · {team.draws}D · {team.losses}L</small></div> }
function TeamStats({ team }: { team: TeamForm }) { return <div className="team-stats"><strong>{team.team}</strong><span>Played <b>{team.matches}</b></span><span>Win rate <b>{team.matches ? `${Math.round(team.wins / team.matches * 100)}%` : '—'}</b></span><span>Goals for / against <b>{team.avg_goals_for.toFixed(1)} / {team.avg_goals_against.toFixed(1)}</b></span><span>Goal difference <b>{team.goal_difference > 0 ? '+' : ''}{team.goal_difference}</b></span><span>Points per game <b>{team.points_per_game.toFixed(2)}</b></span></div> }

function OddsComparison({ quotes, loading, failed }: { quotes: OddsQuote[]; loading: boolean; failed: boolean }) {
  const spread = quotes.length > 1 ? quotes[0].decimal_odds - quotes[quotes.length - 1].decimal_odds : 0
  return (
    <section className="odds-comparison">
      <div className="evidence-title">
        <h3>Bookmaker comparison</h3>
        <span>{quotes.length > 1 ? `${spread.toFixed(2)} price spread` : 'Captured prices'}</span>
      </div>
      {loading && <div className="odds-state"><span className="spinner" />Loading price sources…</div>}
      {!loading && failed && <div className="odds-state">Price comparison is temporarily unavailable.</div>}
      {!loading && !failed && quotes.length === 0 && <div className="odds-state">Only the immutable publication snapshot is available.</div>}
      {!loading && quotes.length > 0 && (
        <div className="odds-table" role="table" aria-label="Bookmaker odds comparison">
          <div className="odds-table-head" role="row">
            <span role="columnheader">Source</span><span role="columnheader">Opening</span><span role="columnheader">Current</span><span role="columnheader">Move</span>
          </div>
          {quotes.map(quote => (
            <div className={`odds-table-row${quote.is_best ? ' best' : ''}`} role="row" key={`${quote.bookmaker}-${quote.fetched_at}`}>
              <span role="cell"><strong>{quote.bookmaker}</strong>{quote.is_best && <small>Best</small>}{quote.is_fallback && <small>Fallback</small>}</span>
              <span role="cell">{quote.opening_odds?.toFixed(2) ?? '—'}</span>
              <strong role="cell">{quote.decimal_odds.toFixed(2)}</strong>
              <Movement value={quote.movement} />
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

function Movement({ value }: { value: number | null }) {
  if (value == null || Math.abs(value) < 0.005) return <span role="cell" className="odds-move flat">—</span>
  const drifted = value > 0
  return <span role="cell" className={`odds-move ${drifted ? 'drift' : 'shorten'}`}>{drifted ? '↑' : '↓'} {Math.abs(value).toFixed(2)}</span>
}

function ProbabilityRow({ label, value, tone }: { label: string; value: number | null; tone: string }) {
  const width = value == null ? 0 : Math.min(100, Math.max(0, value * 100))
  return (
    <div className="probability-row">
      <div><span>{label}</span><strong>{pct(value)}</strong></div>
      <div className="probability-track"><span className={tone} style={{ width: `${width}%` }} /></div>
    </div>
  )
}

function Metric({ label, value, note, hint }: { label: string; value: string; note?: string; hint?: string }) {
  return <div title={hint}><span>{label}{hint && <sup className="metric-help" aria-label={hint}>?</sup>}</span><strong>{value}</strong>{note && <small className="metric-note">{note}</small>}</div>
}
