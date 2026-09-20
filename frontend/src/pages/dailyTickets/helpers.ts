import type { CustomAccumulator as ApiCustomAccumulator, SelectionSummary } from "../../lib/api";
import type {
  CustomAccumulator,
  MatchDisplayState,
  SelectionDisplayState,
} from "./types";

export function localDateString(d = new Date()): string {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export const TODAY = localDateString();

export function spreadLabel(value: number | null | undefined) {
  if (value == null) return "Unavailable";
  if (value <= 0.05) return "Strong alignment";
  if (value <= 0.1) return "Acceptable";
  if (value <= 0.15) return "Caution";
  return "High disagreement";
}

export function spreadTone(value: number | null | undefined) {
  if (value == null) return "unknown";
  if (value <= 0.05) return "strong";
  if (value <= 0.1) return "acceptable";
  if (value <= 0.15) return "caution";
  return "high";
}

export function gradeTone(grade: string | undefined) {
  return grade ? grade.toLowerCase().replace("+", "-plus") : "unknown";
}

export function phaseLabel(phase?: string | null) {
  return ({ '1st_half': '1st Half', half_time: 'Half Time', '2nd_half': '2nd Half', extra_time: 'Extra Time', penalties: 'Penalties', suspended: 'Suspended', interrupted: 'Interrupted', live: 'Live' } as Record<string, string>)[phase ?? ''] ?? null;
}

export function matchDisplayState(row: SelectionSummary): MatchDisplayState {
  const status = row.match_status?.toLowerCase();
  if (status === "finished") return "finished";
  if (status === "live") return "live";
  if (status === "postponed") return "postponed";
  if (status === "cancelled") return "cancelled";
  return "scheduled";
}

export function matchStatusText(row: SelectionSummary): string {
  const state = matchDisplayState(row);
  const score = row.home_goals != null && row.away_goals != null
    ? `${row.home_goals}–${row.away_goals}`
    : null;
  if (state === "finished") return score ? `Finished · ${score}` : "Finished";
  if (state === "live") {
    const phase = phaseLabel(row.live_phase) ?? "Live";
    const elapsed = row.elapsed_minutes != null ? ` · ${row.elapsed_minutes}'` : "";
    return `${phase}${elapsed}${score ? ` · ${score}` : ""}`;
  }
  if (state === "postponed") return "Postponed";
  if (state === "cancelled") return "Cancelled";
  return "Upcoming";
}

export function selectionDisplayState(row: SelectionSummary): SelectionDisplayState {
  if (matchDisplayState(row) === "live") return "inplay";
  if (row.result === "won" || row.result === "lost" || row.result === "void") {
    return row.result;
  }
  return "pending";
}

export function selectionStatusLabel(state: SelectionDisplayState): string {
  return {
    won: "Won",
    lost: "Lost",
    void: "Void",
    inplay: "In play",
    pending: "Pending",
  }[state];
}

const HARD_ACCUMULATOR_BLOCKERS = new Set([
  "MISSING_ODDS",
  "LEG_ODDS_OUT_OF_RANGE",
  "MISSING_EDGE",
  "EDGE_BELOW_NO_BET_THRESHOLD",
  "INSUFFICIENT_MODEL_SET",
  "DATA_QUALITY_BELOW_THRESHOLD",
  "MISSING_ODDS_TIMESTAMP",
  "STALE_ODDS",
  "LEAGUE_CALIBRATION_BELOW_THRESHOLD",
  "MARKET_CALIBRATION_UNRELIABLE",
  "EDGE_BAND_CALIBRATION_UNRELIABLE",
]);

export const reasonLabel = (code: string) => code.replace(/_/g, " ").toLowerCase();

export function accumulatorBlockers(row: SelectionSummary): string[] {
  const blockers = (row.reason_codes ?? []).filter((code) =>
    HARD_ACCUMULATOR_BLOCKERS.has(code),
  );
  if (matchDisplayState(row) !== "scheduled") blockers.push("MATCH_NOT_UPCOMING");
  if (row.best_odds == null || row.best_odds <= 0) blockers.push("MISSING_ODDS");
  if (row.edge == null) blockers.push("MISSING_EDGE");
  return [...new Set(blockers)];
}

export function withRejectionEvidence(
  rows: SelectionSummary[],
  rejectedRows: SelectionSummary[],
): SelectionSummary[] {
  const reasons = new Map(
    rejectedRows.map((row) => [row.prediction_id, row.reason_codes ?? []]),
  );
  return rows.map((row) => ({
    ...row,
    reason_codes: reasons.get(row.prediction_id) ?? row.reason_codes,
  }));
}

export function apiAccumulatorToUi(row: ApiCustomAccumulator): CustomAccumulator {
  return {
    id: row.id,
    name: row.name,
    date: row.target_date,
    legs: row.legs.map((leg) => ({
      prediction_id: leg.prediction_id,
      match_id: leg.match_id,
      home_team: leg.home_team,
      away_team: leg.away_team,
      competition: leg.competition,
      kickoff_at: leg.kickoff_at,
      market: leg.market,
      selection: leg.selection,
      model_probability: leg.probability_snapshot ?? undefined,
      q_score: leg.q_score_snapshot ?? 0,
      edge: leg.edge_snapshot,
      best_odds: leg.odds_snapshot,
      result: leg.result === "pending" ? null : leg.result,
    })),
    stake: row.stake,
    status: row.status,
  };
}
