import type { SelectionSummary } from "../../lib/api";

export interface DailyTicketsPageProps {
  date: string;
}

export type DailyTab =
  "tickets" | "best-mix" | "my-accumulators";

export type MatchDisplayState =
  "finished" | "live" | "scheduled" | "postponed" | "cancelled";

export type SelectionDisplayState = "won" | "lost" | "void" | "inplay" | "pending";

export interface CustomAccumulator {
  id: number;
  name: string;
  date: string;
  legs: SelectionSummary[];
  stake: number | null;
  status: "draft" | "placed" | "won" | "lost" | "void" | "cashout";
}
