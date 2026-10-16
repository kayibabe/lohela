import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchMatchFixtures, formatDate, formatKickoff, type MatchFixture } from '../lib/api'
import { useFavoriteMatches } from './dailyTickets/useFavoriteMatches'
import { TODAY } from './dailyTickets/helpers'
import './LiveMonitor.css'

const REFRESH_MS = 60_000

export default function LiveMonitorPage() {
  const [fixtures, setFixtures] = useState<MatchFixture[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [lastChecked, setLastChecked] = useState<Date | null>(null)
  const [competition, setCompetition] = useState('all')
  const [favoriteOnly, setFavoriteOnly] = useState(false)
  const [alertsEnabled, setAlertsEnabled] = useState(false)
  const [alertMessage, setAlertMessage] = useState('')
  const { favoriteIds } = useFavoriteMatches()
  const previousFixtures = useRef<Map<number, MatchFixture> | null>(null)

  const refresh = useCallback(async (initial = false) => {
    if (initial) setLoading(true)
    else setRefreshing(true)
    try {
      const rows = await fetchMatchFixtures(TODAY)
      const nextFixtures = new Map(rows.map((fixture) => [fixture.match_id, fixture]))
      const previous = previousFixtures.current
      if (alertsEnabled && previous && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        for (const fixture of rows) {
          const old = previous.get(fixture.match_id)
          if (!favoriteIds.has(fixture.match_id) || !old) continue
          const scoreChanged = old.home_goals !== fixture.home_goals || old.away_goals !== fixture.away_goals
          const statusChanged = old.status !== fixture.status
          if (!scoreChanged && !statusChanged) continue
          const score = fixture.home_goals == null || fixture.away_goals == null
            ? 'Score unavailable'
            : `${fixture.home_goals}–${fixture.away_goals}`
          const phase = fixture.live_phase?.replace(/_/g, ' ') || fixture.status
          try {
            const notification = new Notification(`${fixture.home_team} vs ${fixture.away_team}`, {
              body: `${score} · ${phase}${fixture.elapsed_minutes == null ? '' : ` · ${fixture.elapsed_minutes}′`}`,
              tag: `lohela-match-${fixture.match_id}`,
            })
            notification.onclick = () => {
              window.focus()
              window.location.href = `/match/${fixture.match_id}`
            }
          } catch {
            setAlertMessage('A score change was observed, but the browser could not show its notification.')
          }
        }
      }
      previousFixtures.current = nextFixtures
      setFixtures(rows.filter((fixture) => fixture.status === 'live').sort((a, b) =>
        a.competition.localeCompare(b.competition) || a.kickoff_at.localeCompare(b.kickoff_at),
      ))
      setError('')
      setLastChecked(new Date())
    } catch {
      setError('Could not refresh live fixtures. Showing the last data loaded, if available.')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [alertsEnabled, favoriteIds])

  const competitions = [...new Set(fixtures.map((fixture) => fixture.competition))].sort()
  const liveFavoriteCount = fixtures.filter((fixture) => favoriteIds.has(fixture.match_id)).length
  const visibleFixtures = fixtures.filter((fixture) =>
    (competition === 'all' || fixture.competition === competition)
    && (!favoriteOnly || favoriteIds.has(fixture.match_id)),
  )
  const hasFilters = competition !== 'all' || favoriteOnly

  const toggleAlerts = async () => {
    if (alertsEnabled) {
      setAlertsEnabled(false)
      setAlertMessage('Score alerts paused for this page.')
      return
    }
    if (typeof Notification === 'undefined') {
      setAlertMessage('This browser does not support desktop notifications.')
      return
    }
    if (!window.isSecureContext) {
      setAlertMessage('Browser notifications require a secure connection.')
      return
    }
    try {
      const permission = Notification.permission === 'granted'
        ? 'granted'
        : await Notification.requestPermission()
      if (permission === 'granted') {
        setAlertsEnabled(true)
        setAlertMessage('Alerts enabled for favourited matches while this page is open.')
      } else {
        setAlertMessage('Notification permission was not granted.')
      }
    } catch {
      setAlertMessage('Could not request browser notification permission.')
    }
  }

  useEffect(() => {
    void refresh(true)
    const timer = window.setInterval(() => void refresh(), REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  return (
    <section className="live-monitor-page">
      <header className="live-monitor-heading">
        <div>
          <span className="live-monitor-eyebrow"><i /> Live centre</span>
          <h1>Matches in play</h1>
          <p>Live scores and match phases from Lohela’s stored fixture status. This view checks for updates every minute.</p>
        </div>
        <div className="live-monitor-actions">
          {lastChecked && <span>View checked {lastChecked.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>}
          <button type="button" onClick={() => void refresh()} disabled={refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </header>

      {error && <p className="live-monitor-message" role="alert">{error}</p>}
      <div className="live-monitor-controls">
        <label>Competition<select value={competition} onChange={(event) => setCompetition(event.target.value)}>
          <option value="all">All competitions</option>
          {competitions.map((name) => <option key={name} value={name}>{name}</option>)}
        </select></label>
        <button type="button" className={favoriteOnly ? 'active' : ''} aria-pressed={favoriteOnly} onClick={() => setFavoriteOnly((value) => !value)} disabled={liveFavoriteCount === 0}>
          <span aria-hidden="true">★</span> Favourites in play <b>{liveFavoriteCount}</b>
        </button>
        <button type="button" className={alertsEnabled ? 'active' : ''} aria-pressed={alertsEnabled} onClick={() => void toggleAlerts()}>
          {alertsEnabled ? 'Pause score alerts' : 'Enable score alerts'}
        </button>
        {hasFilters && <button type="button" onClick={() => { setCompetition('all'); setFavoriteOnly(false) }}>Clear filters</button>}
      </div>
      {alertMessage && <p className="live-monitor-alert-message" role="status">{alertMessage}</p>}
      <div className="live-monitor-count"><strong>{visibleFixtures.length}</strong> of <strong>{fixtures.length}</strong> live matches today</div>
      {loading ? <p className="live-monitor-empty" role="status">Loading live fixtures…</p> : fixtures.length === 0 ? (
        <p className="live-monitor-empty">No live fixtures are currently available. This page will check again automatically.</p>
      ) : visibleFixtures.length === 0 ? (
        <p className="live-monitor-empty">No live matches match these filters.</p>
      ) : (
        <div className="live-monitor-grid">
          {visibleFixtures.map((fixture) => (
            <article className="live-monitor-card" key={fixture.match_id}>
              <div className="live-monitor-card-top">
                <span className="live-monitor-competition">{fixture.competition}</span>
                <div className="live-monitor-meta">
                  <span className="live-monitor-kickoff">Kickoff <time className="event-date" dateTime={fixture.kickoff_at}>{formatDate(fixture.kickoff_at)}</time> · <time className="kickoff-time" dateTime={fixture.kickoff_at}>{formatKickoff(fixture.kickoff_at)}</time></span>
                  <span className="live-monitor-phase"><i />{fixture.live_phase?.replace(/_/g, ' ') || 'Live'}{fixture.elapsed_minutes != null ? ` · ${fixture.elapsed_minutes}′` : ''}</span>
                </div>
              </div>
              <div className="live-monitor-scoreline">
                <strong>{fixture.home_team}</strong>
                <b>{fixture.home_goals ?? '—'} <span>:</span> {fixture.away_goals ?? '—'}</b>
                <strong>{fixture.away_team}</strong>
              </div>
              <a className="live-monitor-match-link" href={`/match/${fixture.match_id}`}>Open match analysis <span aria-hidden="true">→</span></a>
            </article>
          ))}
        </div>
      )}
      <p className="live-monitor-footnote">“View checked” is when this page last fetched its data; it is not the upstream provider’s feed timestamp. Score alerts run only while this page is open and use changes observed between checks.</p>
    </section>
  )
}
