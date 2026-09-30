import { useEffect, useMemo, useState } from "react";
import type { BandMix, BandMixPick, BandMixSelections, SelectionSummary } from "../../lib/api";
import { fetchBandMixSelections, formatKickoff, formatMarket, formatSelection } from "../../lib/api";
import { bandSortValue, useSortableRows } from "../../components/SortableTable";
import { accumulatorBlockers, gradeTone, matchStatusText, reasonLabel, withRejectionEvidence } from "./helpers";

const pct = (value: number | null | undefined, digits = 1) =>
  value == null ? "—" : `${(value * 100).toFixed(digits)}%`;

const MIX_REASON: Record<NonNullable<BandMix["reason"]>, string> = {
  NO_HISTORY: "No settled history",
  INSUFFICIENT_SAMPLE: "Sample too small",
  NON_POSITIVE_ROI: "ROI not positive",
  OUTSIDE_TOP_MIXES: "Not a top mix",
  ROI_BELOW_THRESHOLD: "Below ROI threshold",
};

function toSelection(row: BandMixPick): SelectionSummary {
  return {
    ...row,
    best_odds: row.odds,
    avg_implied: row.market_probability,
    match_status: row.match_status,
  };
}

export function BandMixPicks({
  date,
  rejectedRows,
  onSelect,
  onAdd,
}: {
  date: string;
  rejectedRows: SelectionSummary[];
  onSelect: (row: SelectionSummary) => void;
  onAdd: (row: SelectionSummary) => void;
}) {
  const [data, setData] = useState<BandMixSelections | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () =>
      fetchBandMixSelections(date)
        .then((value) => { if (active) { setData(value); setError(null); } })
        .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : "Unable to load best-mix picks"); })
        .finally(() => { if (active) setLoading(false); });
    setLoading(true);
    load();
    const timer = window.setInterval(load, 300_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [date]);

  const scan = data?.scan;
  const mixes = useMemo(() => scan?.watchlist ?? [], [scan]);
  const picks = useMemo(() => {
    const rows = data?.rows ?? [];
    const evidence = new Map(withRejectionEvidence(rows.map(toSelection), rejectedRows).map((row) => [row.prediction_id, row]));
    return rows.map((row) => ({ row, selection: evidence.get(row.prediction_id) ?? toSelection(row) }));
  }, [data, rejectedRows]);

  const activeWatch = scan?.watchlist.filter((mix) => mix.active).length ?? 0;
  const promoted = data?.lifecycle_events?.filter((event) => event.transition === "promoted").length ?? 0;
  const demoted = data?.lifecycle_events?.filter((event) => event.transition === "demoted").length ?? 0;
  const settled = picks.filter(({ row }) => row.result === "won" || row.result === "lost");
  const wins = settled.filter(({ row }) => row.result === "won").length;
  const pnl = settled.reduce((sum, { row }) => sum + (row.result === "won" ? row.odds - 1 : -1), 0);

  return (
    <section className="selection-panel band-mix-panel">
      <div className="section-heading">
        <div>
          <h2>Best-mix picks</h2>
          <span className="section-note">
            One qualifying selection per match, chosen only when its Lohela and market band pair meets frozen research criteria.
            Historical performance is not a promise of profit.
          </span>
        </div>
        {data && (
          <span className="section-note match-count-summary">
            <b>{picks.length}</b> qualifying matches
          </span>
        )}
      </div>

      {loading && !data && <p className="panel-empty">Scanning best-performing mixes…</p>}
      {error && !data && (
        <div className="error-bar" role="alert"><div><strong>Best-mix picks unavailable</strong><span>{error}</span></div></div>
      )}

      {scan && (
        <>
          <div className={`band-mix-scan${scan.provisional ? " provisional" : ""}`} role="status">
            <strong>{scan.reconstructed ? "Historical reconstruction" : scan.provisional ? "Provisional scan" : "Daily scan frozen"}</strong>
            <span>
              {scan.reconstructed
                ? "This past date has no frozen scan, so it is shown only as a reconstruction and is not prospective evidence."
                : scan.provisional
                ? "This date is in the future, so its evidence window is still open. The scan freezes on the day."
                : `Captured ${scan.captured_at ? new Date(scan.captured_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—"} (${scan.capture_source.replace(/_/g, " ")}).`}
              {" "}Evidence: {scan.evidence_sample_size.toLocaleString()} settled predictions through {scan.evidence_through}.
              {scan.rules.min_roi == null
                ? " This is a legacy frozen scan using its original criteria."
                : <> A pair joins with at least {scan.rules.min_sample} results and ROI of at least {pct(scan.rules.min_roi)}. Recent ROI covers the last {scan.rules.recent_window_days} days. One qualifying selection is shown per match.</>}
              {data.ledger_backed && <> Daily match output is recorded in the research ledger. {promoted} promoted, {demoted} demoted today.</>}
            </span>
          </div>

          <MixTable mixes={mixes} />

          {activeWatch === 0 && (
            <p className="panel-empty">
              No band pair currently meets the dynamic evidence and ROI threshold, so no picks are listed for this date.
            </p>
          )}

          {activeWatch > 0 && (
            <>
              <div className="band-mix-kpis" aria-label="Best-mix picks summary">
                <span><small>Qualifying matches</small><b>{picks.length}</b></span>
                <span><small>Settled</small><b>{settled.length}</b></span>
                <span><small>Won / lost</small><b>{wins} / {settled.length - wins}</b></span>
                <span><small>Hit rate</small><b>{settled.length ? pct(wins / settled.length) : "—"}</b></span>
                <span><small>Flat-stake P&amp;L</small><b className={pnl >= 0 ? "positive" : "negative"}>{settled.length ? `${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}u` : "—"}</b></span>
                <span><small>ROI</small><b className={pnl >= 0 ? "positive" : "negative"}>{settled.length ? pct(pnl / settled.length) : "—"}</b></span>
              </div>
              {picks.length === 0 ? (
                <p className="panel-empty">No selections for this date fall in a qualifying mix.</p>
              ) : (
                <PickTable picks={picks} onSelect={onSelect} onAdd={onAdd} />
              )}
            </>
          )}
          <p className="matrix-note">
            Mix history is whole-system research evidence, not a promise of profit. Each fixture appears once;
            when several selections qualify, the most established band is retained.
          </p>
        </>
      )}
    </section>
  );
}

function MixTable({ mixes }: { mixes: BandMix[] }) {
  const { sorted, header } = useSortableRows(mixes, {
    lohela: (mix) => bandSortValue(mix.lohela_band),
    market: (mix) => bandSortValue(mix.market_band),
    n: (mix) => mix.sample_size,
    wins: (mix) => mix.wins,
    hit: (mix) => mix.hit_rate,
    roi: (mix) => mix.roi,
    recentRoi: (mix) => mix.recent_roi,
    rank: (mix) => mix.best_rank,
    status: (mix) => (mix.active ? "Active" : MIX_REASON[mix.reason ?? "NO_HISTORY"]),
  });
  return (
    <div className="analytics-table-wrap">
      <table className="analytics-table">
        <caption className="sr-only">Band-mix criteria and their current performance</caption>
        <thead>
          <tr>
            {header("lohela", "Lohela band")}
            {header("market", "Market band")}
            {header("n", "N")}
            {header("wins", "Won / lost")}
            {header("hit", "Hit rate")}
            {header("roi", "ROI")}
            {header("recentRoi", "Recent ROI")}
            {header("rank", "Top-mix rank")}
            {header("status", "Status")}
          </tr>
        </thead>
        <tbody>
          {sorted.map((mix) => (
            <tr key={`${mix.lohela_band}-${mix.market_band}`}>
              <td><strong>{mix.lohela_band}%</strong></td>
              <td>{mix.market_band}%</td>
              <td>{mix.sample_size}</td>
              <td>{mix.wins} / {mix.losses}</td>
              <td>{pct(mix.hit_rate)}</td>
              <td className={mix.roi != null && mix.roi > 0 ? "positive" : "negative"}>{pct(mix.roi)}</td>
              <td className={mix.recent_roi != null && mix.recent_roi >= 0 ? "positive" : "negative"}>{pct(mix.recent_roi)}<small>N {mix.recent_sample_size ?? 0}</small></td>
              <td>{mix.best_rank ?? "—"}</td>
              <td><span className={`band-mix-status ${mix.active ? "active" : "inactive"}`}>{mix.active ? (mix.provisional ? "Provisional research" : "Research qualified") : MIX_REASON[mix.reason ?? "NO_HISTORY"]}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const RESULT_ORDER = { won: 3, void: 2, lost: 1 } as const;

function PickTable({
  picks,
  onSelect,
  onAdd,
}: {
  picks: { row: BandMixPick; selection: SelectionSummary }[];
  onSelect: (row: SelectionSummary) => void;
  onAdd: (row: SelectionSummary) => void;
}) {
  const { sorted, header } = useSortableRows(
    picks,
    {
      kickoff: ({ row }) => row.kickoff_at,
      match: ({ row }) => `${row.home_team} ${row.away_team}`,
      market: ({ row }) => `${formatMarket(row.market)} ${formatSelection(row.selection)}`,
      lohela: ({ row }) => row.model_probability,
      marketProb: ({ row }) => row.market_probability,
      odds: ({ row }) => row.odds,
      mix: ({ row }) => bandSortValue(row.lohela_band) * 1000 + bandSortValue(row.market_band),
      mixRoi: ({ row }) => row.mix_roi,
      q: ({ row }) => row.q_score,
      result: ({ row }) => (row.result ? RESULT_ORDER[row.result] : null),
    },
    { key: "kickoff", direction: "asc" },
  );
  return (
    <div className="analytics-table-wrap">
      <table className="analytics-table band-mix-table">
        <caption className="sr-only">Selections in a qualifying band mix</caption>
        <thead>
          <tr>
            {header("kickoff", "Kickoff")}
            {header("match", "Match / league")}
            {header("market", "Market / selection")}
            {header("lohela", "Lohela")}
            {header("marketProb", "Market")}
            {header("odds", "Odds")}
            {header("mix", "Mix")}
            {header("mixRoi", "Mix ROI")}
            {header("q", "Q-score")}
            {header("result", "Status")}
            <th><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {sorted.map(({ row, selection }) => {
            const blockers = accumulatorBlockers(selection);
            return (
              <tr key={row.prediction_id}>
                <td><strong>{formatKickoff(row.kickoff_at)}</strong></td>
                <td><strong>{row.home_team} <span className="match-vs">vs</span> {row.away_team}</strong><small>{row.competition}</small></td>
                <td><span className="market-chip">{formatMarket(row.market)}</span><strong>{formatSelection(row.selection)}</strong></td>
                <td>{pct(row.model_probability)}</td>
                <td>{pct(row.market_probability)}{!row.pre_kickoff_quote && <small>Post-kickoff quote</small>}</td>
                <td>{row.odds.toFixed(2)}</td>
                <td><strong>{row.lohela_band} × {row.market_band}</strong><small>Dynamic group{row.mix_rank ? ` · ROI rank #${row.mix_rank}` : ""}</small></td>
                <td className={row.mix_roi != null && row.mix_roi > 0 ? "positive" : "negative"}>{pct(row.mix_roi)}<small>N {row.mix_sample_size} · {pct(row.mix_hit_rate)} hit</small></td>
                <td><b className={`grade-badge grade-${gradeTone(row.q_grade)}`}>{row.q_grade}</b> {row.q_score.toFixed(1)}</td>
                <td>
                  <span className={`selection-outcome ${row.result ?? "pending"}`}><strong>{row.result ?? "pending"}</strong></span>
                  <small>{matchStatusText(selection)}</small>
                </td>
                <td>
                  <div className="band-mix-actions">
                    <button type="button" className="btn-ghost btn-sm" onClick={() => onSelect(selection)}>Details</button>
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      onClick={() => onAdd(selection)}
                      disabled={blockers.length > 0}
                      title={blockers.length > 0 ? `Unavailable: ${blockers.map(reasonLabel).join(", ")}` : "Add to personal accumulator"}
                    >
                      {blockers.length > 0 ? "Unavailable" : "Add"}
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
