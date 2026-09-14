import { useCallback, useEffect, useMemo, useState } from 'react'
import { formatMarket, formatSelection, formatTicketType } from '../lib/api'
import { fmt, fmtPnl } from '../utils/currency'
import PaperLedger from '../components/PaperLedger'
import MatchHistory from '../components/MatchHistory'
import { groupJournalBets, type Bet } from '../lib/trackerGrouping'

interface BetPeriod {
  label: string; bets: number; settled: number; wins: number; losses: number; staked: number; returned: number
  profit_loss: number; roi: number | null; hit_rate: number | null
}

interface BetSummary {
  total_bets: number; pending_bets: number; settled_bets: number; wins: number; losses: number; voids: number; cashouts: number
  staked: number; returned: number; profit_loss: number; roi: number | null; hit_rate: number | null
  by_year: BetPeriod[]; by_month: BetPeriod[]; by_date: BetPeriod[]
}

const STATUS_COLORS: Record<string, string> = { pending: 'var(--muted)', won: 'var(--positive)', lost: 'var(--negative)', void: 'var(--muted)', cashout: 'var(--accent)' }
const STATUS_BG: Record<string, string> = { pending: 'var(--panel2)', won: '#14532d20', lost: '#7f1d1d20', void: 'var(--panel2)', cashout: 'var(--accent-bg)' }

async function responseError(response: Response) {
  try { const body = await response.json(); return body.detail || body.message || `Request failed (${response.status})` } catch { return `Request failed (${response.status})` }
}

export default function TrackerPage({ onOpenTickets }: { onOpenTickets?: (date?: string) => void }) {
  const [view, setView] = useState<'paper' | 'matches' | 'manual'>('paper')
  const [bets, setBets] = useState<Bet[]>([])
  const [summary, setSummary] = useState<BetSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [settleBetId, setSettleBetId] = useState<number | null>(null)
  const [filterStatus, setFilterStatus] = useState('all')
  const [form, setForm] = useState({ label: '', odds: '', stake: '', ticket_type: '', ticket_date: '', notes: '' })
  const [settleForm, setSettleForm] = useState({ status: 'won', actual_return: '' })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit: '200' })
      if (filterStatus !== 'all') params.set('status', filterStatus)
      const [listResponse, summaryResponse] = await Promise.all([fetch(`/api/v1/bets?${params}`), fetch('/api/v1/bets/summary')])
      if (!listResponse.ok) throw new Error(await responseError(listResponse))
      if (!summaryResponse.ok) throw new Error(await responseError(summaryResponse))
      const [list, totals] = await Promise.all([listResponse.json(), summaryResponse.json()])
      setBets(list); setSummary(totals)
    } catch (reason) {
      setMessage({ tone: 'error', text: reason instanceof Error ? reason.message : 'Journal unavailable' })
    } finally { setLoading(false) }
  }, [filterStatus])

  useEffect(() => { load() }, [load])
  const groups = useMemo(() => groupJournalBets(bets), [bets])

  async function submitBet(event: React.FormEvent) {
    event.preventDefault(); setMessage(null)
    const response = await fetch('/api/v1/bets', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ label: form.label, odds: Number(form.odds), stake: Number(form.stake), ticket_type: form.ticket_type || null, ticket_date: form.ticket_date || null, notes: form.notes || null }) })
    if (!response.ok) { setMessage({ tone: 'error', text: await responseError(response) }); return }
    setForm({ label: '', odds: '', stake: '', ticket_type: '', ticket_date: '', notes: '' }); setShowForm(false)
    setMessage({ tone: 'success', text: 'Journal entry recorded.' }); await load()
  }

  async function submitSettle(event: React.FormEvent) {
    event.preventDefault(); if (settleBetId == null) return; setMessage(null)
    const response = await fetch(`/api/v1/bets/${settleBetId}/settle`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: settleForm.status, actual_return: settleForm.actual_return === '' ? null : Number(settleForm.actual_return) }) })
    if (!response.ok) { setMessage({ tone: 'error', text: await responseError(response) }); return }
    setSettleBetId(null); setSettleForm({ status: 'won', actual_return: '' }); setMessage({ tone: 'success', text: 'Journal entry settled.' }); await load()
  }

  async function deleteBet(id: number) {
    if (!confirm('Delete this journal entry? This cannot be undone.')) return
    const response = await fetch(`/api/v1/bets/${id}`, { method: 'DELETE' })
    if (!response.ok) { setMessage({ tone: 'error', text: await responseError(response) }); return }
    setMessage({ tone: 'success', text: 'Journal entry deleted.' }); await load()
  }

  const badge = view === 'paper' ? 'Production paper evidence' : view === 'matches' ? 'Published selection evidence' : 'Confirmed and manual journal'

  return <div className="page-content tracker-page">
    <div className="analytics-hero tracker-hero"><div><span className="eyebrow">Auditable portfolio</span><h1>Tracker</h1><p>Keep production paper evidence, published match outcomes and personal journal records in clearly separated views.</p></div><span className={`ledger-chip tracker-chip ${view}`}><span /> {badge}</span></div>
    <div className="analytics-tabs tracker-view-tabs" role="tablist" aria-label="Tracker views">
      <button role="tab" aria-selected={view === 'paper'} className={view === 'paper' ? 'active' : ''} onClick={() => setView('paper')}>System paper ledger</button>
      <button role="tab" aria-selected={view === 'matches'} className={view === 'matches' ? 'active' : ''} onClick={() => setView('matches')}>Match history</button>
      <button role="tab" aria-selected={view === 'manual'} className={view === 'manual' ? 'active' : ''} onClick={() => setView('manual')}>Confirmed &amp; manual journal</button>
    </div>

    {view === 'paper' ? <PaperLedger /> : view === 'matches' ? <MatchHistory /> : <>
      <div className="journal-note"><strong>Personal journal</strong><span>Confirmed selections retain system provenance; free-form entries remain clearly labelled. Neither changes the immutable system paper ledger.</span></div>
      {message && <div className={`tracker-message ${message.tone}`} role={message.tone === 'error' ? 'alert' : 'status'}>{message.text}</div>}
      <div className="journal-summary-heading"><div><span className="eyebrow">All journal records</span><strong>Portfolio totals</strong></div><span>Totals remain stable when the list is filtered.</span></div>
      <div className="stat-row journal-kpis">
        <JournalMetric label="Total bets" value={(summary?.total_bets ?? 0).toString()} note={`${summary?.pending_bets ?? 0} pending`} />
        <JournalMetric label="Hit rate" value={summary?.hit_rate == null ? '—' : `${(summary.hit_rate * 100).toFixed(1)}%`} note={`${summary?.wins ?? 0} won · ${summary?.losses ?? 0} lost`} />
        <JournalMetric label="Settled stake" value={fmt(summary?.staked ?? 0)} note={`${summary?.settled_bets ?? 0} financially settled`} />
        <JournalMetric label="Actual return" value={fmt(summary?.returned ?? 0)} note="Not potential return" />
        <JournalMetric label="P&L" value={fmtPnl(summary?.profit_loss ?? 0)} note="Confirmed and manual records" tone={(summary?.profit_loss ?? 0) >= 0 ? 'positive' : 'negative'} />
        <JournalMetric label="ROI" value={summary?.roi == null ? '—' : `${(summary.roi * 100).toFixed(1)}%`} note="Actual settled returns" tone={(summary?.profit_loss ?? 0) >= 0 ? 'positive' : 'negative'} />
      </div>

      {summary && <div className="journal-periods"><PeriodColumn title="Yearly" rows={summary.by_year.slice(0, 4)} /><PeriodColumn title="Monthly" rows={summary.by_month.slice(0, 6)} /><PeriodColumn title="Daily" rows={summary.by_date.slice(0, 7)} /></div>}

      <div className="tracker-toolbar"><div className="filter-tabs journal-filter-tabs" aria-label="Journal status filter">{['all', 'pending', 'won', 'lost', 'void', 'cashout'].map(status => <button key={status} className={`filter-tab${filterStatus === status ? ' active' : ''}`} onClick={() => setFilterStatus(status)}>{status === 'cashout' ? 'Cash out' : status.charAt(0).toUpperCase() + status.slice(1)}</button>)}</div><button className="btn-primary" onClick={() => setShowForm(value => !value)}>{showForm ? 'Cancel entry' : '+ Log manual bet'}</button></div>

      {showForm && <form className="bet-form" onSubmit={submitBet}>
        <div className="form-row"><div className="form-group" style={{ flex: 3 }}><label>Label</label><input required value={form.label} onChange={event => setForm(value => ({ ...value, label: event.target.value }))} placeholder="Teams or a short description" /></div><div className="form-group"><label>Decimal odds</label><input required type="number" step="0.01" min="1.01" value={form.odds} onChange={event => setForm(value => ({ ...value, odds: event.target.value }))} placeholder="1.85" /></div><div className="form-group"><label>Stake</label><input required type="number" step="0.01" min="0.01" value={form.stake} onChange={event => setForm(value => ({ ...value, stake: event.target.value }))} placeholder="10.00" /></div></div>
        <div className="form-row"><div className="form-group"><label>Entry type</label><select value={form.ticket_type} onChange={event => setForm(value => ({ ...value, ticket_type: event.target.value }))}><option value="">Uncategorised</option><option value="safe">Conservative</option><option value="balanced">Balanced</option><option value="aggressive">Aggressive</option><option value="best_value">Best Value</option><option value="custom">Custom</option></select></div><div className="form-group"><label>Fixture date</label><input type="date" value={form.ticket_date} onChange={event => setForm(value => ({ ...value, ticket_date: event.target.value }))} /></div><div className="form-group" style={{ flex: 2 }}><label>Notes</label><input value={form.notes} onChange={event => setForm(value => ({ ...value, notes: event.target.value }))} placeholder="Optional rationale or bookmaker reference" /></div></div>
        {form.odds && form.stake && <div className="form-preview">Potential return: <strong>{fmt(Number(form.odds) * Number(form.stake))}</strong></div>}
        <div className="form-actions"><button type="submit" className="btn-primary">Record entry</button><button type="button" className="btn-ghost" onClick={() => setShowForm(false)}>Cancel</button></div>
      </form>}

      {settleBetId !== null && <div className="modal-overlay" onClick={() => setSettleBetId(null)}><form className="modal" role="dialog" aria-modal="true" aria-labelledby="settle-title" onClick={event => event.stopPropagation()} onSubmit={submitSettle}><h3 className="modal-title" id="settle-title">Settle journal entry</h3><div className="form-group"><label>Outcome</label><select value={settleForm.status} onChange={event => setSettleForm(value => ({ ...value, status: event.target.value, actual_return: event.target.value === 'cashout' ? value.actual_return : '' }))}><option value="won">Won</option><option value="lost">Lost</option><option value="void">Void</option><option value="cashout">Cash out</option></select></div><div className="form-group"><label>Actual return {settleForm.status === 'cashout' ? '(required)' : '(optional override)'}</label><input required={settleForm.status === 'cashout'} type="number" step="0.01" min="0" value={settleForm.actual_return} onChange={event => setSettleForm(value => ({ ...value, actual_return: event.target.value }))} placeholder={settleForm.status === 'cashout' ? 'Enter cash-out amount' : 'Blank uses the standard outcome return'} /></div><div className="form-actions"><button type="submit" className="btn-primary">Confirm settlement</button><button type="button" className="btn-ghost" onClick={() => setSettleBetId(null)}>Cancel</button></div></form></div>}

      {loading ? <div className="empty-state" aria-busy="true">Loading journal…</div> : !bets.length ? <div className="empty-state">{summary?.total_bets ? 'No journal entries match this status filter.' : <>No journal entries yet — use <strong>+ Log manual bet</strong> or confirm a published selection.</>}</div> : <>
        {bets.length === 200 && <div className="tracker-message warning">Showing the latest 200 matching entries. Portfolio totals and period summaries still cover the complete journal.</div>}
        <div className="journal-history">{groups.map(year => <section className="journal-year-group" key={year.year}><h2 className="paper-year-heading">{year.year}</h2>{year.months.map(month => <section key={month.month}><h3 className="paper-month-heading">{month.label}</h3>{month.dates.map(date => <section className="journal-date-group" key={date.date}><div className="paper-date-heading"><strong>{date.label}</strong><span>{date.rows.length} entr{date.rows.length === 1 ? 'y' : 'ies'}</span></div><div className="bet-list">{date.rows.map(bet => <div key={bet.id} className="bet-row" style={{ borderLeft: `3px solid ${STATUS_COLORS[bet.status] ?? 'var(--muted)'}`, background: STATUS_BG[bet.status] ?? 'var(--surface)' }}>
          <div className="bet-main"><div className="bet-origin"><span className={`origin-badge ${bet.source_selection_id ? 'confirmed' : 'manual'}`}>{bet.source_selection_id ? 'Confirmed selection' : 'Manual entry'}</span>{bet.ticket_type && <span className="badge badge-type">{formatTicketType(bet.ticket_type)}</span>}</div><span className="bet-label">{bet.label}</span>{bet.source_selection_id && <div className="bet-provenance"><span>{bet.market ? formatMarket(bet.market) : 'Published market'}</span><strong>{bet.selection ? formatSelection(bet.selection) : 'Published selection'}</strong><code>source #{bet.source_selection_id}</code></div>}{bet.notes && <p className="bet-notes">{bet.notes}</p>}</div>
          <div className="bet-financials"><div className="fin-item"><span className="fin-label">Odds</span><span className="fin-value">{bet.odds.toFixed(2)}</span></div><div className="fin-item"><span className="fin-label">Stake</span><span className="fin-value">{fmt(bet.stake)}</span></div><div className="fin-item"><span className="fin-label">{bet.status === 'pending' ? 'Potential' : 'Actual return'}</span><span className="fin-value">{bet.status === 'pending' ? fmt(bet.potential_return) : bet.actual_return == null ? '—' : fmt(bet.actual_return)}</span></div>{bet.profit_loss !== null && <div className="fin-item"><span className="fin-label">P&amp;L</span><span className={`fin-value ${bet.profit_loss >= 0 ? 'positive' : 'negative'}`}>{fmtPnl(bet.profit_loss)}</span></div>}</div>
          <div className="bet-actions"><span className="status-badge" style={{ color: STATUS_COLORS[bet.status] }}>{bet.status.toUpperCase()}</span>{bet.source_selection_id && bet.ticket_date && onOpenTickets && <button className="btn-sm btn-ghost" onClick={() => onOpenTickets(bet.ticket_date ?? undefined)}>View source date</button>}{bet.status === 'pending' && <button className="btn-sm" onClick={() => { setSettleBetId(bet.id); setSettleForm({ status: 'won', actual_return: '' }) }}>Settle</button>}<button className="btn-sm btn-danger" aria-label={`Delete ${bet.label}`} title="Delete journal entry" onClick={() => deleteBet(bet.id)}>Delete</button></div>
        </div>)}</div></section>)}</section>)}</section>)}</div>
      </>}
    </>}
  </div>
}

function JournalMetric({ label, value, note, tone }: { label: string; value: string; note: string; tone?: 'positive' | 'negative' }) { return <div className={`stat-card${tone ? ` ${tone}` : ''}`}><div className="kpi-label">{label}</div><div className="kpi-value">{value}</div><div className="kpi-note">{note}</div></div> }
function PeriodColumn({ title, rows }: { title: string; rows: BetPeriod[] }) { return <section><h3>{title}</h3>{rows.length ? rows.map(row => <div className="journal-period-row" key={row.label}><span><strong>{row.label}</strong><small>{row.settled}/{row.bets} settled</small></span><span><strong className={row.profit_loss >= 0 ? 'positive' : 'negative'}>{fmtPnl(row.profit_loss)}</strong><small>{row.roi == null ? 'ROI —' : `${(row.roi * 100).toFixed(1)}% ROI`}</small></span></div>) : <p>No settled periods yet.</p>}</section> }
