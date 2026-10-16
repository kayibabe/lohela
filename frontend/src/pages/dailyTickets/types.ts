import type { SelectionSummary } from "../../lib/api";

export interface DailyTicketsPageProps {
  date: string;
  initialTab?: DailyTab;
  onOpenTracker?: () => void;
}

export type DailyTab =
  "tickets" | "matches" | "best-mix" | "odds-policy" | "market-roi" | "passed";

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
  automatic: boolean;
  source_ticket_snapshots?: Record<string, { ticket_id?: number | null; ticket_type?: string; version?: number | null; model_version?: string; publication_hash?: string; published_at?: string }>;
}
