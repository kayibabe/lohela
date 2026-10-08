import { useEffect, useState, type ReactNode } from 'react'
import './App.css'
import DailyTicketsPage, { TODAY } from './pages/DailyTickets'
import { addDays } from './utils'
import TrackerPage from './pages/Tracker'
import AnalyticsPage from './pages/Analytics'
import ToolsPage from './pages/Tools'
import AdminPage from './pages/Admin'
import UpgradePage from './pages/Upgrade'
import MatchIntelligencePage from './pages/MatchIntelligencePage'
import { AuthControls } from './auth'

type Theme = 'dark' | 'light' | 'system'
type ModulePage = 'tickets' | 'tracker' | 'analytics' | 'tools' | 'admin'
type Page = ModulePage | 'match' | 'upgrade'

// Keep primary navigation aligned to the user's decision loop. Utilities
// remain routable for backwards compatibility, but are not a peer destination
// to recommendations, evidence, and validation.
const NAV: { id: ModulePage; label: string }[] = [
  { id: 'tickets',   label: 'Today'     },
  { id: 'tracker',   label: 'Track'     },
  { id: 'analytics', label: 'Validate'  },
]

const NAV_META: Record<ModulePage, { icon: string; hint: string }> = {
  tickets: { icon: 'sparkles', hint: 'Today’s published research' },
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
    wrench: <><path d="m14.7 6.3 3-3a5 5 0 0 0-6.4 6.4L3 18a2 2 0 1 0 3 3l8.3-8.3a5 5 0 0 0 6.4-6.4l-3 3-3-3Z" /></>,
    shield: <><path d="M12 3 20 6v5c0 5-3.4 8.6-8 10-4.6-1.4-8-5-8-10V6l8-3Z" /><path d="m9 12 2 2 4-4" /></>,
  }
  return <svg className="nav-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

export default function App() {
  const [page, setPage] = useState<Page>(() => routePage(window.location.pathname))
  const [matchId, setMatchId] = useState<number | null>(() => routeMatchId(window.location.pathname))
  const [date, setDate] = useState(TODAY)
  const [theme, setTheme] = useState<Theme>('system')
  const [lastUpdated, setLastUpdated] = useState(() => new Date())

  useEffect(() => {
    const onPopState = () => { setPage(routePage(window.location.pathname)); setMatchId(routeMatchId(window.location.pathname)) }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  function navigate(next: Page, _requested?: ModulePage, replace = false) {
    setPage(next)
    const path = next === 'upgrade' ? '/upgrade' : `/${next}`
    window.history[replace ? 'replaceState' : 'pushState']({}, '', path)
  }

  function navigateToMatch(nextMatchId: number) {
    setMatchId(nextMatchId)
    setPage('match')
    window.history.pushState({}, '', `/match/${nextMatchId}`)
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

  return (
    <div className="app">
      <aside className="app-sidebar">
        <a className="brand" href="/" aria-label="Lohela home" onClick={(event) => { event.preventDefault(); goTo('tickets') }}>
          <img className="brand-logo" src="/lohela-compact.png" alt="Lohela — Intelligence beyond numbers" />
        </a>
        <nav className="sidebar-nav" aria-label="Primary navigation">
          <span className="sidebar-label">Workspace</span>
          {NAV.slice(0, 4).map(n => (
            <button key={n.id} className={`nav-tab${page === n.id ? ' active' : ''}`} onClick={() => goTo(n.id)} title={NAV_META[n.id].hint}>
              <span className="nav-icon"><NavIcon name={NAV_META[n.id].icon} /></span>{n.label}
            </button>
          ))}
          <span className="sidebar-label sidebar-label-admin">System</span>
          <button className={`nav-tab${page === 'admin' ? ' active' : ''}`} onClick={() => goTo('admin')} title={NAV_META.admin.hint}>
            <span className="nav-icon"><NavIcon name={NAV_META.admin.icon} /></span>Admin
          </button>
        </nav>
        <div className="sidebar-account">
          <AuthControls onSignedOut={() => navigate('tickets', undefined, true)} />
        </div>
      </aside>
      <header className="app-header">
        <div className="header-page-title">
        <span className="research-status"><span className="status-dot" /> {page === 'tickets' ? 'Decision workspace' : page === 'match' ? 'Match intelligence' : page === 'upgrade' ? 'System' : NAV_META[page].hint}</span>
          <button className="theme-btn" onClick={cycleTheme} title={`Theme: ${theme}`} aria-label={`Theme: ${theme}. Change theme`}>
            <span className="theme-auto">{theme === 'system' ? 'Auto' : theme}</span>
          </button>
        </div>
        <div className="header-refresh">
          <span className="global-updated">Refreshed {lastUpdated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
          <button className="global-refresh" onClick={() => { setLastUpdated(new Date()); window.location.reload() }} title="Refresh current page" aria-label="Refresh current page">↻ <span>Refresh</span></button>
        </div>
        {page === 'tickets' && (
          <div className="date-nav">
            <button className="date-btn" onClick={() => nav(-1)} title="Previous day" aria-label="Previous day">‹</button>
            <div className="date-center">
              <input
                className="date-input"
                type="date"
                value={date}
                onChange={e => setDate(e.target.value)}
              />
              <button className="today-btn" onClick={() => setDate(TODAY)}>Today</button>
            </div>
            <button className="date-btn" onClick={() => nav(1)} title="Next day" aria-label="Next day">›</button>
          </div>
        )}

      </header>

      <main className="main" id="main-content" tabIndex={-1}>
        {page === 'upgrade' && <UpgradePage onBack={() => goTo('tickets')} />}
        {page === 'tickets' && <DailyTicketsPage key={date} date={date} onOpenMatch={navigateToMatch} />}
        {page === 'match' && matchId != null && <MatchIntelligencePage matchId={matchId} onBack={() => navigate('tickets')} />}
        {page === 'tracker' && <TrackerPage onOpenTickets={(targetDate) => { if (targetDate) setDate(targetDate); goTo('tickets') }} />}
        {page === 'analytics' && <AnalyticsPage />}
        {page === 'tools' && <ToolsPage />}
        {page === 'admin' && <AdminPage />}
      </main>


      <nav className="bottom-nav" aria-label="Primary navigation">
        {NAV.map(item => (
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
  if (path === '/login') return 'tickets'
  if (path === '/upgrade') return 'upgrade'
  if (/^\/match\/\d+$/.test(path)) return 'match'
  if (path === '/tickets') return 'tickets'
  if (path === '/tracker' || path === '/analytics' || path === '/tools' || path === '/admin') return path.slice(1) as ModulePage
  return 'tickets'
}

function routeMatchId(pathname: string): number | null {
  const match = pathname.replace(/\/$/, '').match(/^\/match\/(\d+)$/)
  return match ? Number(match[1]) : null
}
