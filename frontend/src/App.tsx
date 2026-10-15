import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import './App.css'
import { TODAY } from './pages/dailyTickets/helpers'
import { addDays } from './utils'
import { AuthControls, useAuth } from './auth'

const DailyTicketsPage = lazy(() => import('./pages/DailyTickets'))
const TrackerPage = lazy(() => import('./pages/Tracker'))
const AnalyticsPage = lazy(() => import('./pages/Analytics'))
const ToolsPage = lazy(() => import('./pages/Tools'))
const AdminPage = lazy(() => import('./pages/Admin'))
const UpgradePage = lazy(() => import('./pages/Upgrade'))
const MatchIntelligencePage = lazy(() => import('./pages/MatchIntelligencePage'))
const LoginPage = lazy(() => import('./pages/Login'))
const InsightsPage = lazy(() => import('./pages/Insights'))
const LiveMonitorPage = lazy(() => import('./pages/LiveMonitor'))

function PageLoading() {
  return <div className="route-loading" role="status" aria-live="polite">Loading page…</div>
}

type Theme = 'dark' | 'light' | 'system'
type ModulePage = 'tickets' | 'matches' | 'live' | 'insights' | 'research' | 'tracker' | 'analytics' | 'tools' | 'admin'
type Page = ModulePage | 'match' | 'upgrade' | 'login'

function dateFromSearch(search: string): string {
  const value = new URLSearchParams(search).get('date')
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return TODAY
  const [year, month, day] = value.split('-').map(Number)
  const parsed = new Date(year, month - 1, day)
  return parsed.getFullYear() === year && parsed.getMonth() === month - 1 && parsed.getDate() === day
    ? value
    : TODAY
}

function datePath(path: string, date: string): string {
  return date === TODAY ? path : `${path}?date=${encodeURIComponent(date)}`
}

const NAV: { id: ModulePage; label: string }[] = [
  { id: 'tickets', label: 'Today' },
  { id: 'matches', label: 'Matches' },
  { id: 'live', label: 'Live' },
  { id: 'insights', label: 'Stats & trends' },
]
const INTERNAL_NAV: { id: ModulePage; label: string }[] = [
  { id: 'research', label: 'Research' },
  { id: 'tracker', label: 'Track' },
  { id: 'analytics', label: 'Validate' },
  { id: 'tools', label: 'Tools' },
  { id: 'admin', label: 'Admin' },
]

const NAV_META: Record<ModulePage, { icon: string; hint: string }> = {
  tickets: { icon: 'sparkles', hint: 'Published recommendations' },
  matches: { icon: 'search', hint: 'Browse fixtures and qualified match markets' },
  live: { icon: 'activity', hint: 'Live scores and match phases' },
  insights: { icon: 'chart', hint: 'League results and team trends' },
  research: { icon: 'flask', hint: 'Research models and selection policies' },
  tracker: { icon: 'list', hint: 'Evidence and personal journal' },
  analytics: { icon: 'chart', hint: 'Performance and model validation' },
  tools: { icon: 'wrench', hint: 'Calculators and research utilities' },
  admin: { icon: 'shield', hint: 'System administration' },
}

function NavIcon({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    sparkles: <><path d="m12 3-1.2 4.1L7 8.3l3.8 1.2L12 13l1.2-3.5L17 8.3l-3.8-1.2L12 3Z" /><path d="m5 14-.7 2.3L2 17l2.3.7L5 20l.7-2.3L8 17l-2.3-.7L5 14Z" /></>,
    list: <><path d="m4 6 1.5 1.5L8 5" /><path d="M11 6h9M11 12h9M11 18h9" /><path d="m4 12 1.5 1.5L8 11" /><path d="m4 18 1.5 1.5L8 17" /></>,
    chart: <><path d="M5 20V10M12 20V4M19 20v-7" /></>,
    activity: <><path d="M3 12h4l3-8 4 16 3-8h4" /></>,
    search: <><circle cx="10.8" cy="10.8" r="6.6" /><path d="m16 16 4.5 4.5" /></>,
    flask: <><path d="M9 3h6M10 3v6l-5.2 8.5A2.3 2.3 0 0 0 6.8 21h10.4a2.3 2.3 0 0 0 2-3.5L14 9V3" /><path d="M7.7 15h8.6" /></>,
    wrench: <><path d="m14.7 6.3 3-3a5 5 0 0 0-6.4 6.4L3 18a2 2 0 1 0 3 3l8.3-8.3a5 5 0 0 0 6.4-6.4l-3 3-3-3Z" /></>,
    shield: <><path d="M12 3 20 6v5c0 5-3.4 8.6-8 10-4.6-1.4-8-5-8-10V6l8-3Z" /><path d="m9 12 2 2 4-4" /></>,
  }
  return <svg className="nav-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

export default function App() {
  const { user, loading: authLoading } = useAuth()
  const isInternal = user?.role === 'admin'
  const [page, setPage] = useState<Page>(() => routePage(window.location.pathname))
  const [matchId, setMatchId] = useState<number | null>(() => routeMatchId(window.location.pathname))
  const [date, setDate] = useState(() => dateFromSearch(window.location.search))
  const [theme, setTheme] = useState<Theme>('system')
  const [lastUpdated, setLastUpdated] = useState(() => new Date())

  useEffect(() => {
    const url = new URL(window.location.href)
    if (date === TODAY) url.searchParams.delete('date')
    else url.searchParams.set('date', date)
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  }, [date])

  useEffect(() => {
    const onPopState = () => {
      setPage(routePage(window.location.pathname))
      setMatchId(routeMatchId(window.location.pathname))
      setDate(dateFromSearch(window.location.search))
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useEffect(() => {
    if (authLoading) return
    const path = window.location.pathname
    if (!user && path !== '/login') {
      window.history.replaceState({}, '', '/login')
      setPage('login')
      setMatchId(null)
    } else if (user && path === '/login') {
      window.history.replaceState({}, '', '/tickets')
      setPage('tickets')
    }
  }, [authLoading, user])

  useEffect(() => {
    if (authLoading || !user || isInternal || !isInternalPage(page)) return
    setPage('tickets')
    setMatchId(null)
    window.history.replaceState({}, '', '/tickets')
  }, [authLoading, isInternal, page])

  function navigate(next: Page, _requested?: ModulePage, replace = false) {
    setPage(next)
    const path = next === 'upgrade' ? '/upgrade' : `/${next}`
    window.history[replace ? 'replaceState' : 'pushState']({}, '', datePath(path, date))
  }

  function navigateToMatch(nextMatchId: number) {
    setMatchId(nextMatchId)
    setPage('match')
    window.history.pushState({}, '', datePath(`/match/${nextMatchId}`, date))
  }

  function goTo(next: ModulePage) {
    navigate(next)
  }

  const cycleTheme = () => {
    setTheme(t => {
      const next = t === 'system' ? 'dark' : t === 'dark' ? 'light' : 'system'
      if (next === 'system') {
        document.documentElement.removeAttribute('data-theme')
      } else {
        document.documentElement.setAttribute('data-theme', next)
      }
      return next
    })
  }

  const nav = (n: number) => setDate(d => addDays(d, n))

  if (authLoading) {
    return <div className="auth-standalone" role="status" aria-live="polite">Checking your session…</div>
  }

  if (!user) {
    return <div className="auth-standalone"><Suspense fallback={<PageLoading />}><LoginPage onSuccess={() => navigate('tickets', undefined, true)} /></Suspense></div>
  }

  return (
    <div className="app">
      <aside className="app-sidebar">
        <a className="brand" href="/" aria-label="Lohela home" onClick={(event) => { event.preventDefault(); goTo('tickets') }}>
          <img className="brand-logo" src="/lohela-compact.png" alt="Lohela — Intelligence beyond numbers" />
        </a>
        <nav className="sidebar-nav" aria-label="Primary navigation">
          <span className="sidebar-label">Football</span>
          {NAV.map(n => (
            <button key={n.id} className={`nav-tab${page === n.id ? ' active' : ''}`} onClick={() => goTo(n.id)} title={NAV_META[n.id].hint} aria-current={page === n.id ? 'page' : undefined}>
              <span className="nav-icon"><NavIcon name={NAV_META[n.id].icon} /></span>{n.label}
            </button>
          ))}
          {isInternal && <>
            <span className="sidebar-label sidebar-label-evidence">Internal workspace</span>
            {INTERNAL_NAV.map(n => (
              <button key={n.id} className={`nav-tab${page === n.id ? ' active' : ''}`} onClick={() => goTo(n.id)} title={NAV_META[n.id].hint} aria-current={page === n.id ? 'page' : undefined}>
                <span className="nav-icon"><NavIcon name={NAV_META[n.id].icon} /></span>{n.label}
              </button>
            ))}
          </>}
        </nav>
        <div className="sidebar-account">
          <AuthControls onSignIn={() => navigate('login')} onSignedOut={() => navigate('login', undefined, true)} />
        </div>
      </aside>
      <header className="app-header">
        <a className="topbar-brand" href="/tickets" aria-label="Lohela home" onClick={(event) => { event.preventDefault(); goTo('tickets') }}>
          <span className="topbar-mark" aria-hidden="true">L</span><span>LOHELA</span>
        </a>
        {(page === 'tickets' || page === 'matches' || (page === 'research' && isInternal)) && (
          <div className="date-nav">
            <button className="date-btn" onClick={() => nav(-1)} title="Previous day" aria-label="Previous day">‹</button>
            <div className="date-strip" role="group" aria-label="Choose match date">
              {[-2, -1, 0, 1, 2].map(offset => {
                const value = addDays(date, offset)
                const localDate = new Date(`${value}T12:00:00`)
                const label = localDate.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'long', year: 'numeric' })
                return <button
                  key={value}
                  className={`date-strip-day${date === value ? ' active' : ''}${value === TODAY ? ' today' : ''}`}
                  onClick={() => setDate(value)}
                  aria-label={label}
                  aria-pressed={date === value}
                >
                  <span>{value === TODAY ? 'Today' : localDate.toLocaleDateString('en-GB', { weekday: 'short' })}</span>
                  <b>{localDate.getDate()}</b>
                </button>
              })}
            </div>
            <div className="date-nav-tools">
              <button className="today-btn" onClick={() => setDate(TODAY)} disabled={date === TODAY}>Today</button>
              <label className="date-picker-control" title="Choose any date">
                <span aria-hidden="true">▦</span>
              <input
                className="date-input"
                type="date"
                value={date}
                aria-label="Choose any date"
                onChange={e => setDate(e.target.value)}
              />
              </label>
            </div>
            <button className="date-btn" onClick={() => nav(1)} title="Next day" aria-label="Next day">›</button>
          </div>
        )}
        <div className="header-page-title">
        <span className="research-status"><span className="status-dot" /> {page === 'tickets' ? 'Decision workspace' : page === 'match' ? 'Match intelligence' : page === 'upgrade' ? 'System' : page === 'login' ? 'Account' : NAV_META[page].hint}</span>
          <button className="theme-btn" onClick={cycleTheme} title={`Theme: ${theme}`} aria-label={`Theme: ${theme}. Change theme`}>
            <span className="theme-auto">{theme === 'system' ? 'Auto' : theme}</span>
          </button>
        </div>
        <div className="header-refresh">
          <AuthControls onSignIn={() => navigate('login')} onSignedOut={() => navigate('login', undefined, true)} />
          <button className="premium-button" onClick={() => navigate('upgrade')}>{user?.plan === 'pro' ? 'Pro account' : 'Go Premium'}</button>
          <button className="theme-btn settings-btn" onClick={cycleTheme} title={`Theme: ${theme}`} aria-label={`Theme: ${theme}. Change theme`}>⚙</button>
          <span className="global-updated">Refreshed {lastUpdated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
          <button className="global-refresh" onClick={() => { setLastUpdated(new Date()); window.location.reload() }} title="Refresh current page" aria-label="Refresh current page">↻ <span>Refresh</span></button>
        </div>
      </header>

      <main className="main" id="main-content" tabIndex={-1}>
        <Suspense fallback={<PageLoading />}>
        {page === 'login' && <LoginPage onSuccess={() => navigate('tickets', undefined, true)} />}
        {page === 'upgrade' && <UpgradePage onBack={() => goTo('tickets')} />}
        {page === 'tickets' && <DailyTicketsPage key={`${date}-${page}`} date={date} initialTab="tickets" onOpenMatch={navigateToMatch} />}
        {page === 'matches' && <DailyTicketsPage key={`${date}-${page}`} date={date} initialTab="matches" onOpenMatch={navigateToMatch} />}
        {page === 'live' && <LiveMonitorPage />}
        {page === 'insights' && (user.plan === 'pro' || isInternal
          ? <InsightsPage />
          : <section className="insights-access-card"><h1>Stats &amp; trends</h1><p>League results and team tables are available on Pro.</p><button className="btn-primary" onClick={() => navigate('upgrade')}>View Pro access</button></section>)}
        {isInternal && page === 'research' && <DailyTicketsPage key={`${date}-${page}`} date={date} initialTab="odds-policy" onOpenMatch={navigateToMatch} />}
        {page === 'match' && matchId != null && (isInternal || authLoading) && <MatchIntelligencePage matchId={matchId} onBack={() => navigate('tickets')} />}
        {isInternal && page === 'tracker' && <TrackerPage onOpenTickets={(targetDate) => { if (targetDate) setDate(targetDate); goTo('tickets') }} />}
        {isInternal && page === 'analytics' && <AnalyticsPage />}
        {isInternal && page === 'tools' && <ToolsPage />}
        {isInternal && page === 'admin' && <AdminPage />}
        </Suspense>
      </main>


      <nav className={`bottom-nav ${isInternal ? 'internal' : 'public'}`} aria-label="Primary navigation">
        {(isInternal ? [...NAV, ...INTERNAL_NAV] : NAV).map(item => (
          <button
            key={item.id}
            className={page === item.id ? 'active' : ''}
            onClick={() => goTo(item.id)}
            aria-current={page === item.id ? 'page' : undefined}
          >
            <span aria-hidden="true"><NavIcon name={NAV_META[item.id].icon} /></span>
            {item.label}
          </button>
        ))}
      </nav>
    </div>
  )
}

function routePage(pathname: string): Page {
  const path = pathname.replace(/\/$/, '')
  if (path === '/login') return 'login'
  if (path === '/upgrade') return 'upgrade'
  if (/^\/match\/\d+$/.test(path)) return 'match'
  if (path === '/tickets' || path === '/matches' || path === '/live' || path === '/insights' || path === '/research') return path.slice(1) as ModulePage
  if (path === '/tracker' || path === '/analytics' || path === '/tools' || path === '/admin') return path.slice(1) as ModulePage
  return 'tickets'
}

function isInternalPage(page: Page): boolean {
  return page === 'research' || page === 'tracker' || page === 'analytics' || page === 'tools' || page === 'admin' || page === 'match'
}

function routeMatchId(pathname: string): number | null {
  const match = pathname.replace(/\/$/, '').match(/^\/match\/(\d+)$/)
  return match ? Number(match[1]) : null
}

