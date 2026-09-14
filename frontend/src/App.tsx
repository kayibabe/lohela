import { useState, type ReactNode } from 'react'
import './App.css'
import DailyTicketsPage, { TODAY } from './pages/DailyTickets'
import { addDays } from './utils'
import TrackerPage from './pages/Tracker'
import AnalyticsPage from './pages/Analytics'
import ToolsPage from './pages/Tools'
import AdminPage from './pages/Admin'

type Theme = 'dark' | 'light' | 'system'
type Page = 'tickets' | 'tracker' | 'analytics' | 'tools' | 'admin'

const NAV: { id: Page; label: string }[] = [
  { id: 'tickets',   label: 'Tickets'   },
  { id: 'tracker',   label: 'Tracker'   },
  { id: 'analytics', label: 'Analytics' },
  { id: 'tools',     label: 'Tools'     },
  { id: 'admin',     label: 'Admin'     },
]

const NAV_META: Record<Page, { icon: string; hint: string }> = {
  tickets: { icon: 'sparkles', hint: 'Daily published research' },
  tracker: { icon: 'list', hint: 'System evidence and confirmed journal' },
  analytics: { icon: 'chart', hint: 'Performance and calibration' },
  tools: { icon: 'wrench', hint: 'Research utilities' },
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
  const [page, setPage] = useState<Page>('tickets')
  const [date, setDate] = useState(TODAY)
  const [theme, setTheme] = useState<Theme>('system')
  const [lastUpdated, setLastUpdated] = useState(() => new Date())

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
        <a className="brand" href="/" aria-label="Lohela home" onClick={(event) => { event.preventDefault(); setPage('tickets') }}>
          <img className="brand-logo" src="/lohela-logo.svg" alt="" />
          <span className="brand-copy">
            <span className="brand-name">Lohela</span>
            <span className="brand-tagline">Intelligence beyond numbers</span>
          </span>
        </a>
        <nav className="sidebar-nav" aria-label="Primary navigation">
          <span className="sidebar-label">Workspace</span>
          {NAV.slice(0, 4).map(n => (
            <button key={n.id} className={`nav-tab${page === n.id ? ' active' : ''}`} onClick={() => setPage(n.id)} title={NAV_META[n.id].hint}>
              <span className="nav-icon"><NavIcon name={NAV_META[n.id].icon} /></span>{n.label}
            </button>
          ))}
          <span className="sidebar-label sidebar-label-admin">System</span>
          <button className={`nav-tab${page === 'admin' ? ' active' : ''}`} onClick={() => setPage('admin')} title={NAV_META.admin.hint}>
            <span className="nav-icon"><NavIcon name={NAV_META.admin.icon} /></span>Admin
          </button>
        </nav>
      </aside>
      <header className="app-header">
        <div className="main-header-status">
          <span className="research-status"><span className="status-dot" /> Paper research</span>
          <button className="theme-btn" onClick={cycleTheme} title={`Theme: ${theme}`}>
            <span className="theme-auto">{theme === 'system' ? 'Auto' : theme}</span>
          </button>
        </div>
        {page !== 'tickets' && <div className="global-page-context"><strong>{NAV.find(item => item.id === page)?.label}</strong><span>{NAV_META[page].hint}</span></div>}
        <span className="global-updated">Updated {lastUpdated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
        <button className="global-refresh" onClick={() => { setLastUpdated(new Date()); window.location.reload() }} title="Refresh current page" aria-label="Refresh current page">â†» <span>Refresh</span></button>
        {page === 'tickets' && (
          <div className="date-nav">
            <button className="date-btn" onClick={() => nav(-1)} title="Previous day">â€¹</button>
            <div className="date-center">
              <input
                className="date-input"
                type="date"
                value={date}
                onChange={e => setDate(e.target.value)}
              />
              <button className="today-btn" onClick={() => setDate(TODAY)}>Today</button>
            </div>
            <button className="date-btn" onClick={() => nav(1)} title="Next day">â€º</button>
          </div>
        )}

      </header>

      <main className="main">
        {page === 'tickets'   && <DailyTicketsPage key={date} date={date} />}
        {page === 'tracker'   && <TrackerPage onOpenTickets={(targetDate) => { if (targetDate) setDate(targetDate); setPage('tickets') }} />}
        {page === 'analytics' && <AnalyticsPage />}
        {page === 'tools'     && <ToolsPage />}
        {page === 'admin'     && <AdminPage />}
      </main>


      <nav className="bottom-nav" aria-label="Primary navigation">
        {NAV.map(item => (
          <button
            key={item.id}
            className={page === item.id ? 'active' : ''}
            onClick={() => setPage(item.id)}
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
