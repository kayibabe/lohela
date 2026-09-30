import { useEffect, useState } from "react";
import type { ParameterSweepPick, ParameterSweepSelections, SelectionSummary } from "../../lib/api";
import { fetchParameterSweepSelections, formatKickoff, formatMarket, formatSelection } from "../../lib/api";
import { useSortableRows } from "../../components/SortableTable";
import { accumulatorBlockers, gradeTone, reasonLabel } from "./helpers";

const pct = (value: number | null | undefined) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;

function toSelection(row: ParameterSweepPick): SelectionSummary {
  return {
    prediction_id: row.prediction_id,
    match_id: row.match_id,
    home_team: row.home_team,
    away_team: row.away_team,
    competition: row.competition,
    kickoff_at: row.kickoff_at,
    market: row.market,
    selection: row.selection,
    model_probability: row.model_probability,
    q_score: row.q_score,
    q_grade: row.q_grade,
    edge: row.edge,
    best_odds: row.odds,
    match_status: "scheduled",
    result: row.result === "win" ? "won" : row.result === "loss" ? "lost" : row.result,
  };
}

export function ParameterSweepPicks({
  date,
  onSelect,
  onAdd,
}: {
  date: string;
  onSelect: (row: SelectionSummary) => void;
  onAdd: (row: SelectionSummary) => void;
}) {
  const [data, setData] = useState<ParameterSweepSelections | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () => fetchParameterSweepSelections(date)
      .then(value => { if (active) { setData(value); setError(null); } })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : "Unable to load dynamic policy"); });
    load();
    const timer = window.setInterval(load, 300_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [date]);

  const scan = data?.scan;
  const policy = scan?.chosen_policy;
  const rows = data?.rows ?? [];

  return (
    <section className="selection-panel band-mix-panel">
      <div className="section-heading">
        <div>
          <h2>Dynamic odds policy</h2>
          <span className="section-note">A broad daily parameter sweep. Matches appear only when a frozen pre-day validation window clears the 20% ROI research gate.</span>
        </div>
        {data && <span className="section-note match-count-summary"><b>{rows.length}</b> qualifying matches</span>}
      </div>

      {error && <div className="error-bar" role="alert"><div><strong>Dynamic policy unavailable</strong><span>{error}</span></div></div>}
      {!scan && !error && <p className="panel-empty">Running the daily parameter sweep…</p>}
      {scan && (
        <>
          <div className={`band-mix-scan${scan.provisional ? " provisional" : ""}`} role="status">
            <strong>{scan.reconstructed ? "Historical reconstruction" : scan.provisional ? "Provisional scan" : "Daily policy frozen"}</strong>
            <span>
              {scan.status === "qualified"
                ? <>Odds ≥ <b>{policy?.min_odds.toFixed(2)}</b>, probability ≥ <b>{pct(policy?.min_probability)}</b>, validation ROI <b>{pct(scan.chosen_validation?.roi)}</b>.</>
                : <>No policy currently clears the minimum 20% validation ROI and evidence gates, so no matches are listed.</>}
              {" "}Grid: {scan.rules.grid_size.toLocaleString()} combinations; evidence through {scan.evidence_through}.
            </span>
          </div>

          {policy && scan.chosen_train && scan.chosen_validation && (
            <div className="band-mix-kpis" aria-label="Dynamic policy evidence">
              <span><small>Minimum odds</small><b>{policy.min_odds.toFixed(2)}</b></span>
              <span><small>Probability floor</small><b>{pct(policy.min_probability)}</b></span>
              <span><small>Train ROI / N</small><b>{pct(scan.chosen_train.roi)} / {scan.chosen_train.resolved}</b></span>
              <span><small>Validation ROI / N</small><b className="positive">{pct(scan.chosen_validation.roi)} / {scan.chosen_validation.resolved}</b></span>
              <span><small>Quote age</small><b>{policy.max_quote_age_hours}h</b></span>
              <span><small>Policy count</small><b>{scan.qualifying_policy_count}</b></span>
            </div>
          )}

          {rows.length > 0 && <SweepTable rows={rows} onSelect={onSelect} onAdd={onAdd} />}
          {scan.status === "qualified" && rows.length === 0 && <p className="panel-empty">The policy cleared historical gates, but no today selections passed its pre-kickoff filters.</p>}
          <p className="matrix-note">Paper research only. Historical ROI is not a promise of profit, and a policy is not exposed when its evidence gate fails.</p>
        </>
      )}
    </section>
  );
}

function SweepTable({ rows, onSelect, onAdd }: { rows: ParameterSweepPick[]; onSelect: (row: SelectionSummary) => void; onAdd: (row: SelectionSummary) => void }) {
  const { sorted, header } = useSortableRows(rows, {
    kickoff: row => new Date(row.kickoff_at).getTime(),
    match: row => `${row.home_team} ${row.away_team} ${row.competition}`,
    market: row => `${formatMarket(row.market)} ${formatSelection(row.selection)}`,
    probability: row => row.model_probability,
    odds: row => row.odds,
    qScore: row => row.q_score,
  });

  return (
    <div className="analytics-table-wrap">
      <table className="analytics-table band-mix-table">
        <caption className="sr-only">Matches selected by the dynamic odds policy</caption>
        <thead><tr>{header('kickoff', 'Kickoff')}{header('match', 'Match / league')}{header('market', 'Market')}{header('probability', 'Probability')}{header('odds', 'Odds')}{header('qScore', 'Q-score')}<th><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{sorted.map(row => {
          const selection = toSelection(row);
          const blockers = accumulatorBlockers(selection);
          return <tr key={row.prediction_id}>
            <td><strong>{formatKickoff(row.kickoff_at)}</strong></td>
            <td><strong>{row.home_team} <span className="match-vs">vs</span> {row.away_team}</strong><small>{row.competition}</small></td>
            <td><span className="market-chip">{formatMarket(row.market)}</span><strong>{formatSelection(row.selection)}</strong></td>
            <td>{pct(row.model_probability)}</td>
            <td><b>{row.odds.toFixed(2)}</b></td>
            <td><b className={`grade-badge grade-${gradeTone(row.q_grade)}`}>{row.q_grade}</b> {row.q_score.toFixed(1)}</td>
            <td><div className="band-mix-actions"><button type="button" className="btn-ghost btn-sm" onClick={() => onSelect(selection)}>Details</button><button type="button" className="btn-ghost btn-sm" onClick={() => onAdd(selection)} disabled={blockers.length > 0} title={blockers.length > 0 ? `Unavailable: ${blockers.map(reasonLabel).join(", ")}` : "Add to personal accumulator"}>Add</button></div></td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  );
}
