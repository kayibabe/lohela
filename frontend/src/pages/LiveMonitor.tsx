import { useCallback, useEffect, useState } from 'react'
import { fetchMatchFixtures, formatDate, formatKickoff, type MatchFixture } from '../lib/api'
import { TODAY } from './dailyTickets/helpers'
import './LiveMonitor.css'

const REFRESH_MS = 60_000

export default function LiveMonitorPage() {
  const [fixtures, setFixtures] = useState<MatchFixture[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [lastChecked, setLastChecked] = useState<Date | null>(null)

  const refresh = useCallback(async (initial = false) => {
    if (initial) setLoading(true)
    else setRefreshing(true)
    try {
      const rows = await fetchMatchFixtures(TODAY)
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
  }, [])

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
      <div className="live-monitor-count"><strong>{fixtures.length}</strong> {fixtures.length === 1 ? 'match' : 'matches'} in play today</div>
      {loading ? <p className="live-monitor-empty" role="status">Loading live fixtures…</p> : fixtures.length === 0 ? (
        <p className="live-monitor-empty">No live fixtures are currently available. This page will check again automatically.</p>
      ) : (
        <div className="live-monitor-grid">
          {fixtures.map((fixture) => (
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
            </article>
          ))}
        </div>
      )}
      <p className="live-monitor-footnote">“View checked” is when this page last fetched its data; it is not the upstream provider’s feed timestamp.</p>
    </section>
  )
}
