import { useEffect, useState } from 'react'
import { fetchLeagueInsights, type LeagueInsights } from '../lib/api'

function percent(value: number | null | undefined) {
  return value == null ? 'Unavailable' : `${value.toFixed(1)}%`
}

export default function InsightsPage() {
  const [data, setData] = useState<LeagueInsights | null>(null)
  const [competitionId, setCompetitionId] = useState('')
  const [season, setSeason] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let current = true
    setLoading(true)
    setError('')
    fetchLeagueInsights(competitionId ? Number(competitionId) : undefined, season || undefined)
      .then((result) => {
        if (!current) return
        setData(result)
        if (!competitionId && result.competitions.length) {
          setCompetitionId(String(result.competitions[0].id))
          setSeason(result.competitions[0].seasons[0] ?? '')
        }
      })
      .catch(() => { if (current) setError('League insights are temporarily unavailable. Please try again.') })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [competitionId, season])

  const selectedCompetition = data?.competitions.find((item) => String(item.id) === competitionId)
  const summary = data?.summary

  return (
    <section className="league-insights-page">
      <div className="section-heading">
        <div><h1>Stats &amp; trends</h1><p>League results and team form, calculated from completed matches in Lohela’s database.</p></div>
      </div>
      <div className="insights-controls">
        <label>Competition
          <select value={competitionId} onChange={(event) => {
            const next = data?.competitions.find((item) => String(item.id) === event.target.value)
            setCompetitionId(event.target.value)
            setSeason(next?.seasons[0] ?? '')
          }}>
            <option value="">Choose a competition</option>
            {data?.competitions.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.country}</option>)}
          </select>
        </label>
        <label>Season
          <select value={season} onChange={(event) => setSeason(event.target.value)} disabled={!selectedCompetition}>
            {selectedCompetition?.seasons.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        {selectedCompetition && <span>{selectedCompetition.completed_matches} completed matches across seasons</span>}
      </div>
      {error && <p className="insights-notice" role="alert">{error}</p>}
      {loading && <p className="insights-notice" role="status">Loading results…</p>}
      {!loading && !error && data?.competitions.length === 0 && <p className="insights-notice">No completed match results are available yet.</p>}
      {!loading && !error && summary && (
        <>
          <div className="insights-sample-note">Based on {summary.played} completed matches · {selectedCompetition?.name} · {season}</div>
          <div className="insights-metric-grid">
            <article><span>Average goals</span><strong>{summary.average_goals?.toFixed(2) ?? 'Unavailable'}</strong></article>
            <article><span>Home wins</span><strong>{percent(summary.home_win_pct)}</strong></article>
            <article><span>Draws</span><strong>{percent(summary.draw_pct)}</strong></article>
            <article><span>Away wins</span><strong>{percent(summary.away_win_pct)}</strong></article>
            <article><span>Both teams scored</span><strong>{percent(summary.btts_pct)}</strong></article>
            <article><span>Over 2.5 goals</span><strong>{percent(summary.over_2_5_pct)}</strong></article>
            <article><span>xG coverage</span><strong>{percent(summary.xg_coverage_pct)}</strong></article>
          </div>
          <section className="insights-table-card">
            <div className="insights-table-heading"><h2>Team table</h2><span>Sorted by points, goal difference, then goals scored</span></div>
            <div className="insights-table-scroll"><table>
              <thead><tr><th>#</th><th>Team</th><th>Pl</th><th>W</th><th>D</th><th>L</th><th>GF</th><th>GA</th><th>GD</th><th>Pts</th><th>Form</th><th>Avg xG</th></tr></thead>
              <tbody>{data.teams.map((team, index) => <tr key={team.team_id}>
                <td>{index + 1}</td><th scope="row">{team.team}</th><td>{team.played}</td><td>{team.won}</td><td>{team.drawn}</td><td>{team.lost}</td><td>{team.goals_for}</td><td>{team.goals_against}</td><td>{team.goal_difference > 0 ? `+${team.goal_difference}` : team.goal_difference}</td><td><strong>{team.points}</strong></td><td aria-label={`Recent form ${team.recent_form}`}>{team.recent_form || '—'}</td><td>{team.avg_xg == null ? '—' : team.avg_xg.toFixed(2)}</td>
              </tr>)}</tbody>
            </table></div>
          </section>
          <p className="insights-footnote">xG is shown only when recorded. Percentages describe this completed-match sample and are not forecasts.</p>
        </>
      )}
    </section>
  )
}
