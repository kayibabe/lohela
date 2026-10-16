import { useEffect, useMemo, useState } from 'react'
import type { MatchFixture } from '../lib/api'
import { fetchPublicMatchFixtures } from '../lib/api'
import { TODAY } from './dailyTickets/helpers'
import './PublicHome.css'

interface PublicHomeProps {
  date: string
  onDateChange: (date: string) => void
  onSignIn: () => void
  onCreateAccount: () => void
}

type FixtureSortKey = 'kickoff' | 'league' | 'match' | 'statusScore'
type SortDirection = 'asc' | 'desc'

function shiftDate(date: string, amount: number) {
  const value = new Date(`${date}T12:00:00`)
  value.setDate(value.getDate() + amount)
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
}

function kickoffLabel(value: string) {
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function matchStatus(match: MatchFixture) {
  const status = match.status.toLowerCase()
  if (status === 'live') return match.elapsed_minutes != null ? `${match.elapsed_minutes}\u2032` : match.live_phase || 'Live'
  if (status === 'finished') return 'FT'
  if (status === 'postponed') return 'Postponed'
  if (status === 'cancelled') return 'Cancelled'
  return 'Upcoming'
}

export default function PublicHome({ date, onDateChange, onSignIn, onCreateAccount }: PublicHomeProps) {
  const [matches, setMatches] = useState<MatchFixture[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [activeStatus, setActiveStatus] = useState<'all' | 'live' | 'scheduled' | 'finished'>('all')
  const [search, setSearch] = useState('')
  const [selectedLeague, setSelectedLeague] = useState('')
  const [sortKey, setSortKey] = useState<FixtureSortKey>('kickoff')
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc')

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(false)
    fetchPublicMatchFixtures(date)
      .then((rows) => { if (active) setMatches(rows) })
      .catch(() => { if (active) { setMatches([]); setError(true) } })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [date])

  const counts = useMemo(() => ({
    live: matches.filter((match) => match.status.toLowerCase() === 'live').length,
    finished: matches.filter((match) => match.status.toLowerCase() === 'finished').length,
    upcoming: matches.filter((match) => match.status.toLowerCase() === 'scheduled').length,
  }), [matches])
  const leagues = useMemo(() => {
    const counts = new Map<string, number>()
    matches.forEach((match) => counts.set(match.competition, (counts.get(match.competition) ?? 0) + 1))
    return [...counts.entries()].sort((left, right) => right[1] - left[1]).slice(0, 6)
  }, [matches])
  const today = TODAY
  const availableLeagues = useMemo(() => [...new Set(matches.map((match) => match.competition))].sort((a, b) => a.localeCompare(b)), [matches])
  const visibleMatches = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase()
    const filtered = matches.filter((match) => {
      const status = match.status.toLowerCase()
      const statusMatch = activeStatus === 'all' || status === activeStatus
      const leagueMatch = !selectedLeague || match.competition === selectedLeague
      const searchMatch = !needle || `${match.home_team} ${match.away_team} ${match.competition}`.toLocaleLowerCase().includes(needle)
      return statusMatch && leagueMatch && searchMatch
    })
    const direction = sortDirection === 'asc' ? 1 : -1
    return filtered.sort((a, b) => {
      let order = 0
      if (sortKey === 'kickoff') order = a.kickoff_at.localeCompare(b.kickoff_at)
      if (sortKey === 'league') order = a.competition.localeCompare(b.competition, undefined, { numeric: true, sensitivity: 'base' })
      if (sortKey === 'match') order = `${a.home_team} ${a.away_team}`.localeCompare(`${b.home_team} ${b.away_team}`, undefined, { numeric: true, sensitivity: 'base' })
      if (sortKey === 'statusScore') order = `${matchStatus(a)} ${a.home_goals ?? ''} ${a.away_goals ?? ''}`.localeCompare(`${matchStatus(b)} ${b.home_goals ?? ''} ${b.away_goals ?? ''}`, undefined, { numeric: true, sensitivity: 'base' })
      return order * direction || a.kickoff_at.localeCompare(b.kickoff_at)
    })
  }, [matches, activeStatus, selectedLeague, search, sortKey, sortDirection])

  const sortHeader = (key: FixtureSortKey, label: string) => {
    const active = sortKey === key
    const ariaSort = active ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'
    return <th scope="col" aria-sort={ariaSort}><button className="public-fixture-sort-button" onClick={() => {
      if (active) setSortDirection(current => current === 'asc' ? 'desc' : 'asc')
      else { setSortKey(key); setSortDirection('asc') }
    }}>{label}<span aria-hidden="true">{active ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}</span></button></th>
  }

  return (
    <main className="public-home">
      <header className="public-topbar">
        <a className="public-brand" href="/" aria-label="Lohela home">
          <span className="public-brand-mark">L</span>
          <span><strong>LOHELA</strong><small>FOOTBALL INTELLIGENCE</small></span>
        </a>
        <nav className="public-page-nav" aria-label="Explore Lohela">
          <a href="#public-product">How it works</a>
          <a href="#public-matches">Fixtures</a>
          <a href="#public-faq">FAQs</a>
        </nav>
        <nav className="public-top-actions" aria-label="Account">
          <button className="public-link-button" onClick={onSignIn}>Sign in</button>
          <button className="public-cta" onClick={onCreateAccount}>Create free account <span aria-hidden="true">→</span></button>
        </nav>
      </header>

      <div className="public-dashboard" id="public-matches">
        <aside className="public-dashboard-rail" aria-label="Public Lohela tools">
          <nav className="public-tool-nav" aria-label="Tools">
            <span className="public-rail-heading">EXPLORE</span>
            <a className="is-active" href="#public-matches" aria-current="page"><span aria-hidden="true">◉</span>Matches</a>
            <a href="#public-product"><span aria-hidden="true">▤</span>How Lohela works</a>
            <a href="#public-faq"><span aria-hidden="true">ⓘ</span>FAQs</a>
          </nav>
          <section className="public-side-card public-rail-card">
            <div className="public-side-title"><span className="public-section-kicker">ON THIS DATE</span><strong>Top leagues</strong></div>
            {leagues.length ? leagues.map(([league, count]) => <button className={`public-league-row ${selectedLeague === league ? 'is-selected' : ''}`} key={league} onClick={() => setSelectedLeague(current => current === league ? '' : league)} aria-pressed={selectedLeague === league}><span>{league}</span><b>{count}</b></button>) : <p className="public-side-empty">League coverage appears with the fixture list.</p>}
            {selectedLeague && <button className="public-clear-filter" onClick={() => setSelectedLeague('')}>Clear league filter</button>}
          </section>
          <section className="public-rail-note"><span className="public-section-kicker">FOOTBALL, WITH CONTEXT</span><p>Follow fixtures, live status and completed scores. Recommendations remain distinct from match results.</p><button className="public-link-button" onClick={onCreateAccount}>Open your workspace →</button></section>
        </aside>

        <section className="public-content" aria-labelledby="public-matches-title">
          <div className="public-dashboard-heading">
            <div><span className="public-section-kicker">MATCH CENTRE</span><h1 id="public-matches-title">Fixtures &amp; scores</h1><p>Browse public fixtures and follow the matchday as it unfolds.</p></div>
          </div>

          <div className="public-date-strip" aria-label="Choose fixture date">
            <button className={`public-date-live ${activeStatus === 'live' ? 'is-active' : ''}`} onClick={() => setActiveStatus(current => current === 'live' ? 'all' : 'live')} aria-pressed={activeStatus === 'live'}><span className="public-live-dot" />Live <b>{counts.live}</b></button>
            {[-2, -1, 0, 1, 2].map((offset) => {
              const tabDate = offset === 0 ? date : shiftDate(date, offset)
              const value = new Date(`${tabDate}T12:00:00`)
              return <button key={offset} className={`public-date-tab ${offset === 0 ? 'is-selected' : ''}`} onClick={() => onDateChange(tabDate)} aria-current={offset === 0 ? 'date' : undefined}><span>{value.toLocaleDateString('en-GB', { weekday: 'short' })}</span><strong>{value.getDate()}</strong>{tabDate === today && <small>TODAY</small>}</button>
            })}
          </div>

          <div className="public-status-filters" role="group" aria-label="Filter matches by status">
            {([['all', 'All matches', matches.length], ['scheduled', 'Upcoming', counts.upcoming], ['finished', 'Finished', counts.finished]] as const).map(([status, label, count]) => <button key={status} className={`public-filter-chip ${activeStatus === status ? 'is-active' : ''}`} onClick={() => setActiveStatus(status)} aria-pressed={activeStatus === status}>{label}<span>{loading ? '—' : count}</span></button>)}
          </div>

          <div className="public-fixture-toolbar">
            <label className="public-search-box"><span aria-hidden="true">⌕</span><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Team, league or country" aria-label="Search teams and leagues" /></label>
            <label className="public-league-select"><span className="public-select-icon" aria-hidden="true">☷</span><select value={selectedLeague} onChange={(event) => setSelectedLeague(event.target.value)} aria-label="Filter by league"><option value="">All leagues</option>{availableLeagues.map((league) => <option key={league} value={league}>{league}</option>)}</select></label>
            <span className="public-fixture-count">{loading ? 'Updating…' : `${visibleMatches.length} matches`}</span>
          </div>

          <div className="public-match-panel">
            {error ? <div className="public-empty"><strong>Fixture data is temporarily unavailable</strong><span>Please try again shortly.</span></div>
              : loading ? <div className="public-empty"><span>Loading fixtures…</span></div>
                : visibleMatches.length === 0 ? <div className="public-empty"><strong>No matches match these filters</strong><span>Try another date, league or search.</span></div>
                  : <div className="public-fixture-table-wrap"><table className="public-fixture-table">
                    <thead><tr>{sortHeader('kickoff', 'Kickoff Time')}{sortHeader('league', 'League')}{sortHeader('match', 'Match')}{sortHeader('statusScore', 'Status/Score')}</tr></thead>
                    <tbody>{visibleMatches.map((match) => {
                      const status = match.status.toLowerCase()
                      const hasScore = match.home_goals != null && match.away_goals != null
                      return <tr className="public-fixture-table-row" key={match.match_id}>
                        <td className="public-fixture-time"><time dateTime={match.kickoff_at}>{kickoffLabel(match.kickoff_at)}</time></td>
                        <td className="public-fixture-league" title={match.competition}>{match.competition}</td>
                        <td><div className="public-fixture-match" title={`${match.home_team} vs ${match.away_team}`}><span>{match.home_team}</span><small>vs</small><span>{match.away_team}</span></div></td>
                        <td><div className="public-fixture-status-score"><span className={`public-fixture-state ${status === 'live' ? 'is-live' : ''}`}>{matchStatus(match)}</span>{hasScore && <strong>{match.home_goals} : {match.away_goals}</strong>}</div></td>
                      </tr>
                    })}</tbody>
                  </table></div>}
            <div className="public-match-panel-foot">Scores and match states are shown when available. No outcome is guaranteed.</div>
          </div>
        </section>
      </div>

      <section className="public-product" id="public-product" aria-labelledby="public-product-title">
        <div className="public-product-heading">
          <span className="public-section-kicker">THE LOHELA APPROACH</span>
          <h2 id="public-product-title">From fixture list to final result.</h2>
          <p>Useful football research should show its work—and leave room for uncertainty.</p>
        </div>
        <div className="public-product-grid">
          <article className="public-product-card">
            <span className="public-product-number">01</span>
            <h3>Start with the match</h3>
            <p>Browse fixtures by date, competition, kickoff, live status and score. Sort the slate to find the games you want to follow.</p>
            <a href="#public-matches">Browse fixtures <span aria-hidden="true">→</span></a>
          </article>
          <article className="public-product-card">
            <span className="public-product-number">02</span>
            <h3>Read the recommendation in context</h3>
            <p>Lohela keeps published selections distinct from research candidates. Where price evidence is available, the workspace shows the captured odds and timing.</p>
            <span className="public-product-note">Recommendations are opinions, not guarantees.</span>
          </article>
          <article className="public-product-card">
            <span className="public-product-number">03</span>
            <h3>Review the result honestly</h3>
            <p>When a match is complete, its score and settled selection outcome can be reviewed against the original published record.</p>
            <span className="public-product-note">Historical results describe the sample shown; they do not predict future results.</span>
          </article>
        </div>
      </section>

      <section className="public-access" aria-label="Lohela account access">
        <div className="public-access-heading">
          <span className="public-section-kicker">START FREE, GO DEEPER WHEN YOU NEED TO</span>
          <h2>Choose how you want to explore.</h2>
          <p>You can look around before creating an account. Your workspace adds more ways to follow the game.</p>
        </div>
        <div className="public-access-grid">
          <article className="public-access-card">
            <div><span className="public-access-label">PUBLIC</span><h3>Take a look around</h3></div>
            <p>Explore public fixtures, competition names, live status and completed scores—without signing in.</p>
            <a href="#public-matches">Open the match centre <span aria-hidden="true">→</span></a>
          </article>
          <article className="public-access-card is-featured">
            <div><span className="public-access-label">FREE ACCOUNT</span><h3>Follow the daily slate</h3></div>
            <p>Open your Lohela workspace for daily published recommendations, match browsing and live scores.</p>
            <button onClick={onCreateAccount}>Create a free account <span aria-hidden="true">→</span></button>
          </article>
          <article className="public-access-card">
            <div><span className="public-access-label">PRO</span><h3>Explore more detail</h3></div>
            <p>Pro adds detailed odds and expanded ticket history. Stats &amp; Trends gives league context from completed matches.</p>
            <button onClick={onCreateAccount}>See what’s inside <span aria-hidden="true">→</span></button>
          </article>
        </div>
      </section>

      <section className="public-faq" id="public-faq" aria-labelledby="public-faq-title">
        <div className="public-faq-heading"><span className="public-section-kicker">GOOD TO KNOW</span><h2 id="public-faq-title">A few quick answers.</h2></div>
        <div className="public-faq-list">
          <details><summary>What can I see without an account?</summary><p>Public fixtures include the competition, teams, kickoff or live status, and scores when available. Personal account information and workspace tools stay behind sign-in.</p></details>
          <details><summary>What does Lohela do with recommendations?</summary><p>Lohela presents dated published selections separately from research. Where captured price evidence exists, it can be reviewed alongside the selection. A recommendation is never a promise of a result.</p></details>
          <details><summary>Is Lohela free to use?</summary><p>Public fixture browsing is open. A free account unlocks the daily workspace; Pro adds detailed odds, expanded ticket history and Stats &amp; Trends.</p></details>
          <details><summary>Does Lohela guarantee wins or profit?</summary><p>No. Football outcomes are uncertain. Any historical result is a record of completed matches, not a guarantee or forecast of future performance.</p></details>
        </div>
        <div className="public-faq-cta"><div><strong>Ready to follow the next matchday?</strong><span>Create a free account to open your Lohela workspace.</span></div><button className="public-cta" onClick={onCreateAccount}>Get started for free <span aria-hidden="true">→</span></button></div>
      </section>

      <footer className="public-footer"><span>LOHELA · Intelligence beyond numbers</span><span>Information only. Football outcomes are uncertain; no result is guaranteed.</span></footer>
    </main>
  )
}
