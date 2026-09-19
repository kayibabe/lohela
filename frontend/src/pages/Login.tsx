import { useState } from 'react'
import { useAuth } from '../auth'

export default function LoginPage({ onSuccess }: { onSuccess: () => void }) {
  const { login, register, error } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
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
        <div className="auth-benefits">
          <span className="auth-kicker">Lohela intelligence</span>
          <h1>Make every match<br /><em>more readable.</em></h1>
          <p>One workspace for disciplined football research, evidence, and decisions you can explain.</p>
          <div className="auth-benefit-list">
            <span><b>01</b> Daily published research</span>
            <span><b>02</b> Evidence-led performance views</span>
            <span><b>03</b> Access matched to your plan</span>
          </div>
        </div>
        <div className="auth-form-panel">
          <a className="auth-card-brand" href="/" aria-label="Lohela home">
            <img src="/lohela-logo.svg" alt="" />
            <span><small>Intelligence beyond numbers</small></span>
          </a>
          <h2 id="auth-page-title">{mode === 'login' ? 'Sign in to Lohela' : 'Create your free account'}</h2>
          <p>{mode === 'login' ? 'Continue to your research workspace.' : 'Start with the free research view. Upgrade access can be applied by an administrator.'}</p>
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
        </div>
        </div>
      </div>
    </section>
  )
}
