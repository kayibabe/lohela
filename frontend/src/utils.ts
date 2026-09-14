export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
  })
}

export function formatKickoff(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

export function formatOdds(n: number): string {
  return n.toFixed(2)
}

export function formatPct(n: number): string {
  return `${(n * 100).toFixed(1)}%`
}

export function formatEV(n: number): string {
  const sign = n >= 0 ? '+' : ''
  return `${sign}${(n * 100).toFixed(1)}%`
}

export function gradeColor(grade: string): string {
  if (grade === 'A+' || grade === 'A') return 'grade-a'
  if (grade === 'B+' || grade === 'B') return 'grade-b'
  if (grade === 'C') return 'grade-c'
  return 'grade-reject'
}

export function marketLabel(market: string): string {
  const labels: Record<string, string> = {
    home_win: 'Home Win',
    draw: 'Draw',
    away_win: 'Away Win',
    'over_1.5': 'Over 1.5',
    'over_2.5': 'Over 2.5',
    btts_yes: 'BTTS Yes',
    double_chance_1x: 'DC 1X',
    double_chance_x2: 'DC X2',
    dnb_home: 'DNB Home',
    dnb_away: 'DNB Away',
  }
  return labels[market] ?? market
}

export function addDays(dateStr: string, n: number): string {
  const d = new Date(dateStr)
  d.setDate(d.getDate() + n)
  return d.toISOString().slice(0, 10)
}

export function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}
