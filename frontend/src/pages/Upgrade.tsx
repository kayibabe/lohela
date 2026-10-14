import { useAuth } from '../auth'

export default function UpgradePage({ onBack }: { onBack: () => void }) {
  const { user } = useAuth()
  const hasProAccess = user?.plan === 'pro' || user?.role === 'admin'
  return (
    <section className="auth-page" aria-labelledby="upgrade-title">
      <div className="auth-card">
        <span className="eyebrow">Pro module</span>
        <h1 id="upgrade-title">{hasProAccess ? 'Your Pro access is active' : 'Unlock Pro features'}</h1>
        <p>{hasProAccess ? `Your ${user?.plan === 'pro' ? 'Pro' : 'admin'} account includes Pro features.` : 'Your Free account includes all Free features. Pro adds detailed odds and expanded ticket history.'}</p>
        <button className="btn-primary" onClick={onBack}>Return to tickets</button>
      </div>
    </section>
  )
}
