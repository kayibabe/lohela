import { useState, useEffect, useCallback } from 'react'
import { localDateString } from './DailyTickets'
import { formatStage } from '../lib/api'

interface SystemStats {
  matches: number
  predictions: number
  competitions: number
  teams: number
  bets: number
}

interface CacheStats {
  fixtures: number
  odds: number
  stats: number
  injuries: number
  total: number
  error?: string
}

interface PipelineSchedule {
  schedules: { name: string; hour_utc: number; minute_utc: number; time_cat: string }[]
  jobs: { id: string; next_run_utc: string | null }[]
  automation?: { startup_catchup_enabled: boolean; watchdog_interval_minutes: number; automatic_retry_limit: number; settlement_interval_minutes: number; settlement_startup_lookback_days: number; scheduler_leader_lock_enabled: boolean; scheduler_leader_lock_name: string; latest_due_window: string | null; latest_due_target_date: string | null }
}
interface PipelineStatus { id: number; target_date: string; run_type: string; trigger_source: string | null; scheduled_window: string | null; status: string; current_stage: string | null; error_details: string | null; started_at: string | null; completed_at: string | null; stages: { name: string; status: string; retry_count: number; input_count: number; output_count: number; error_details: string | null }[] }
interface AutomationAlert { id: number; severity: string; task_name: string; target_date: string | null; title: string; detail: string; occurrence_count: number; resolved: boolean; last_seen_at: string }
interface OperationalStatus {
  observed_at: string
  latest_run: { id: number; target_date: string; run_type: string; status: string; current_stage: string | null; completed_at: string | null } | null
  latest_due_window: { name: string; target_date: string; needs_catchup: boolean; reason: string } | null
  upcoming_stale_odds_predictions: number
  finished_match_refresh_lag: number
  active_alerts: number
}
interface MarketPolicyShadowTicket {
  ticket_type: string
  legs: { prediction_id: number; match_id: number; market: string; selection: string; odds: number; source_odds_at: string | null }[]
  combined_odds: number
  combined_probability: number
}
interface MarketPolicyShadow {
  target_date: string
  status: 'captured' | 'not_captured'
  mode: 'shadow_only'
  control_unchanged: boolean
  policy_version?: string
  excluded_markets?: string[]
  captured_at?: string
  qualified_pool?: number
  public_ticket_count?: number
  missing_public_ticket_types?: string[]
  tickets?: (MarketPolicyShadowTicket | null)[]
}
interface UserRecord { id: number; username: string; email: string; role: 'user' | 'admin'; plan: 'free' | 'pro'; account_status: 'active' | 'suspended' | 'pending'; created_at: string | null; last_login_at: string | null }
type UserForm = Omit<UserRecord, 'id' | 'created_at' | 'last_login_at'> & { password: string }
const emptyUserForm: UserForm = { username: '', email: '', password: '', role: 'user', plan: 'free', account_status: 'active' }

function fmt2(n: number) { return String(n).padStart(2, '0') }

async function fetchJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const response = await fetch(url)
    return response.ok ? await response.json() : fallback
  } catch {
    return fallback
  }
}

const JOB_LABELS: Record<string, string> = {
  daily_pipeline_early: 'Next early pipeline',
  daily_pipeline_morning: 'Next morning pipeline',
  paper_ticket_settlement: 'Next settlement',
  live_match_status_refresh: 'Next live refresh',
  pipeline_automation_watchdog: 'Next automation check',
  weekly_shadow_model_learning: 'Next shadow learning',
}

export default function AdminPage() {
  const [sysStats, setSysStats] = useState<SystemStats | null>(null)
  const [cacheStats, setCacheStats] = useState<CacheStats | null>(null)
  const [schedule, setSchedule] = useState<PipelineSchedule | null>(null)
  const [loading, setLoading] = useState(true)
  const [pipelineDate, setPipelineDate] = useState(localDateString())
  const [triggering, setTriggering] = useState(false)
  const [triggerMsg, setTriggerMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [clearingCache, setClearingCache] = useState(false)
  const [pipelineRuns, setPipelineRuns] = useState<PipelineStatus[]>([])
  const [alerts, setAlerts] = useState<AutomationAlert[]>([])
  const [operational, setOperational] = useState<OperationalStatus | null>(null)
  const [marketPolicyShadow, setMarketPolicyShadow] = useState<MarketPolicyShadow | null>(null)
  const [refreshingSafety, setRefreshingSafety] = useState(false)
  const [historyStart, setHistoryStart] = useState(() => {
    const d = new Date(); d.setDate(d.getDate() - 7); return d.toISOString().slice(0, 10)
  })
  const [historyEnd, setHistoryEnd] = useState(localDateString())
  const [syncingHistory, setSyncingHistory] = useState(false)
  const [users, setUsers] = useState<UserRecord[]>([])
  const [userForm, setUserForm] = useState<UserForm>(emptyUserForm)
  const [editingUserId, setEditingUserId] = useState<number | null>(null)
  const [userBusy, setUserBusy] = useState(false)
  const [userError, setUserError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    const [sys, cache, sched, status, alertData, ops, shadow, userData] = await Promise.all([
      fetchJson<SystemStats | null>('/api/v1/admin/system/stats', null),
      fetchJson<CacheStats | null>('/api/v1/admin/cache/stats', null),
      fetchJson<PipelineSchedule | null>('/api/v1/admin/pipeline/schedule', null),
      fetchJson<{ runs: PipelineStatus[] }>('/api/v1/admin/pipeline/status', { runs: [] }),
      fetchJson<{ alerts: AutomationAlert[] }>('/api/v1/admin/alerts', { alerts: [] }),
      fetchJson<OperationalStatus | null>('/api/v1/admin/operational-status', null),
      fetchJson<MarketPolicyShadow | null>(`/api/v1/selections/market-policy-shadow?date=${localDateString()}`, null),
      fetchJson<{ users: UserRecord[] }>('/api/v1/admin/users', { users: [] }),
    ])
    setSysStats(sys)
    setCacheStats(cache)
    setSchedule(sched)
    setPipelineRuns(status.runs)
    setAlerts(alertData.alerts)
    setOperational(ops)
    setMarketPolicyShadow(shadow)
    setUsers(userData.users)
    setLoading(false)
  }, [])

  function updateUserField<K extends keyof UserForm>(field: K, value: UserForm[K]) {
    setUserForm(current => ({ ...current, [field]: value }))
  }

  function editUser(user: UserRecord) {
    setEditingUserId(user.id)
    setUserForm({ username: user.username, email: user.email, password: '', role: user.role, plan: user.plan, account_status: user.account_status })
    setUserError(null)
  }

  async function saveUser(event: React.FormEvent) {
    event.preventDefault(); setUserBusy(true); setUserError(null)
    const body = { ...userForm, ...(editingUserId && !userForm.password ? { password: undefined } : {}) }
    if (editingUserId && !userForm.password) delete (body as { password?: string }).password
    try {
      const res = await fetch(editingUserId ? `/api/v1/admin/users/${editingUserId}` : '/api/v1/admin/users', { method: editingUserId ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) { setUserError(data.detail ?? 'Could not save user'); return }
      setUserForm(emptyUserForm); setEditingUserId(null); await load()
    } finally { setUserBusy(false) }
  }

  async function removeUser(user: UserRecord) {
    if (!window.confirm(`Delete ${user.username}?`)) return
    const res = await fetch(`/api/v1/admin/users/${user.id}`, { method: 'DELETE' })
    if (!res.ok) { const data = await res.json().catch(() => ({})); setUserError(data.detail ?? 'Could not delete user'); return }
    if (editingUserId === user.id) { setEditingUserId(null); setUserForm(emptyUserForm) }
    await load()
  }

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const timer = window.setInterval(() => { fetch('/api/v1/admin/pipeline/status').then(r => r.ok ? r.json() : { runs: [] }).then(data => setPipelineRuns(data.runs ?? [])) }, 10000)
    return () => window.clearInterval(timer)
  }, [])

  async function resolveAlert(id: number) {
    const res = await fetch(`/api/v1/admin/alerts/${id}/resolve`, { method: 'POST' })
    if (res.ok) setAlerts(current => current.filter(alert => alert.id !== id))
  }

  async function refreshSafetyStatus() {
    setRefreshingSafety(true)
    try {
      const [ops, alertData] = await Promise.all([
        fetchJson<OperationalStatus | null>('/api/v1/admin/operational-status', null),
        fetchJson<{ alerts: AutomationAlert[] }>('/api/v1/admin/alerts', { alerts: [] }),
      ])
      setOperational(ops)
      setAlerts(alertData.alerts)
    } finally {
      setRefreshingSafety(false)
    }
  }

  async function triggerPipeline() {
    setTriggering(true)
    setTriggerMsg(null)
    try {
      const res = await fetch(`/api/v1/admin/pipeline/trigger?target_date=${pipelineDate}`, { method: 'POST' })
      const data = await res.json()
      setTriggerMsg({ ok: res.ok, text: res.ok ? `Pipeline queued for ${data.target_date}` : data.detail ?? 'Error' })
    } catch (e) {
      setTriggerMsg({ ok: false, text: 'Failed to reach server' })
    } finally {
      setTriggering(false)
    }
  }

  async function clearCache(prefix?: string) {
    setClearingCache(true)
    try {
      const url = prefix ? `/api/v1/admin/cache/clear?prefix=${prefix}` : '/api/v1/admin/cache/clear'
      const res = await fetch(url, { method: 'DELETE' })
      const data = await res.json()
      setTriggerMsg({ ok: res.ok, text: `Cleared ${data.deleted_keys} cache keys` })
      await load()
    } finally {
      setClearingCache(false)
    }
  }

  async function syncHistory() {
    setSyncingHistory(true); setTriggerMsg(null)
    try {
      const res = await fetch('/api/v1/admin/historical/sync', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ period_start: historyStart, period_end: historyEnd }),
      })
      const data = await res.json()
      setTriggerMsg({ ok: res.ok, text: res.ok ? `Historical sync queued for ${data.period_start} to ${data.period_end}` : data.detail ?? 'Error' })
    } catch { setTriggerMsg({ ok: false, text: 'Failed to reach server' }) }
    finally { setSyncingHistory(false) }
  }

  return (
    <div className="page-content">
      <div className="page-intro admin-page-intro">
        <div><span className="eyebrow">System area</span><h1>Administration</h1><p>Monitor publication, recover missed runs, and manage access.</p></div>
        <span className="ledger-chip"><span /> Restricted</span>
      </div>

      {triggerMsg && (
        <div className={`alert ${triggerMsg.ok ? 'alert-ok' : 'alert-error'}`}>
          {triggerMsg.text}
          <button className="alert-close" onClick={() => setTriggerMsg(null)}>×</button>
        </div>
      )}

      <div className="admin-grid">

        <div className="admin-card">
          <h3 className="admin-card-title">Operational safety</h3>
          <div className="admin-action-row admin-safety-actions">
            <p className="admin-hint">Read-only health signals. Investigate before any manual intervention.</p>
            <button className="btn-ghost btn-sm" onClick={refreshSafetyStatus} disabled={refreshingSafety}>{refreshingSafety ? 'Refreshing…' : 'Refresh'}</button>
          </div>
          {operational ? (
            <div className="admin-info-list">
              <div className="admin-info-row"><span className="admin-info-label">Observed</span><span className="admin-info-value">{new Date(operational.observed_at).toLocaleString()}</span></div>
              <div className="admin-info-row"><span className="admin-info-label">Latest pipeline</span><span className={`admin-info-value admin-run-status ${operational.latest_run?.status === 'failed' ? 'negative' : ''}`}>{operational.latest_run ? <><span className={`status-badge status-${operational.latest_run.status}`}>{formatStage(operational.latest_run.status)}</span> · {operational.latest_run.target_date}{operational.latest_run.current_stage ? ` · ${formatStage(operational.latest_run.current_stage)}` : ''}</> : 'No run recorded'}</span></div>
              <div className="admin-info-row"><span className="admin-info-label">Scheduled catch-up</span><span className={`admin-info-value ${operational.latest_due_window?.needs_catchup ? 'negative' : ''}`}>{operational.latest_due_window ? operational.latest_due_window.needs_catchup ? `Required · ${operational.latest_due_window.reason}` : 'Current' : 'No window due'}</span></div>
              <div className="admin-info-row"><span className="admin-info-label">Upcoming price freshness</span><span className={`admin-info-value ${operational.upcoming_stale_odds_predictions ? 'negative' : ''}`}>{operational.upcoming_stale_odds_predictions ? `${operational.upcoming_stale_odds_predictions} stale or missing` : 'Current'}</span></div>
              <div className="admin-info-row"><span className="admin-info-label">Finished-result refresh</span><span className={`admin-info-value ${operational.finished_match_refresh_lag ? 'negative' : ''}`}>{operational.finished_match_refresh_lag ? `${operational.finished_match_refresh_lag} delayed` : 'Current'}</span></div>
              <div className="admin-info-row"><span className="admin-info-label">Active alerts</span><span className={`admin-info-value ${operational.active_alerts ? 'negative' : ''}`}>{operational.active_alerts || 'None'}</span></div>
            </div>
          ) : <div className="admin-loading">Could not load operational status</div>}
        </div>

        <div className="admin-card">
          <h3 className="admin-card-title">Market policy shadow</h3>
          <p className="admin-hint">Shadow only · the control portfolio remains unchanged.</p>
          {!marketPolicyShadow ? <div className="admin-loading">Could not load the frozen comparison</div> : marketPolicyShadow.status === 'not_captured' ? (
            <div className="admin-loading">No frozen comparison captured for {marketPolicyShadow.target_date}.</div>
          ) : (
            <>
              <div className="admin-info-list">
                <div className="admin-info-row"><span className="admin-info-label">Policy</span><span className="admin-info-value">{marketPolicyShadow.policy_version}</span></div>
                <div className="admin-info-row"><span className="admin-info-label">Excluded markets</span><span className="admin-info-value">{marketPolicyShadow.excluded_markets?.join(', ') || 'None'}</span></div>
                <div className="admin-info-row"><span className="admin-info-label">Qualified pool</span><span className="admin-info-value admin-count">{marketPolicyShadow.qualified_pool ?? '—'}</span></div>
                <div className="admin-info-row"><span className="admin-info-label">Comparable public tiers</span><span className="admin-info-value">{marketPolicyShadow.public_ticket_count ?? 0} / 3</span></div>
                {marketPolicyShadow.missing_public_ticket_types?.length ? <div className="admin-info-row"><span className="admin-info-label">Missing tiers</span><span className="admin-info-value">{marketPolicyShadow.missing_public_ticket_types.join(', ')}</span></div> : null}
              </div>
              <details className="admin-shadow-details">
                <summary>Inspect frozen counterfactual tickets</summary>
                {(marketPolicyShadow.tickets ?? []).filter((ticket): ticket is MarketPolicyShadowTicket => ticket !== null).map(ticket => (
                  <div className="admin-shadow-ticket" key={ticket.ticket_type}>
                    <strong>{formatStage(ticket.ticket_type)}</strong><span>{ticket.legs.length} legs · {ticket.combined_odds.toFixed(2)} odds · {(ticket.combined_probability * 100).toFixed(1)}% model probability</span>
                    <small>{ticket.legs.map(leg => `${formatStage(leg.market)}: ${leg.selection} (${leg.odds.toFixed(2)})`).join(' · ')}</small>
                  </div>
                ))}
              </details>
            </>
          )}
        </div>

        {/* Pipeline */}
        <div className="admin-card">
          <h3 className="admin-card-title">Daily Pipeline</h3>
          {schedule && (
            <div className="admin-info-list">
              <div className="admin-info-row">
                <span className="admin-info-label">Schedule</span>
                <span className="admin-info-value">
                  {schedule.schedules.map((item, index) => <span key={`${item.hour_utc}-${item.minute_utc}`}>{index > 0 && ' · '}{item.time_cat} CAT ({fmt2(item.hour_utc)}:{fmt2(item.minute_utc)} UTC)</span>)} daily
                </span>
              </div>
              {schedule.jobs.map(j => (
                <div key={j.id} className="admin-info-row">
                  <span className="admin-info-label">
                    {JOB_LABELS[j.id] ?? formatStage(j.id)}
                  </span>
                  <span className="admin-info-value">
                    {j.next_run_utc ? new Date(j.next_run_utc).toLocaleString() : '—'}
                  </span>
                </div>
              ))}
              {schedule.automation && (
                <>
                  <div className="admin-info-row"><span className="admin-info-label">Missed-run recovery</span><span className="admin-info-value">{schedule.automation.startup_catchup_enabled ? `Automatic · every ${schedule.automation.watchdog_interval_minutes} min` : 'Disabled'}</span></div>
                  <div className="admin-info-row"><span className="admin-info-label">Result settlement</span><span className="admin-info-value">Automatic · every {schedule.automation.settlement_interval_minutes} min · {schedule.automation.settlement_startup_lookback_days}-day startup catch-up</span></div>
                  <div className="admin-info-row"><span className="admin-info-label">Scheduler ownership</span><span className="admin-info-value">{schedule.automation.scheduler_leader_lock_enabled ? 'PostgreSQL leader lock enabled' : 'Disabled'}</span></div>
                </>
              )}
            </div>
          )}
          <div className="admin-action-row" style={{ marginTop: 16 }}>
            <input
              type="date"
              className="date-input"
              value={pipelineDate}
              onChange={e => setPipelineDate(e.target.value)}
            />
            <button className="btn-primary" onClick={triggerPipeline} disabled={triggering} title={`Run the pipeline for ${pipelineDate}`}>
              {triggering ? 'Queuing…' : pipelineDate === localDateString() ? '▶ Run Today’s Pipeline' : '▶ Run Pipeline'}
            </button>
          </div>
          <p className="admin-hint">
            Runs: ingest fixtures → fetch odds → enrich form data → prediction models
          </p>
          <div className="admin-action-row" style={{ marginTop: 12, flexWrap: 'wrap' }}>
            <input type="date" className="date-input" value={historyStart} onChange={e => setHistoryStart(e.target.value)} aria-label="Historical sync start date" />
            <span className="admin-hint">to</span>
            <input type="date" className="date-input" value={historyEnd} onChange={e => setHistoryEnd(e.target.value)} aria-label="Historical sync end date" />
            <button className="btn-ghost" onClick={syncHistory} disabled={syncingHistory} title="Ingest finished fixtures and settle tickets in this range">
              {syncingHistory ? 'Queuing…' : '↻ Sync Historical Results'}
            </button>
          </div>
          <p className="admin-hint">Use this for prior dates: it refreshes finished scores and settles matching paper tickets.</p>
          <div className="admin-info-list" style={{ marginTop: 16 }}>
            <div className="admin-info-row"><span className="admin-info-label">Recent runs</span><span className="admin-info-value">{pipelineRuns.length}</span></div>
            {pipelineRuns.slice(0, 3).map(run => {
              const published = run.stages.find(stage => stage.name === 'publication')?.output_count ?? 0
              return <div className="admin-info-row" key={run.id}><span className="admin-info-label">#{run.id} · {run.target_date} · {formatStage(run.run_type)}</span><span className={`admin-info-value admin-run-status ${run.status === 'failed' ? 'negative' : ''}`}><span className={`status-badge status-${run.status}`}>{formatStage(run.status)}</span>{run.trigger_source ? ` · ${formatStage(run.trigger_source)}` : ''}{run.scheduled_window ? ` · ${formatStage(run.scheduled_window)} window` : ''}{published ? ` · ${published} published` : ''}{run.current_stage ? ` · ${formatStage(run.current_stage)}` : ''}{run.error_details ? ` · ${run.error_details}` : ''}</span></div>
            })}
          </div>
        </div>

        {/* Automation alerts */}
        <div className="admin-card">
          <h3 className="admin-card-title">Automation alerts</h3>
          <p className="admin-hint">Persistent provider failures, exhausted retries, and decision-safety signals.</p>
          {alerts.length === 0 ? (
            <div className="admin-loading">No active automation alerts</div>
          ) : (
            <div className="admin-info-list">
              {alerts.map(alert => (
                <div className="admin-info-row" key={alert.id}>
                  <span className="admin-info-label">{alert.title}</span>
                  <span className="admin-info-value admin-run-status negative">
                    {alert.detail} · {alert.occurrence_count} occurrence{alert.occurrence_count === 1 ? '' : 's'}
                    <button className="btn-ghost btn-sm" style={{ marginLeft: 8 }} onClick={() => resolveAlert(alert.id)}>Resolve</button>
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* System stats */}
        <div className="admin-card admin-users-card">
          <h3 className="admin-card-title">User access</h3>
          <p className="admin-hint">Create, update, suspend, or remove accounts. Passwords are write-only and never displayed.</p>
          <form className="admin-user-form" onSubmit={saveUser}>
            <input placeholder="Username" value={userForm.username} onChange={e => updateUserField('username', e.target.value)} required minLength={2} />
            <input type="email" placeholder="Email" value={userForm.email} onChange={e => updateUserField('email', e.target.value)} required />
            <input type="password" placeholder={editingUserId ? 'New password (optional)' : 'Password (6+ characters)'} value={userForm.password} onChange={e => updateUserField('password', e.target.value)} minLength={6} required={!editingUserId} />
            <select value={userForm.role} onChange={e => updateUserField('role', e.target.value as UserForm['role'])}><option value="user">User</option><option value="admin">Admin</option></select>
            <select value={userForm.plan} onChange={e => updateUserField('plan', e.target.value as UserForm['plan'])}><option value="free">Free</option><option value="pro">Pro</option></select>
            <select value={userForm.account_status} onChange={e => updateUserField('account_status', e.target.value as UserForm['account_status'])}><option value="active">Active</option><option value="pending">Pending</option><option value="suspended">Suspended</option></select>
            <button className="btn-primary" type="submit" disabled={userBusy}>{userBusy ? 'Saving…' : editingUserId ? 'Save changes' : 'Create user'}</button>
            {editingUserId && <button className="btn-ghost" type="button" onClick={() => { setEditingUserId(null); setUserForm(emptyUserForm) }}>Cancel</button>}
          </form>
          {userError && <div className="auth-error" role="alert">{userError}</div>}
          <div className="admin-user-list">
            {users.map(user => <div className="admin-user-row" key={user.id}><div><strong>{user.username}</strong><span>{user.email}</span></div><span>{user.role} · {user.plan} · {user.account_status}</span><div><button className="btn-ghost btn-sm" onClick={() => editUser(user)}>Edit</button><button className="btn-danger-outline btn-sm" onClick={() => removeUser(user)}>Delete</button></div></div>)}
            {users.length === 0 && <div className="admin-loading">No users found</div>}
          </div>
        </div>

        {/* System stats */}
        <div className="admin-card">
          <h3 className="admin-card-title">Database</h3>
          {loading ? (
            <div className="admin-loading">Loading…</div>
          ) : sysStats ? (
            <div className="admin-info-list">
              {Object.entries(sysStats).map(([k, v]) => (
                <div key={k} className="admin-info-row">
                  <span className="admin-info-label">{k.charAt(0).toUpperCase() + k.slice(1)}</span>
                  <span className="admin-info-value admin-count">{(v as number).toLocaleString()}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="admin-loading">Could not load stats</div>
          )}
        </div>

        {/* Cache */}
        <div className="admin-card">
          <h3 className="admin-card-title">API Cache</h3>
          <p className="admin-hint">Redis-backed — preserves API quota across pipeline runs.</p>
          {loading ? (
            <div className="admin-loading">Loading…</div>
          ) : cacheStats ? (
            <>
              <div className="admin-info-list">
                {Object.entries(cacheStats).filter(([k]) => k !== 'error').map(([k, v]) => (
                  <div key={k} className="admin-info-row">
                    <span className="admin-info-label">{k}</span>
                    <span className="admin-info-value admin-count">{v as number} keys</span>
                  </div>
                ))}
              </div>
              <div className="admin-action-row" style={{ marginTop: 16, flexWrap: 'wrap', gap: 8 }}>
                {['fixtures', 'odds', 'stats', 'injuries'].map(p => (
                  <button key={p} className="btn-ghost btn-sm" onClick={() => clearCache(p)} disabled={clearingCache}>
                    Clear {p}
                  </button>
                ))}
                <button className="btn-danger-outline btn-sm" onClick={() => clearCache()} disabled={clearingCache}>
                  Clear All
                </button>
              </div>
            </>
          ) : (
            <div className="admin-loading">Could not load cache stats</div>
          )}
        </div>

        {/* API info */}
        <div className="admin-card">
          <h3 className="admin-card-title">Pipeline Stages</h3>
          <div className="stage-list">
            {[
              { n: 1, name: 'Fixture Ingestion', desc: 'API-Football → Match rows for all Tier 1 leagues', ttl: '6 h cache' },
              { n: 2, name: 'Live Odds',          desc: 'API-Football → Odds rows (bookmaker prices)',       ttl: '30 min cache' },
              { n: 3, name: 'Data Enrichment',    desc: 'Rolling form strings, xG averages',                ttl: 'DB only' },
              { n: 4, name: 'Model Run',          desc: 'Poisson + ZINB + Elo + xG + Bayesian → ensemble',  ttl: 'DB only' },
            ].map(s => (
              <div key={s.n} className="stage-row">
                <span className="stage-num">{s.n}</span>
                <div>
                  <div className="stage-name">{s.name} <span className="stage-ttl">{s.ttl}</span></div>
                  <div className="stage-desc">{s.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

      </div>
    </div>
  )
}
