import { useMemo, useState } from "react";
import "./MarketRoiDaily.css";

interface MarketRoiRow {
  market: string;
  sample_size: number;
  wins: number;
  losses: number;
  hit_rate: number;
  flat_stake_pnl: number;
  roi: number;
  average_odds: number;
  qualifies: boolean;
  evidence_label: string;
}

interface MarketCandidate {
  prediction_id: number;
  match_id: number;
  home_team: string;
  away_team: string;
  competition: string;
  kickoff_at: string;
  match_status: string;
  home_goals: number | null;
  away_goals: number | null;
  result: "won" | "lost" | "void" | null;
  market: string;
  selection: string;
  odds: number;
  model_probability: number;
  q_score: number;
  q_grade: string;
  recommendation_status: string;
  market_roi: number;
  market_sample_size: number;
  quote_at: string;
  quote_age_minutes: number;
  bookmaker: string;
}

export interface MarketRoiDailyData {
  target_date: string;
  refreshed_at: string;
  roi_threshold: number;
  max_quote_age_hours: number;
  historical_through: string;
  historical_unique_selections: number;
  historical_fixture_summary: { unique_fixtures: number; all_won: number; all_lost: number; mixed: number };
  historical_markets: MarketRoiRow[];
  qualifying_markets: string[];
  candidate_count: number;
  candidate_fixture_count: number;
  candidates: MarketCandidate[];
  unsupported_outcomes_excluded: number;
  method: string;
  research_only: boolean;
}

export async function fetchDailyMarketRoi(date: string): Promise<MarketRoiDailyData> {
  const response = await fetch(`/api/v1/performance/market-roi-daily?date=${encodeURIComponent(date)}`);
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json() as Promise<MarketRoiDailyData>;
}

const pct = (value: number) => `${(value * 100).toFixed(1)}%`;
const title = (value: string) => value
  .replace(/_(\d+)_(\d+)/g, " $1.$2")
  .replace(/_/g, " ")
  .replace(/\b\w/g, (letter: string) => letter.toUpperCase());
const localDateTime = (value: string) => new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium", timeStyle: "short",
}).format(new Date(value));

export function MarketRoiDaily({ data, error }: { data: MarketRoiDailyData | null; error: string | null }) {
  const [minimumQuality, setMinimumQuality] = useState("all");
  const [grade, setGrade] = useState("all");
  const fixtures = useMemo(() => {
    const grouped = new Map<number, MarketCandidate[]>();
    for (const candidate of data?.candidates ?? []) {
      if (candidate.q_grade.trim().toUpperCase() === "REJECT" || candidate.recommendation_status.trim().toUpperCase() === "REJECT") continue;
      const rows = grouped.get(candidate.match_id) ?? [];
      rows.push(candidate);
      grouped.set(candidate.match_id, rows);
    }
    return [...grouped.values()];
  }, [data?.candidates]);
  const filteredFixtures = useMemo(() => fixtures
    .map((rows) => rows.filter((row) =>
      (minimumQuality === "all" || row.q_score >= Number(minimumQuality)) &&
      (grade === "all" || row.q_grade.trim().toUpperCase() === grade)))
    .filter((rows) => rows.length > 0), [fixtures, minimumQuality, grade]);

  return (
    <section className="market-roi-panel">
      <header className="market-roi-heading">
        <div>
          <span className="eyebrow">Daily market scan · research only</span>
          <h2 className="section-title">Market ROI and today’s shortlist</h2>
          <p>Markets are ranked by flat-stake ROI from earlier settled public market-priced picks. Today’s candidates keep only markets at or above the +20% historical ROI threshold and existing BET/WATCH recommendations with a valid pre-kickoff price. The listed quote age shows how current that saved price is.</p>
        </div>
        <div className="market-roi-refresh">
          <span>Auto refresh · 5 min</span>
          {data && <small>Updated {localDateTime(data.refreshed_at)}</small>}
        </div>
      </header>

      {error && <div className="market-roi-error" role="alert"><b>Market scan unavailable</b><span>{error}</span></div>}
      {!data && !error && <p className="panel-empty">Loading daily market performance…</p>}

      {data && <>
        <div className="market-roi-kpis">
          <span><small>Historical window through</small><b>{data.historical_through}</b></span>
          <span><small>Unique settled selections</small><b>{data.historical_unique_selections}</b></span>
          <span><small>Unique fixtures</small><b>{data.historical_fixture_summary.unique_fixtures}</b></span>
          <span><small>Markets at ≥ 20% ROI</small><b>{data.qualifying_markets.length}</b></span>
          <span><small>Today’s unique fixtures</small><b>{fixtures.length}</b></span>
          <span><small>Eligible market selections</small><b>{fixtures.reduce((count, rows) => count + rows.length, 0)}</b></span>
        </div>

        <div className="market-roi-fixture-summary" aria-label="Historical results grouped by unique fixture">
          <b>Historical fixture outcomes</b>
          <span>{data.historical_fixture_summary.all_won} all selections won</span>
          <span>{data.historical_fixture_summary.all_lost} all selections lost</span>
          <span>{data.historical_fixture_summary.mixed} mixed outcomes</span>
        </div>

        <div className="market-roi-note" role="note">
          ROI uses one unit per unique fixture-market-selection at the captured decimal odds. Prices must have been captured before kickoff and within {data.max_quote_age_hours} hours of the decision. Results are refreshed from settled matches; this is a research shortlist, not a profitability claim.
        </div>

        <div className="market-roi-table-wrap">
          <h3>Historical market ranking</h3>
          {data.historical_markets.length === 0 ? <p className="panel-empty">No settled predictions with complete, fresh pre-kickoff prices are available yet.</p> : (
            <table className="market-roi-table">
              <thead><tr><th>Rank</th><th>Market</th><th>ROI</th><th>W–L</th><th>Hit rate</th><th>Flat P&amp;L</th><th>Picks</th><th>Evidence</th><th>Status</th></tr></thead>
              <tbody>{data.historical_markets.map((row, index) => <tr key={row.market}>
                <td>{index + 1}</td><td><span className={`market-roi-selection-chip ${row.qualifies ? "qualifies" : ""}`}>{title(row.market)}</span></td>
                <td className={row.roi >= 0 ? "roi-positive" : "roi-negative"}>{pct(row.roi)}</td>
                <td>{row.wins}–{row.losses}</td><td>{pct(row.hit_rate)}</td>
                <td className={row.flat_stake_pnl >= 0 ? "roi-positive" : "roi-negative"}>{row.flat_stake_pnl >= 0 ? "+" : ""}{row.flat_stake_pnl.toFixed(2)}</td>
                <td>{row.sample_size}</td><td>{row.evidence_label}</td>
                <td><span className={`market-roi-badge ${row.qualifies ? "qualifies" : ""}`}>{row.qualifies ? "≥ 20%" : "Below gate"}</span></td>
              </tr>)}</tbody>
            </table>
          )}
        </div>

        <div className="market-roi-table-wrap market-roi-candidates">
          <h3>Today’s qualified picks and results</h3>
          <div className="market-roi-filters">
            <label>Quality Score (Q)
              <select value={minimumQuality} onChange={(event) => setMinimumQuality(event.target.value)}>
                <option value="all">All</option>
                {[50, 60, 70, 80, 90].map((score) => <option key={score} value={score}>{score} and above</option>)}
              </select>
            </label>
            <label>Grade
              <select value={grade} onChange={(event) => setGrade(event.target.value)}>
                <option value="all">All</option>
                {["A+", "A", "B+", "B", "C"].map((value) => <option key={value} value={value}>{value}</option>)}
              </select>
            </label>
            <span role="status">Showing {filteredFixtures.length} of {fixtures.length} matches · {filteredFixtures.reduce((count, rows) => count + rows.length, 0)} selections</span>
          </div>
          {fixtures.length === 0 ? <p className="panel-empty">No upcoming match currently clears the historical ROI and recommendation gates with a valid pre-kickoff quote.</p> : (
            filteredFixtures.length === 0 ? <p className="panel-empty">No selections match these filters. Choose All or lower the Quality Score threshold.</p> :
            <div className="market-roi-fixtures">{filteredFixtures.map((rows) => {
              const fixture = rows[0];
              return <article className="market-roi-fixture" key={fixture.match_id}>
                <div className="market-roi-fixture-title">
                  <div><b>{fixture.home_team} vs {fixture.away_team}</b><small>{fixture.competition}</small></div>
                  <time dateTime={fixture.kickoff_at}>{localDateTime(fixture.kickoff_at)}</time>
                </div>
                <div className="market-roi-picks">{rows.map((row) => <div className="market-roi-pick" key={row.prediction_id}>
                  <span><b>{title(row.market)}</b><small>{title(row.selection)}</small></span>
                  <span><b>{row.odds.toFixed(2)}</b><small>price</small></span>
                  <span><b className="roi-positive">{pct(row.market_roi)}</b><small>historical ROI · N={row.market_sample_size}</small></span>
                  <span><b>{row.recommendation_status}</b><small>{row.q_grade} · Q {row.q_score.toFixed(0)}</small><small className={`market-roi-result ${row.result ?? "pending"}`}>{row.result ?? "Pending"}{row.home_goals != null && row.away_goals != null ? ` · ${row.home_goals}–${row.away_goals}` : ""}</small></span>
                  <span><b>{row.quote_age_minutes.toFixed(0)} min</b><small>{row.bookmaker || "Unknown bookmaker"}</small></span>
                </div>)}</div>
              </article>;
            })}</div>
          )}
        </div>
      </>}
    </section>
  );
}
