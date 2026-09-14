import { useState } from 'react'
import { triggerModelRun } from '../api'

interface Props {
  date: string
  onComplete: () => void
  onClose: () => void
}

export function RunModelModal({ date, onComplete, onClose }: Props) {
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleRun() {
    setRunning(true)
    setError(null)
    try {
      const r = await triggerModelRun(date)
      setResult(`${r.predictions} predictions written.`)
      setTimeout(() => {
        onComplete()
        onClose()
      }, 1500)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <h2 className="modal-title">Run Model</h2>
        <p className="modal-body">
          Generate predictions for <strong>{date}</strong>.<br />
          This fits Poisson-DC on 2000 historical matches and scores every eligible fixture.
          Takes ~10–30 seconds.
        </p>
        {error && <p className="modal-error">{error}</p>}
        {result && <p className="modal-success">{result}</p>}
        <div className="modal-actions">
          <button className="btn-secondary" onClick={onClose} disabled={running}>Cancel</button>
          <button className="btn-primary" onClick={handleRun} disabled={running || !!result}>
            {running ? 'Running…' : 'Run now'}
          </button>
        </div>
      </div>
    </div>
  )
}
