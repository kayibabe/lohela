import { useState } from 'react'
import { useAuth } from '../auth'

export default function LoginPage({ onSuccess }: { onSuccess: () => void }) {
  const { login, register, error } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>(() => new URLSearchParams(window.location.search).get('mode') === 'register' ? 'register' : 'login')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    const success = mode === 'login' ? await login(email, password) : await register(username, email, password)
    setBusy(false)
    if (success) onSuccess()
  }

  return (
    <section className="auth-page" aria-labelledby="auth-page-title">
      <div className="auth-card">
        <div className="auth-card-body">
        <div className="auth-form-panel">
          <a className="auth-card-brand" href="/" aria-label="Lohela home">
            <img src="/lohela-primary.png" alt="Lohela — Intelligence beyond numbers" />
          </a>
          <h2 id="auth-page-title">{mode === 'login' ? 'Welcome back' : 'Create your account'}</h2>
          <p>{mode === 'login' ? 'Sign in to your Lohela workspace.' : 'Create an account to get started with Lohela.'}</p>
          <form onSubmit={submit}>
            {mode === 'register' && <label>Username<input autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} required /></label>}
            <label>Email<input type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} required /></label>
            <label>Password<input type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} minLength={6} value={password} onChange={event => setPassword(event.target.value)} required /></label>
            {error && <div className="auth-error" role="alert" aria-live="assertive">{error}</div>}
            <button className="btn-primary" type="submit" disabled={busy}>{busy ? 'Working…' : mode === 'login' ? 'Sign in' : 'Create account'}</button>
          </form>
          <button className="btn-ghost auth-switch" type="button" onClick={() => setMode(value => value === 'login' ? 'register' : 'login')}>
            {mode === 'login' ? 'Need an account? Create one' : 'Already have an account? Sign in'}
          </button>
          <a className="auth-home-link" href="/">Back to home</a>
        </div>
        </div>
      </div>
    </section>
  )
}
