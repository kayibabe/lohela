import { useState } from "react";
import type { SelectionSummary } from "../../lib/api";
import { formatKickoff, formatMarket, formatSelection } from "../../lib/api";
import {
  accumulatorBlockers,
  matchDisplayState,
  matchStatusText,
  reasonLabel,
  selectionDisplayState,
  selectionStatusLabel,
  spreadTone,
} from "./helpers";
import type { SelectionDisplayState } from "./types";

export function SelectionPanel({
  title,
  rows,
  empty,
  rejected = false,
  onSelect,
  onAdd,
}: {
  title: string;
  rows: SelectionSummary[];
  empty: string;
  rejected?: boolean;
  onSelect?: (row: SelectionSummary) => void;
  onAdd?: (row: SelectionSummary) => void;
}) {
  const [statusFilter, setStatusFilter] = useState<"all" | SelectionDisplayState>("all");
  const statusCounts = rows.reduce<Record<SelectionDisplayState, number>>(
    (counts, row) => {
      counts[selectionDisplayState(row)] += 1;
      return counts;
    },
    { won: 0, lost: 0, void: 0, inplay: 0, pending: 0 },
  );
  const filteredRows = statusFilter === "all"
    ? rows
    : rows.filter((row) => selectionDisplayState(row) === statusFilter);
  const filterButton = (
    key: "all" | SelectionDisplayState,
    label: string,
    count: number,
  ) => (
    <button
      className={`${statusFilter === key ? "active" : ""} ${key}`}
      onClick={() => setStatusFilter(key)}
      aria-pressed={statusFilter === key}
      disabled={count === 0}
    >
      {label} <b>{count}</b>
    </button>
  );
  return (
    <section className={`selection-panel selection-card-panel ${rejected ? "rejected" : "strongest"}`}>
      <div className="section-heading">
        <div>
          <h2>{title}</h2>
          <span className="section-note">Each card is one market selection; match and selection status are shown separately.</span>
        </div>
        <span className="section-note"><b>{rows.length}</b> shown</span>
      </div>
      {rows.length === 0 ? (
        <p className="panel-empty">{empty}</p>
      ) : (
        <>
          <div className="selection-status-filters" aria-label={`Filter ${title.toLowerCase()} by status`}>
            {filterButton("all", "All", rows.length)}
            {filterButton("won", "Won", statusCounts.won)}
            {filterButton("lost", "Lost", statusCounts.lost)}
            {filterButton("void", "Void", statusCounts.void)}
            {filterButton("inplay", "In play", statusCounts.inplay)}
            {filterButton("pending", "Pending", statusCounts.pending)}
          </div>
          {filteredRows.length === 0 ? (
            <p className="panel-empty">No selections have this status.</p>
          ) : (
          <ul className="selection-card-grid">
          {filteredRows.map((row) => {
            const matchState = matchDisplayState(row);
            const selectionState = selectionDisplayState(row);
            const blockers = accumulatorBlockers(row);
            return (
            <li className={`selection-card ${matchState} ${selectionState}`} key={`${row.prediction_id}-${rejected ? "r" : "s"}`}>
              <button
                className="selection-card-trigger"
                onClick={() => onSelect?.(row)}
                disabled={!onSelect}
              >
                <div className="selection-card-heading">
                  <span>{row.competition} · {formatKickoff(row.kickoff_at)}</span>
                  <b className={`summary-match-status ${matchState}`}>{matchStatusText(row)}</b>
                </div>
                <div className="selection-card-match">
                  <strong>
                    {row.home_team} <span className="leg-match-vs">vs</span>{" "}
                    {row.away_team}
                  </strong>
                  <span className="market-chip">{formatMarket(row.market)}</span>
                </div>
                  {rejected && (
                    <div className="reason-codes">
                      {row.reason_codes?.map((code) => (
                        <span key={code}>{code.replace(/_/g, " ")}</span>
                      ))}
                    </div>
                  )}
                <div className="selection-card-metrics">
                  <span><small>Q-score</small><b>Q {row.q_score.toFixed(1)}</b></span>
                  <span><small>Odds</small><b>{row.best_odds ? `${row.best_odds.toFixed(2)}×` : "—"}</b></span>
                  <span><small>Edge</small><b className={row.edge && row.edge > 0 ? "positive" : ""}>{row.edge != null ? `${(row.edge * 100).toFixed(1)}%` : "—"}</b></span>
                  <span><small>Spread</small><b className={spreadTone(row.model_agreement)}>{row.model_agreement == null ? "—" : `${(row.model_agreement * 100).toFixed(1)} pp`}</b></span>
                </div>
                <div className="selection-card-footer">
                  <span className="selection-pick"><small>Selection</small><strong>{formatSelection(row.selection)}</strong></span>
                  <b className={`selection-status-badge ${selectionState}`}>{selectionStatusLabel(selectionState)}</b>
                  <span className="row-open-icon" aria-hidden="true">›</span>
                </div>
              </button>
              {onAdd && (
                <div className="selection-card-actions">
                  <button
                    className="btn-ghost btn-sm"
                    onClick={() => onAdd(row)}
                    disabled={blockers.length > 0}
                    title={blockers.length > 0 ? `Unavailable: ${blockers.map(reasonLabel).join(", ")}` : "Add to personal accumulator"}
                  >
                    {blockers.length > 0 ? "Unavailable" : "Add to accumulator"}
                  </button>
                  <button className="btn-ghost btn-sm" onClick={() => onSelect?.(row)}>View evidence</button>
                </div>
              )}
              {onAdd && blockers.length > 0 && <small className="selection-add-blockers">Not addable · {blockers.map(reasonLabel).join(" · ")}</small>}
            </li>
          )})}
          </ul>
          )}
        </>
      )}
    </section>
  );
}
