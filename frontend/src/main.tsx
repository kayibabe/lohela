import { Component, StrictMode, type ErrorInfo, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { AuthProvider } from './auth'

interface ErrorBoundaryState { hasError: boolean }

class AppErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the user-facing fallback deterministic while retaining a useful
    // diagnostic in the browser console for local/support investigation.
    console.error('Lohela workspace render failure', error, info)
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="app-fatal-error" role="alert" aria-labelledby="app-fatal-error-title">
          <div className="app-fatal-card">
            <span className="eyebrow">Workspace recovery</span>
            <h1 id="app-fatal-error-title">The workspace needs to reload</h1>
            <p>A display component failed before the workspace could be shown. Your saved research data was not changed.</p>
            <button className="btn-primary" type="button" onClick={() => window.location.reload()}>Reload workspace</button>
          </div>
        </main>
      )
    }
    return this.props.children
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppErrorBoundary><AuthProvider><App /></AuthProvider></AppErrorBoundary>
  </StrictMode>,
)
