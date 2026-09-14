import { addDays, formatDate, todayISO } from '../utils'

interface Props {
  date: string
  onChange: (date: string) => void
  loading: boolean
}

export function DateNav({ date, onChange, loading }: Props) {
  const today = todayISO()
  const isToday = date === today

  return (
    <div className="date-nav">
      <button
        className="date-btn"
        onClick={() => onChange(addDays(date, -1))}
        disabled={loading}
        aria-label="Previous day"
      >
        ‹
      </button>
      <div className="date-center">
        <input
          type="date"
          className="date-input"
          value={date}
          onChange={e => onChange(e.target.value)}
          disabled={loading}
        />
        <span className="date-label">{formatDate(date)}</span>
        {!isToday && (
          <button
            className="today-btn"
            onClick={() => onChange(today)}
            disabled={loading}
          >
            Today
          </button>
        )}
      </div>
      <button
        className="date-btn"
        onClick={() => onChange(addDays(date, 1))}
        disabled={loading}
        aria-label="Next day"
      >
        ›
      </button>
    </div>
  )
}
