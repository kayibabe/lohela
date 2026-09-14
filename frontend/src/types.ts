export type { Leg, Ticket, DailyTickets } from './lib/api'

export interface Prediction {
  id: number
  match_id: number
  market: string
  selection: string
  model_probability: number
  q_score: number
  q_grade: string
  edge: number | null
  expected_value: number | null
}

export interface TopPrediction extends Prediction {
  home_team: string
  away_team: string
  competition: string
  kickoff_at: string
  best_odds: number | null
}
