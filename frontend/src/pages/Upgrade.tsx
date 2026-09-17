import { useAuth } from '../auth'

export default function UpgradePage({ onBack }: { onBack: () => void }) {
  const { user } = useAuth()
  return (
    <section className="auth-page" aria-labelledby="upgrade-title">
      <div className="auth-card">
        <span className="eyebrow">Pro module</span>
        <h1 id="upgrade-title">This module is available to Pro users</h1>
        <p>{user ? `You are signed in as ${user.email} on the Free plan.` : 'Sign in to continue.'}</p>
        <button className="btn-primary" onClick={onBack}>Return to tickets</button>
      </div>
    </section>
  )
}
