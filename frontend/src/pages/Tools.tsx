import { useState } from 'react'
import { fmt, fmtPnl } from '../utils/currency'

// ── Kelly Criterion ───────────────────────────────────────────────────────────

function KellyCalculator() {
  const [odds, setOdds] = useState('')
  const [prob, setProb] = useState('')
  const [bankroll, setBankroll] = useState('')
  const [fraction, setFraction] = useState<'full' | 'half' | 'quarter'>('half')

  const o = parseFloat(odds)
  const p = parseFloat(prob) / 100
  const b = parseFloat(bankroll)

  let kelly = NaN
  if (o > 1 && p > 0 && p < 1) {
    kelly = (p * (o - 1) - (1 - p)) / (o - 1)
  }

  const fractionMap = { full: 1, half: 0.5, quarter: 0.25 }
  const adjKelly = isNaN(kelly) ? NaN : kelly * fractionMap[fraction]
  const stakeAmount = adjKelly > 0 && b > 0 ? adjKelly * b : NaN

  return (
    <div className="tool-card">
      <h3 className="tool-title">Kelly Criterion Staking</h3>
      <p className="tool-desc">Calculates the optimal stake fraction based on your edge.</p>
      <div className="tool-inputs">
        <div className="form-group">
          <label>Decimal Odds</label>
          <input type="number" step="0.01" min="1.01" value={odds} onChange={e => setOdds(e.target.value)} placeholder="e.g. 2.50" />
        </div>
        <div className="form-group">
          <label>Model Probability (%)</label>
          <input type="number" step="0.1" min="1" max="99" value={prob} onChange={e => setProb(e.target.value)} placeholder="e.g. 55" />
        </div>
        <div className="form-group">
          <label>Bankroll</label>
          <input type="number" step="1" min="1" value={bankroll} onChange={e => setBankroll(e.target.value)} placeholder="e.g. 1000" />
        </div>
        <div className="form-group">
          <label>Fraction</label>
          <select value={fraction} onChange={e => setFraction(e.target.value as typeof fraction)}>
            <option value="full">Full Kelly</option>
            <option value="half">Half Kelly (recommended)</option>
            <option value="quarter">Quarter Kelly (conservative)</option>
          </select>
        </div>
      </div>
      {!isNaN(kelly) && (
        <div className="tool-result">
          {kelly <= 0 ? (
            <div className="result-neg">No edge — Kelly says don't bet.</div>
          ) : (
            <div className="result-grid">
              <div className="result-item">
                <div className="result-label">Full Kelly %</div>
                <div className="result-value">{(kelly * 100).toFixed(2)}%</div>
              </div>
              <div className="result-item highlight">
                <div className="result-label">{fraction === 'full' ? 'Full' : fraction === 'half' ? 'Half' : 'Quarter'} Kelly %</div>
                <div className="result-value">{(adjKelly * 100).toFixed(2)}%</div>
              </div>
              {!isNaN(stakeAmount) && (
                <div className="result-item highlight">
                  <div className="result-label">Recommended Stake</div>
                  <div className="result-value">{fmt(stakeAmount)}</div>
                </div>
              )}
              <div className="result-item">
                <div className="result-label">Edge</div>
                <div className={`result-value ${kelly > 0 ? 'positive' : 'negative'}`}>
                  {((p * (o - 1) - (1 - p)) * 100).toFixed(2)}%
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Accumulator Builder ───────────────────────────────────────────────────────

interface Leg { label: string; odds: string; key: number }

function AccaBuilder() {
  const [legs, setLegs] = useState<Leg[]>([
    { label: '', odds: '', key: 0 },
    { label: '', odds: '', key: 1 },
  ])
  const [stake, setStake] = useState('')
  let nextKey = legs.length

  const addLeg = () => setLegs(l => [...l, { label: '', odds: '', key: nextKey++ }])
  const removeLeg = (key: number) => setLegs(l => l.filter(x => x.key !== key))
  const updateLeg = (key: number, field: 'label' | 'odds', val: string) =>
    setLegs(l => l.map(x => x.key === key ? { ...x, [field]: val } : x))

  const parsedOdds = legs.map(l => parseFloat(l.odds)).filter(n => n > 1)
  const complete = legs.length >= 2 && parsedOdds.length === legs.length
  const combinedOdds = complete ? parsedOdds.reduce((a, b) => a * b, 1) : NaN
  const s = parseFloat(stake)
  const potReturn = !isNaN(combinedOdds) && s > 0 ? combinedOdds * s : NaN

  return (
    <div className="tool-card">
      <h3 className="tool-title">Accumulator Builder</h3>
      <p className="tool-desc">Calculate combined odds and potential return for any number of legs.</p>
      <div className="acca-legs">
        {legs.map((leg, i) => (
          <div key={leg.key} className="acca-leg-row">
            <span className="leg-num">{i + 1}</span>
            <input className="leg-label-input" value={leg.label} onChange={e => updateLeg(leg.key, 'label', e.target.value)} placeholder="Selection (optional)" />
            <input className="leg-odds-input" type="number" step="0.01" min="1.01" value={leg.odds} onChange={e => updateLeg(leg.key, 'odds', e.target.value)} placeholder="Odds" />
            {legs.length > 2 && (
              <button className="btn-sm btn-danger" onClick={() => removeLeg(leg.key)}>×</button>
            )}
          </div>
        ))}
      </div>
      <div className="acca-controls">
        <button className="btn-ghost btn-sm" onClick={addLeg}>+ Add Leg</button>
        <div className="form-group" style={{ marginBottom: 0 }}>
          <input type="number" step="0.01" min="0.01" value={stake} onChange={e => setStake(e.target.value)} placeholder="Stake" style={{ width: 100 }} />
        </div>
      </div>
      {!complete && parsedOdds.length > 0 && <div className="tool-validation-warning">Enter valid odds above 1.00 for every leg before calculating.</div>}
      {!isNaN(combinedOdds) && (
        <div className="tool-result">
          <div className="result-grid">
            <div className="result-item">
              <div className="result-label">Legs</div>
              <div className="result-value">{parsedOdds.length}</div>
            </div>
            <div className="result-item highlight">
              <div className="result-label">Combined Odds</div>
              <div className="result-value">{combinedOdds.toFixed(2)}</div>
            </div>
            {!isNaN(potReturn) && (
              <div className="result-item highlight">
              <div className="result-label">Potential Return (incl. stake)</div>
                <div className="result-value">{fmt(potReturn)}</div>
              </div>
            )}
            {!isNaN(potReturn) && (
              <div className="result-item">
                <div className="result-label">Profit</div>
                <div className="result-value positive">{fmtPnl(potReturn - s)}</div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Implied Probability ───────────────────────────────────────────────────────

function ImpledProbability() {
  const [odds, setOdds] = useState('')
  const o = parseFloat(odds)
  const impliedProb = o > 1 ? (1 / o) * 100 : NaN
  const breakEvenProb = impliedProb

  return (
    <div className="tool-card">
      <h3 className="tool-title">Odds Converter</h3>
      <p className="tool-desc">Convert decimal odds to implied probability and break-even win rate.</p>
      <div className="tool-inputs">
        <div className="form-group">
          <label>Decimal Odds</label>
          <input type="number" step="0.01" min="1.01" value={odds} onChange={e => setOdds(e.target.value)} placeholder="e.g. 2.50" />
        </div>
      </div>
      {!isNaN(impliedProb) && (
        <div className="tool-result">
          <div className="result-grid">
            <div className="result-item highlight">
              <div className="result-label">Implied Probability</div>
              <div className="result-value">{impliedProb.toFixed(2)}%</div>
            </div>
            <div className="result-item">
              <div className="result-label">Break-even Win Rate</div>
              <div className="result-value">{breakEvenProb.toFixed(2)}%</div>
            </div>
            <div className="result-item">
              <div className="result-label">Fractional</div>
              <div className="result-value">{(o - 1).toFixed(2)}/1</div>
            </div>
            <div className="result-item">
              <div className="result-label">American Odds</div>
              <div className="result-value">
                {o >= 2 ? `+${((o - 1) * 100).toFixed(0)}` : `-${(100 / (o - 1)).toFixed(0)}`}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Expected Value ────────────────────────────────────────────────────────────

function EVCalculator() {
  const [odds, setOdds] = useState('')
  const [prob, setProb] = useState('')
  const [stake, setStake] = useState('')

  const o = parseFloat(odds)
  const p = parseFloat(prob) / 100
  const s = parseFloat(stake) || 1

  let ev = NaN
  if (o > 1 && p > 0 && p < 1) {
    ev = p * (o - 1) * s - (1 - p) * s
  }

  return (
    <div className="tool-card">
      <h3 className="tool-title">Expected Value</h3>
      <p className="tool-desc">Calculate the expected value of a bet given your estimated probability.</p>
      <div className="tool-inputs">
        <div className="form-group">
          <label>Decimal Odds</label>
          <input type="number" step="0.01" min="1.01" value={odds} onChange={e => setOdds(e.target.value)} placeholder="e.g. 2.50" />
        </div>
        <div className="form-group">
          <label>Your Probability (%)</label>
          <input type="number" step="0.1" min="1" max="99" value={prob} onChange={e => setProb(e.target.value)} placeholder="e.g. 55" />
        </div>
        <div className="form-group">
          <label>Stake</label>
          <input type="number" step="1" min="1" value={stake} onChange={e => setStake(e.target.value)} placeholder="e.g. 10" />
        </div>
      </div>
      {!isNaN(ev) && (
        <div className="tool-result">
          <div className="result-grid">
            <div className={`result-item highlight ${ev > 0 ? 'positive' : 'negative'}`}>
              <div className="result-label">Expected Value</div>
              <div className="result-value">{fmtPnl(ev)}</div>
            </div>
            <div className="result-item">
              <div className="result-label">EV per unit</div>
              <div className={`result-value ${ev > 0 ? 'positive' : 'negative'}`}>
                {(ev / s >= 0 ? '+' : '')}{(ev / s * 100).toFixed(2)}%
              </div>
            </div>
            <div className="result-item">
              <div className="result-label">Verdict</div>
              <div className={`result-value ${ev > 0 ? 'positive' : 'negative'}`}>
                {ev > 0 ? '+EV — bet has value' : '−EV — no value'}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ToolsPage() {
  return (
    <div className="page-content">
      <div className="section-header">
        <h2 className="section-title">Betting Tools</h2>
        <span className="section-subtitle">Staking calculators and utilities</span>
      </div>
      <div className="tools-note"><strong>What-if calculators</strong><span>These tools are for independent analysis only and do not create, edit, or settle paper-ledger records.</span></div>
      <div className="tools-grid">
        <KellyCalculator />
        <AccaBuilder />
        <ImpledProbability />
        <EVCalculator />
      </div>
    </div>
  )
}
