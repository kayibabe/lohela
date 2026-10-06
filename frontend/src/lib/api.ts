export interface Leg {
  selection_id: number
  prediction_id: number
  match_id: number
  home_team: string
  away_team: string
  competition: string
  kickoff_at: string
  market: string
  selection: string
  model_probability: number | null
  model_agreement?: number | null
  best_odds: number | null
  q_score: number | null
  q_grade: string | null
  edge: number | null
  expected_value: number | null
  source_odds_at: string | null
  result: string
  match_status?: string
  home_goals?: number | null
  away_goals?: number | null
  live_phase?: string | null
  elapsed_minutes?: number | null
  selection_settled_at?: string | null
  locked_fields?: string[]
}

export interface Ticket {
  ticket_id: number
  ticket_type: string
  name: string
  status: string
  version: number
  legs: Leg[]
  combined_odds: number | null
  combined_probability: number | null
  adjusted_probability: number | null
  correlation_penalty: number | null
  expected_value: number | null
  risk_score: number | null
  confidence_score: number | null
  avg_q_score: number | null
  avg_edge: number | null
  model_version: string
  published_at: string
  publication_hash: string
  relaxed_tier: boolean
  relaxation_level: number
  /** >0 when a thin day's ticket includes fixtures up to this many days later. */
  horizon_days?: number
  /** 'market': priced at the bookmaker's fair (de-vigged) odds, no value claimed. */
  pricing?: 'market' | 'model'
  internal_only: boolean
  result: string | null
  profit_loss: number | null
  settled_at?: string | null
  settlement_source?: string | null
  settlement_version?: number | null
  locked_fields?: string[]
}

export interface DailyTickets {
  target_date: string
  qualified_pool: number
  generation_id: number | null
  generation_status: string | null
  generated_ticket_count: number
  missing_public_ticket_types: string[]
  pricing?: 'market' | 'model'
  pipeline_run_id: number | null
  pipeline_status: string | null
  pipeline_current_stage: string | null
  pipeline_trigger_source: string | null
  pipeline_started_at: string | null
  pipeline_completed_at: string | null
  pipeline_error: string | null
  conservative: Ticket | null
  balanced: Ticket | null
  high_odds: Ticket | null
  best_value: Ticket | null
  superseded_versions: TicketHistoryItem[]
}

export interface TicketHistoryItem {
  ticket_id: number
  target_date: string
  ticket_type: string
  name: string
  status: string
  version: number
  leg_count: number
  combined_odds: number
  adjusted_probability: number
  risk_score: number
  avg_q_score: number
  model_version: string
  published_at: string
  publication_hash: string
  relaxed_tier: boolean
  internal_only: boolean
  result: string | null
  stake: number | null
  return_amount: number | null
  profit_loss: number | null
  settled_at: string | null
}

export async function fetchTicketHistory(
  includeSuperseded = false,
  includeInternal = false,
): Promise<TicketHistoryItem[]> {
  const params = new URLSearchParams({
    limit: '200',
    include_superseded: String(includeSuperseded),
    include_internal: String(includeInternal),
  })
  const res = await fetch(`/api/v1/tickets/history?${params}`)
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export async function fetchTicket(ticketId: number): Promise<Ticket> {
  const res = await fetch(`/api/v1/tickets/${ticketId}`)
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export async function fetchDailyTickets(
  date: string,
): Promise<DailyTickets> {
  const params = new URLSearchParams({ date })
  const res = await fetch(`/api/v1/tickets/daily?${params}`)
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export interface SelectionSummary {
  prediction_id: number
  match_id: number
  home_team: string
  away_team: string
  competition: string
  kickoff_at: string
  market: string
  selection: string
  model_probability?: number
  model_agreement?: number | null
  q_score: number
  q_grade?: string
  edge: number | null
  source_ticket_id?: number | null
  source_ticket_type?: 'safe' | 'balanced' | 'high_odds' | 'best_value' | null
  source_ticket_version?: number | null
  source_conflict?: boolean
  expected_value?: number | null
  best_odds: number | null
  bookmaker_count?: number | null
  avg_implied?: number | null
  match_status?: string
  home_goals?: number | null
  away_goals?: number | null
  live_phase?: string | null
  elapsed_minutes?: number | null
  result?: 'won' | 'lost' | 'void' | null
  reason_codes?: string[]
}

export interface CustomAccumulatorLeg {
  id: number
  position: number
  prediction_id: number
  match_id: number
  home_team: string
  away_team: string
  competition: string
  kickoff_at: string
  market: string
  selection: string
  odds_snapshot: number
  probability_snapshot: number | null
  q_score_snapshot: number | null
  edge_snapshot: number | null
  source_ticket_id: number | null
  source_ticket_type: 'safe' | 'balanced' | 'high_odds' | 'best_value' | null
  source_ticket_version: number | null
  source_conflict: boolean
  result: 'pending' | 'won' | 'lost' | 'void'
  settled_at: string | null
}

export interface CustomAccumulator {
  id: number
  name: string
  target_date: string
  status: 'draft' | 'placed' | 'won' | 'lost' | 'void' | 'cashout'
  stake: number | null
  combined_odds: number
  potential_return: number | null
  actual_return: number | null
  created_at: string
  placed_at: string | null
  settled_at: string | null
  automatic: boolean
  source_ticket_snapshots?: Record<string, { ticket_id?: number | null; ticket_type?: string; version?: number | null; model_version?: string; publication_hash?: string; published_at?: string }>
  legs: CustomAccumulatorLeg[]
}

export async function fetchCustomAccumulators(date?: string): Promise<CustomAccumulator[]> {
  const query = date ? `?target_date=${encodeURIComponent(date)}` : ''
  const res = await fetch(`/api/v1/custom-accumulators${query}`)
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export async function createCustomAccumulator(payload: { name: string; target_date: string; prediction_ids: number[]; stake: number | null; status: 'draft' | 'placed' }): Promise<CustomAccumulator> {
  const res = await fetch('/api/v1/custom-accumulators', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? `API error ${res.status}`)
  return res.json()
}

export async function updateCustomAccumulator(id: number, payload: { name: string; target_date: string; prediction_ids: number[]; stake: number | null; status: 'draft' | 'placed' }): Promise<CustomAccumulator> {
  const res = await fetch(`/api/v1/custom-accumulators/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...payload }) })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? `API error ${res.status}`)
  return res.json()
}

export async function deleteCustomAccumulator(id: number): Promise<void> {
  const res = await fetch(`/api/v1/custom-accumulators/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? `API error ${res.status}`)
}

export interface OddsQuote {
  bookmaker: string
  decimal_odds: number
  implied_probability: number
  opening_odds: number | null
  movement: number | null
  fetched_at: string
  source_type: string
  is_fallback: boolean
  is_best: boolean
}

export async function fetchSelectionOdds(predictionId: number): Promise<OddsQuote[]> {
  const res = await fetch(`/api/v1/selections/${predictionId}/odds`)
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export async function fetchStrongestSelections(date: string): Promise<SelectionSummary[]> {
  const params = new URLSearchParams({ date, min_qscore: '85' })
  const res = await fetch(`/api/v1/selections/qualified?${params}`)
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export async function fetchQualifiedSelections(date: string, minQScore = 75): Promise<SelectionSummary[]> {
  const params = new URLSearchParams({ date, min_qscore: String(minQScore) })
  const res = await fetch(`/api/v1/selections/qualified?${params}`)
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export interface BandMix {
  lohela_band: string
  market_band: string
  source: 'dynamic' | 'watchlist' | 'discovered'
  best_rank: number | null
  active: boolean
  reason: 'NO_HISTORY' | 'INSUFFICIENT_SAMPLE' | 'NON_POSITIVE_ROI' | 'OUTSIDE_TOP_MIXES' | 'ROI_BELOW_THRESHOLD' | null
  research_status: 'research_qualified' | 'not_qualified'
  provisional: boolean
  sample_size: number
  wins: number
  losses: number
  hit_rate: number | null
  roi: number | null
  recent_sample_size?: number
  recent_hit_rate?: number | null
  recent_roi?: number | null
}

export interface BandMixScan {
  target_date: string
  evidence_through: string
  captured_at: string | null
  capture_source: string
  provisional: boolean
  reconstructed?: boolean
  rules: { min_sample: number; min_roi?: number; provisional_below_sample?: number; recent_window_days?: number; one_selection_per_match?: boolean; best_mix_limit?: number; requires_positive_roi?: boolean; evidence: string }
  evidence_sample_size: number
  watchlist: BandMix[]
  discovered: BandMix[]
  monitored?: BandMix[]
}

export interface BandMixPick {
  prediction_id: number
  match_id: number
  home_team: string
  away_team: string
  competition: string
  kickoff_at: string
  market: string
  selection: string
  model_probability: number
  market_probability: number
  odds: number
  edge: number | null
  q_score: number
  q_grade: string
  lohela_band: string
  market_band: string
  mix_source: 'dynamic' | 'watchlist' | 'discovered'
  mix_rank: number | null
  mix_sample_size: number
  mix_hit_rate: number | null
  mix_roi: number | null
  pre_kickoff_quote: boolean
  match_status: string
  home_goals: number | null
  away_goals: number | null
  live_phase: string | null
  elapsed_minutes: number | null
  result: 'won' | 'lost' | 'void' | null
}

export interface BandMixSummary {
  picks: number; matches: number; settled: number; wins: number; losses: number; voids: number; pending: number
  profit_loss: number; roi: number | null
}

export interface BandMixLifecycleEvent {
  lohela_band: string
  market_band: string
  status: 'active' | 'inactive'
  transition: 'promoted' | 'demoted' | 'unchanged' | 'initial_inactive'
  sample_size: number
  roi: number | null
  reason: string | null
}

export interface BandMixSelections {
  scan: BandMixScan
  lifecycle_events?: BandMixLifecycleEvent[]
  ledger_backed?: boolean
  summary: BandMixSummary
  rows: BandMixPick[]
}

export async function fetchBandMixSelections(date: string): Promise<BandMixSelections> {
  const res = await fetch(`/api/v1/selections/band-mix?${new URLSearchParams({ date })}`)
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export interface ParameterSweepPolicy {
  version: string
  min_probability: number
  min_odds: number
  price_haircut: number
  min_ev: number
  max_quote_age_hours: number
}

export interface ParameterSweepMetric {
  selected: number
  resolved: number
  wins: number
  losses: number
  voids: number
  profit: number
  roi: number | null
  hit_rate: number | null
  days: number
}

export interface ParameterSweepPick {
  prediction_id: number
  match_id: number
  home_team: string
  away_team: string
  competition: string
  kickoff_at: string
  market: string
  selection: string
  model_probability: number
  odds: number
  q_score: number
  q_grade: string
  edge: number | null
  source_odds_at: string
  pre_kickoff_quote: boolean
  result: 'win' | 'loss' | 'void' | null
  policy_train_roi: number | null
  policy_validation_roi: number | null
}

export interface ParameterSweepScan {
  target_date: string
  evidence_through: string
  captured_at: string | null
  capture_source: string
  provisional: boolean
  reconstructed: boolean
  status: 'qualified' | 'no_qualifying_policy'
  rules: { grid_size: number; minimum_validation_roi: number; minimum_train_sample: number; minimum_validation_sample: number; selection_rule: string; one_selection_per_match: boolean }
  chosen_policy: ParameterSweepPolicy | null
  chosen_train: ParameterSweepMetric | null
  chosen_validation: ParameterSweepMetric | null
  qualifying_policy_count: number
  candidate_database_rows: number
  adapted_candidates: number
  parameter_ranges: Record<string, [number, number]>
  limitations: string[]
}

export interface ParameterSweepSelections {
  scan: ParameterSweepScan
  summary: { picks: number; matches: number; settled: number; wins: number; losses: number; voids: number; pending: number; profit_loss: number; roi: number | null }
  rows: ParameterSweepPick[]
}

export async function fetchParameterSweepSelections(date: string): Promise<ParameterSweepSelections> {
  const res = await fetch(`/api/v1/selections/parameter-sweep?${new URLSearchParams({ date })}`)
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export async function fetchRejectedSelections(date: string): Promise<SelectionSummary[]> {
  // Load the complete rejection ledger so candidate views can enforce the
  // same hard safety gates as ticket publication. The Rejected view may also
  // explain every excluded candidate instead of silently truncating evidence.
  const params = new URLSearchParams({ date, ticket_type: 'safe', limit: '500' })
  const res = await fetch(`/api/v1/selections/rejected?${params}`)
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export async function triggerModelRun(date: string): Promise<{ predictions: number }> {
  const res = await fetch(`/api/v1/models/run?target_date=${date}`, { method: 'POST' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  const data = await res.json()
  return data.result
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

export function formatKickoff(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatMarket(market: string): string {
  const labels: Record<string, string> = {
    home_win: 'Home Win',
    draw: 'Draw',
    away_win: 'Away Win',
    over_1_5: 'Over 1.5',
    'over_1.5': 'Over 1.5',
    over_2_5: 'Over 2.5',
    'over_2.5': 'Over 2.5',
    under_2_5: 'Under 2.5',
    'under_2.5': 'Under 2.5',
    over_3_5: 'Over 3.5',
    'over_3.5': 'Over 3.5',
    under_3_5: 'Under 3.5',
    'under_3.5': 'Under 3.5',
    over_4_5: 'Over 4.5',
    'over_4.5': 'Over 4.5',
    under_4_5: 'Under 4.5',
    'under_4.5': 'Under 4.5',
    btts_yes: 'BTTS Yes',
    btts_no: 'BTTS No',
    double_chance_1x: '1X',
    double_chance_x2: 'X2',
    dnb_home: 'DNB Home',
    dnb_away: 'DNB Away',
  }
  return labels[market] ?? market.replace(/_/g, ' ').replace(/\b\w/g, character => character.toUpperCase())
}

export function formatSelection(selection: string): string {
  const labels: Record<string, string> = {
    double_chance_1x: 'Double Chance (1X)',
    double_chance_x2: 'Double Chance (X2)',
    dnb_home: 'Draw No Bet (Home)',
    dnb_away: 'Draw No Bet (Away)',
    btts_yes: 'Both Teams to Score (Yes)',
    btts_no: 'Both Teams to Score (No)',
    home_win: 'Home Win',
    away_win: 'Away Win',
    draw: 'Draw',
  }
  return labels[selection] ?? selection.replace(/_/g, ' ').replace(/\b\w/g, character => character.toUpperCase())
}

export function formatTicketType(type: string): string {
  return type === 'safe' ? 'Conservative' : type.replace(/_/g, ' ').replace(/\b\w/g, character => character.toUpperCase())
}

export function formatStage(stage: string): string {
  return stage.replace(/_/g, ' ').replace(/\b\w/g, character => character.toUpperCase())
}
