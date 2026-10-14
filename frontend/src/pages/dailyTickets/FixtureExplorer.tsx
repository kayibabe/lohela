import { useEffect, useMemo, useState } from 'react'
import { formatKickoff, type MatchFixture } from '../../lib/api'

type FixtureStatus = MatchFixture['status'] | 'all'

export function FixtureExplorer({ fixtures }: { fixtures: MatchFixture[] }) {
  const [query, setQuery] = useState('')
  const [league, setLeague] = useState('all')
  const [status, setStatus] = useState<FixtureStatus>('all')
  const [favoriteOnly, setFavoriteOnly] = useState(false)
  const [favorites, setFavorites] = useState<number[]>(() => {
    try {
      const saved = localStorage.getItem('lohela.match-favorites.v1')
      const parsed: unknown = saved ? JSON.parse(saved) : []
      return Array.isArray(parsed) ? parsed.filter((id): id is number => Number.isInteger(id)) : []
    } catch {
      return []
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem('lohela.match-favorites.v1', JSON.stringify(favorites))
    } catch {
      // Keep favorites usable in this view if browser storage is unavailable.
    }
  }, [favorites])
  const leagues = useMemo(() => [...new Set(fixtures.map(fixture => fixture.competition))].sort(), [fixtures])
  const favoriteIds = useMemo(() => new Set(favorites), [favorites])
  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase()
    return fixtures.filter(fixture => {
      const matchesQuery = !normalized || `${fixture.home_team} ${fixture.away_team} ${fixture.competition}`.toLocaleLowerCase().includes(normalized)
      return matchesQuery && (league === 'all' || fixture.competition === league) && (status === 'all' || fixture.status === status) && (!favoriteOnly || favoriteIds.has(fixture.match_id))
    })
  }, [favoriteIds, favoriteOnly, fixtures, league, query, status])

  function toggleFavorite(matchId: number) {
    setFavorites(current => current.includes(matchId) ? current.filter(id => id !== matchId) : [...current, matchId])
  }

  return (
    <section className="selection-panel fixture-explorer" aria-labelledby="fixture-explorer-title">
      <div className="section-heading">
        <div>
          <h2 id="fixture-explorer-title">Match Explorer</h2>
          <span className="section-note">Browse fixtures, competitions, kickoff times and match status.</span>
        </div>
        <span className="section-note"><b>{filtered.length}</b> of {fixtures.length} matches</span>
      </div>
      <div className="match-toolbar fixture-toolbar">
        <div className="match-explorer-controls">
          <label className="match-search"><span className="sr-only">Search teams or competition</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Team, league or country" /></label>
          <label><span className="sr-only">Filter by competition</span><select value={league} onChange={event => setLeague(event.target.value)}><option value="all">All competitions</option>{leagues.map(item => <option key={item} value={item}>{item}</option>)}</select></label>
        </div>
        <div className="match-status-filters" aria-label="Filter fixtures by status">
          {(['all', 'live', 'scheduled', 'finished', 'postponed', 'cancelled'] as FixtureStatus[]).map(key => {
            const count = key === 'all' ? fixtures.length : fixtures.filter(fixture => fixture.status === key).length
            if (key !== 'all' && count === 0) return null
            const label = key === 'all' ? 'All' : key === 'scheduled' ? 'Upcoming' : key === 'live' ? 'In play' : key[0].toUpperCase() + key.slice(1)
            return <button key={key} className={status === key ? 'active' : ''} onClick={() => setStatus(key)} aria-pressed={status === key}>{label} <b>{count}</b></button>
          })}
          <button className={`fixture-favorites-filter${favoriteOnly ? ' active' : ''}`} onClick={() => setFavoriteOnly(current => !current)} aria-pressed={favoriteOnly} title="Favourites are saved in this browser">
            <span aria-hidden="true">★</span> Favourites <b>{favorites.length}</b>
          </button>
        </div>
      </div>
      {filtered.length === 0 ? <p className="panel-empty">{favoriteOnly && favorites.length === 0 ? 'No favourites yet. Select a star beside a match to save it here.' : 'No fixtures match these filters.'}</p> : (
        <div className="fixture-list">
          {filtered.map(fixture => (
            <div className="fixture-row-wrap" key={fixture.match_id}>
              <button
                className={`fixture-favorite${favoriteIds.has(fixture.match_id) ? ' active' : ''}`}
                onClick={() => toggleFavorite(fixture.match_id)}
                aria-label={`${favoriteIds.has(fixture.match_id) ? 'Remove' : 'Add'} ${fixture.home_team} vs ${fixture.away_team} ${favoriteIds.has(fixture.match_id) ? 'from' : 'to'} favourites`}
                aria-pressed={favoriteIds.has(fixture.match_id)}
                title={favoriteIds.has(fixture.match_id) ? 'Remove from favourites' : 'Add to favourites'}
              >★</button>
              <details className={`fixture-row ${fixture.status}`}>
              <summary>
                <span className="fixture-league">{fixture.competition}</span>
                <strong>{fixture.home_team} <span className="leg-match-vs">vs</span> {fixture.away_team}</strong>
                <span className={`summary-match-status ${fixture.status}`}>{fixture.status === 'scheduled' ? 'Upcoming' : fixture.status === 'live' ? 'In play' : fixture.status[0].toUpperCase() + fixture.status.slice(1)}</span>
                <span className="fixture-kickoff">{formatKickoff(fixture.kickoff_at)}</span>
              </summary>
              <div className="fixture-drilldown">
                <span><small>Competition</small><b>{fixture.competition}</b></span>
                <span><small>Kickoff</small><b>{new Date(fixture.kickoff_at).toLocaleString('en-GB')}</b></span>
                <span><small>Status</small><b>{fixture.status === 'live' && fixture.elapsed_minutes != null ? `In play · ${fixture.elapsed_minutes}'` : fixture.status === 'live' && fixture.live_phase ? `In play · ${fixture.live_phase}` : fixture.status}</b></span>
                {fixture.home_goals != null && fixture.away_goals != null && <span><small>Score</small><b>{fixture.home_goals} – {fixture.away_goals}</b></span>}
                <span className="fixture-pro-note">Market selections and model analysis are available with Pro.</span>
              </div>
              </details>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
