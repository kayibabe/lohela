import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export interface CurrentUser {
  id: number
  username: string
  email: string
  role: 'user' | 'admin'
  plan: 'free' | 'pro'
  account_status: 'active' | 'suspended' | 'pending'
}

interface AuthContextValue {
  user: CurrentUser | null
  loading: boolean
  error: string | null
  login: (email: string, password: string) => Promise<boolean>
  register: (username: string, email: string, password: string) => Promise<boolean>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

async function parseResponse(response: Response): Promise<{ user?: CurrentUser; detail?: string }> {
  return response.json().catch(() => ({}))
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/v1/auth/me')
      .then(async response => response.ok ? (await parseResponse(response)).user ?? null : null)
      .then(setUser)
      .catch(() => setError('The authentication service is unavailable. Please try again.'))
      .finally(() => setLoading(false))
  }, [])

  async function authenticate(path: 'login' | 'register', username: string | undefined, email: string, password: string) {
    setError(null)
    try {
      const response = await fetch(`/api/v1/auth/${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...(username ? { username } : {}), email, password }),
      })
      const data = await parseResponse(response)
      if (!response.ok || !data.user) {
        setError(data.detail ?? 'Authentication failed')
        return false
      }
      setUser(data.user)
      return true
    } catch {
      setError('Unable to reach the authentication service. Check your connection and try again.')
      return false
    }
  }

  async function login(email: string, password: string) { return authenticate('login', undefined, email, password) }
  async function register(username: string, email: string, password: string) { return authenticate('register', username, email, password) }

  async function logout() {
    try {
      await fetch('/api/v1/auth/logout', { method: 'POST' })
    } finally {
      // Clear local identity even when the server is temporarily unavailable.
      // This prevents a stale UI session from surviving a failed sign-out.
      setUser(null)
    }
  }

  const value = useMemo(() => ({ user, loading, error, login, register, logout }), [user, loading, error])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}

export function AuthControls({ onSignedOut }: { onSignedOut?: () => void }) {
  const { user, loading, error, login, register, logout } = useAuth()
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  if (loading) return <span className="auth-status">Checking session…</span>
  if (user) {
    return (
      <div className="auth-session">
        <span className="auth-status">{user.plan === 'pro' ? 'Pro' : 'Free'} · {user.email}</span>
        <button className="theme-btn auth-signout" onClick={async () => { await logout(); onSignedOut?.() }}>Sign out</button>
      </div>
    )
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const ok = mode === 'login' ? await login(email, password) : await register(username, email, password)
    if (ok) { setOpen(false); setPassword('') }
  }

  return (
    <div className="auth-control">
      <button className="theme-btn" onClick={() => setOpen(value => !value)}>{mode === 'login' ? 'Sign in' : 'Create account'}</button>
      {open && (
        <form className="auth-popover" onSubmit={submit}>
          <strong>{mode === 'login' ? 'Sign in to Lohela' : 'Create a free account'}</strong>
          {mode === 'register' && <input aria-label="Username" autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} placeholder="Username" required />}
          <input aria-label="Email" type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} placeholder="Email" required />
          <input aria-label="Password" type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={event => setPassword(event.target.value)} placeholder="Password (6+ characters)" minLength={6} required />
          {error && <span className="auth-error" role="alert">{error}</span>}
          <button className="btn-primary" type="submit">{mode === 'login' ? 'Sign in' : 'Register'}</button>
          <button className="btn-ghost" type="button" onClick={() => setMode(value => value === 'login' ? 'register' : 'login')}>
            {mode === 'login' ? 'Need an account?' : 'Already have an account?'}
          </button>
        </form>
      )}
    </div>
  )
}
