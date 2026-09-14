export interface Bet {
  id: number
  label: string
  odds: number
  stake: number
  potential_return: number
  ticket_type: string | null
  ticket_date: string | null
  status: string
  actual_return: number | null
  profit_loss: number | null
  roi: number | null
  notes: string | null
  created_at: string
  source_selection_id: number | null
  match_id: number | null
  market: string | null
  selection: string | null
}

interface JournalDateGroup { date: string; label: string; rows: Bet[] }
interface JournalMonthGroup { month: string; label: string; dates: JournalDateGroup[] }
interface JournalYearGroup { year: string; months: JournalMonthGroup[] }

export function groupJournalBets(rows: Bet[]): JournalYearGroup[] {
  const years = new Map<string, Map<string, Map<string, Bet[]>>>()
  for (const row of rows) {
    const date = row.ticket_date || row.created_at.slice(0, 10)
    const year = date.slice(0, 4), month = date.slice(0, 7)
    if (!years.has(year)) years.set(year, new Map())
    const months = years.get(year)!
    if (!months.has(month)) months.set(month, new Map())
    const dates = months.get(month)!
    dates.set(date, [...(dates.get(date) ?? []), row])
  }
  return [...years.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([year, months]) => ({
    year,
    months: [...months.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([month, dates]) => ({
      month,
      label: new Date(`${month}-01T00:00:00`).toLocaleDateString(undefined, { month: 'long' }),
      dates: [...dates.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([date, dateRows]) => ({ date, label: new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' }), rows: dateRows })),
    })),
  }))
}

export interface MatchHistoryEvidence {
  market: string
  selection: string
  model_probability: number
  model_spread: number | null
  q_score: number
  edge: number | null
  odds: number | null
  odds_captured_at: string | null
  result: string
}

export interface MatchHistoryRow {
  match_id: number
  target_date: string
  kickoff_at: string
  home_team: string
  away_team: string
  competition: string
  status: string
  live_phase: string | null
  elapsed_minutes: number | null
  home_goals: number | null
  away_goals: number | null
  outcome: string | null
  ticket_types: string[]
  selections: string[]
  selection_evidence: MatchHistoryEvidence[]
}

interface MatchHistoryMonthGroup {
  label: string
  matchesByDate: Array<{ date: string; label: string; matches: MatchHistoryRow[] }>
}

interface MatchHistoryYearGroup { year: number; months: MatchHistoryMonthGroup[] }

export function groupMatchHistory(rows: MatchHistoryRow[]): MatchHistoryYearGroup[] {
  const years = new Map<number, Map<string, Map<string, MatchHistoryRow[]>>>()
  for (const row of rows) {
    const parsed = new Date(`${row.target_date}T00:00:00`)
    const year = parsed.getFullYear()
    const month = parsed.toLocaleDateString(undefined, { month: 'long' })
    if (!years.has(year)) years.set(year, new Map())
    const months = years.get(year)!
    if (!months.has(month)) months.set(month, new Map())
    const dates = months.get(month)!
    dates.set(row.target_date, [...(dates.get(row.target_date) ?? []), row])
  }
  return [...years.entries()].sort(([a], [b]) => b - a).map(([year, months]) => ({
    year,
    months: [...months.entries()].map(([label, dates]) => ({
      label,
      matchesByDate: [...dates.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([date, matches]) => ({
        date,
        label: new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' }),
        matches,
      })),
    })),
  }))
}
