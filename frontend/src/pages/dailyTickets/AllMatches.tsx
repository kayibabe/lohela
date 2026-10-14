import { useState } from "react";
import type { SelectionSummary } from "../../lib/api";
import { formatKickoff, formatMarket, formatSelection } from "../../lib/api";
import {
  accumulatorBlockers,
  gradeTone,
  matchDisplayState,
  matchStatusText,
  reasonLabel,
  spreadLabel,
  spreadTone,
} from "./helpers";
import type { MatchDisplayState } from "./types";

function quoteFreshness(row: SelectionSummary) {
  if (!row.source_odds_at) return { label: "Quote time unavailable", state: "unknown" };
  const capturedAt = Date.parse(row.source_odds_at);
  const kickoffAt = Date.parse(row.kickoff_at);
  if (!Number.isFinite(capturedAt)) return { label: "Quote time invalid", state: "unknown" };
  if (Number.isFinite(kickoffAt) && capturedAt >= kickoffAt) return { label: "After kickoff", state: "blocked" };
  const ageHours = (Date.now() - capturedAt) / 3_600_000;
  if (ageHours < 0) return { label: "Future timestamp", state: "unknown" };
  if (ageHours <= 2) return { label: "Fresh", state: "fresh" };
  if (ageHours <= 8) return { label: "Aging", state: "aging" };
  return { label: "Historical", state: "stale" };
}

function percent(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "Unavailable" : `${(value * 100).toFixed(1)}%`;
}

function quoteTime(value: string | null | undefined) {
  if (!value) return "Time unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleString();
}

export function AllMatches({
  rows,
  onSelect,
  onAdd,
  allowAdd = true,
}: {
  rows: [number, SelectionSummary[]][];
  onSelect: (row: SelectionSummary) => void;
  onAdd: (row: SelectionSummary) => void;
  allowAdd?: boolean;
}) {
  const [statusFilter, setStatusFilter] = useState<"all" | MatchDisplayState>("all");
  const [query, setQuery] = useState("");
  const [leagueFilter, setLeagueFilter] = useState("all");
  const [marketFilter, setMarketFilter] = useState("all");
  const [sort, setSort] = useState<{
    key: "match" | "kickoff" | "league" | "q" | "markets" | "spread";
    direction: "asc" | "desc";
  }>({ key: "kickoff", direction: "asc" });
  const leagues = [...new Set(rows.map(([, selections]) => selections[0]?.competition).filter(Boolean))].sort();
  const leagueCounts = rows.reduce<Record<string, number>>((counts, [, selections]) => {
    const league = selections[0]?.competition;
    if (league) counts[league] = (counts[league] ?? 0) + 1;
    return counts;
  }, {});
  const topLeagues = Object.entries(leagueCounts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 8);
  const markets = [...new Set(rows.flatMap(([, selections]) => selections.map((row) => formatMarket(row.market))))].sort();
  const queryText = query.trim().toLocaleLowerCase();
  const discoveredRows = rows.flatMap(([id, selections]) => {
    const first = selections[0];
    const matchesText = !queryText || `${first.home_team} ${first.away_team} ${first.competition}`.toLocaleLowerCase().includes(queryText);
    const marketRows = marketFilter === "all" ? selections : selections.filter((row) => formatMarket(row.market) === marketFilter);
    if (!matchesText || (leagueFilter !== "all" && first.competition !== leagueFilter) || marketRows.length === 0) return [];
    return [[id, marketRows] as [number, SelectionSummary[]]];
  });
  const filteredRows = statusFilter === "all"
    ? discoveredRows
    : discoveredRows.filter(([, selections]) => matchDisplayState(selections[0]) === statusFilter);
  const sortedRows = [...filteredRows].sort(([, a], [, b]) => {
    const left = a[0];
    const right = b[0];
    const values: Record<typeof sort.key, [string | number, string | number]> =
      {
        match: [
          `${left.home_team} ${left.away_team}`,
          `${right.home_team} ${right.away_team}`,
        ],
        kickoff: [left.kickoff_at, right.kickoff_at],
        league: [left.competition, right.competition],
        q: [
          Math.max(...a.map((row) => row.q_score)),
          Math.max(...b.map((row) => row.q_score)),
        ],
        markets: [a.length, b.length],
        spread: [a[0].model_agreement ?? 999, b[0].model_agreement ?? 999],
      };
    const [first, second] = values[sort.key];
    const result = first < second ? -1 : first > second ? 1 : 0;
    return sort.direction === "asc" ? result : -result;
  });
  const changeSort = (key: typeof sort.key) =>
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: key === "kickoff" ? "asc" : "desc" },
    );
  const sortButton = (key: typeof sort.key, label: string) => (
    <button
      className={sort.key === key ? "active" : ""}
      onClick={() => changeSort(key)}
    >
      {label}
      {sort.key === key && (
        <span aria-hidden="true"> {sort.direction === "asc" ? "↑" : "↓"}</span>
      )}
    </button>
  );
  const statusCounts = discoveredRows.reduce<Record<MatchDisplayState, number>>(
    (counts, [, selections]) => {
      counts[matchDisplayState(selections[0])] += 1;
      return counts;
    },
    { finished: 0, live: 0, scheduled: 0, postponed: 0, cancelled: 0 },
  );
  const filterButton = (
    key: "all" | MatchDisplayState,
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
    <section className="selection-panel all-matches-panel">
      <div className="section-heading">
        <div>
          <h2>Q-score candidate matches</h2>
          <span className="section-note">
            Q ≥75 only · final Acca publication also checks odds, edge,
            freshness, data quality, diversification, and ticket construction
          </span>
        </div>
        <span className="section-note match-count-summary">
          <b>{discoveredRows.length}</b> matches
          {statusCounts.finished > 0 && <> · <b>{statusCounts.finished}</b> finished</>}
          {statusCounts.live > 0 && <> · <b>{statusCounts.live}</b> live</>}
          {statusCounts.scheduled > 0 && <> · <b>{statusCounts.scheduled}</b> upcoming</>}
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="panel-empty">
          No Q ≥75 model candidates are available for this date.
        </p>
      ) : (
        <div className="matches-workspace">
          <aside className="match-insights-column" aria-label="Match overview">
            <section className="match-sidebar-card">
              <h3>Predictions</h3>
              <p className="match-sidebar-caption">Qualified candidates for this date</p>
              <div className="match-sidebar-stat"><span>Matches</span><strong>{discoveredRows.length}</strong></div>
              <div className="match-sidebar-stat"><span>Upcoming</span><strong>{statusCounts.scheduled}</strong></div>
              <div className="match-sidebar-stat"><span>In play</span><strong>{statusCounts.live}</strong></div>
            </section>
            <section className="match-sidebar-card">
              <div className="match-sidebar-heading"><h3>Top leagues</h3><button onClick={() => setLeagueFilter("all")}>All</button></div>
              {topLeagues.length === 0 ? <p className="match-sidebar-caption">No competition data</p> : topLeagues.map(([league, count]) => (
                <button key={league} className={`top-league-option${leagueFilter === league ? " active" : ""}`} onClick={() => setLeagueFilter(league)}>
                  <span>{league}</span><b>{count}</b>
                </button>
              ))}
            </section>
          </aside>
          <div className="matches-browser">
          <div className="match-toolbar">
            <div className="match-explorer-controls">
              <label className="match-search"><span className="sr-only">Search team or competition</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Team or competition" /></label>
              <label><span className="sr-only">Filter by league</span><select value={leagueFilter} onChange={(event) => setLeagueFilter(event.target.value)}><option value="all">All competitions</option>{leagues.map((league) => <option key={league} value={league}>{league}</option>)}</select></label>
              <label><span className="sr-only">Filter by market</span><select value={marketFilter} onChange={(event) => setMarketFilter(event.target.value)}><option value="all">All markets</option>{markets.map((market) => <option key={market} value={market}>{market}</option>)}</select></label>
            </div>
            <div className="match-status-filters" aria-label="Filter matches by status">
              {filterButton("all", "All", discoveredRows.length)}
              {filterButton("finished", "Finished", statusCounts.finished)}
              {filterButton("live", "In play", statusCounts.live)}
              {filterButton("scheduled", "Pending", statusCounts.scheduled)}
              {statusCounts.postponed > 0 && filterButton("postponed", "Postponed", statusCounts.postponed)}
              {statusCounts.cancelled > 0 && filterButton("cancelled", "Cancelled", statusCounts.cancelled)}
            </div>
            <div className="match-sort-controls" aria-label="Sort matches">
              <span>Sort</span>
              {sortButton("kickoff", "Kickoff")}
              {sortButton("match", "Match")}
              {sortButton("league", "League")}
              {sortButton("q", "Q-score")}
              {sortButton("markets", "Markets")}
              {sortButton("spread", "Spread")}
            </div>
          </div>
          {sortedRows.length === 0 ? (
            <p className="panel-empty">No qualified matches match these filters.</p>
          ) : (
            <div className="match-card-grid">
              {sortedRows.map(([matchId, selections]) => (
                <MatchMarketGroup
                  key={matchId}
                  selections={selections}
                  onSelect={onSelect}
                  onAdd={onAdd}
                  allowAdd={allowAdd}
                />
              ))}
            </div>
          )}
          </div>
        </div>
      )}
    </section>
  );
}

function MatchMarketGroup({
  selections,
  onSelect,
  onAdd,
  allowAdd,
}: {
  selections: SelectionSummary[];
  onSelect: (row: SelectionSummary) => void;
  onAdd: (row: SelectionSummary) => void;
  allowAdd: boolean;
}) {
  const [sort, setSort] = useState<{
    key: "market" | "q" | "odds" | "spread" | "edge";
    direction: "asc" | "desc";
  }>({ key: "q", direction: "desc" });
  const first = selections[0];
  const displayState = matchDisplayState(first);
  const best = selections.reduce((a, b) => (a.q_score > b.q_score ? a : b));
  const outcomeCounts = selections.reduce(
    (counts, row) => {
      const outcome = row.result ?? "pending";
      counts[outcome] += 1;
      return counts;
    },
    { won: 0, lost: 0, void: 0, pending: 0 },
  );
  const sorted = [...selections].sort((a, b) => {
    const values: Record<typeof sort.key, [string | number, string | number]> =
      {
        market: [formatMarket(a.market), formatMarket(b.market)],
        q: [a.q_score, b.q_score],
        odds: [a.best_odds ?? 0, b.best_odds ?? 0],
        spread: [a.model_agreement ?? 999, b.model_agreement ?? 999],
        edge: [a.edge ?? -999, b.edge ?? -999],
      };
    const [left, right] = values[sort.key];
    const result = left < right ? -1 : left > right ? 1 : 0;
    return sort.direction === "asc" ? result : -result;
  });
  const changeSort = (key: typeof sort.key) =>
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: key === "market" ? "asc" : "desc" },
    );
  const button = (key: typeof sort.key, label: string) => (
    <button
      className={sort.key === key ? "active" : ""}
      onClick={(event) => {
        event.preventDefault();
        changeSort(key);
      }}
    >
      {label}
      {sort.key === key && (
        <span aria-hidden="true"> {sort.direction === "asc" ? "↑" : "↓"}</span>
      )}
    </button>
  );
  return (
    <details className={`match-accordion match-card ${displayState}`}>
      <summary className="match-card-summary">
        <div className="match-card-heading">
          <div className="summary-match">
            <small className="summary-league">{first.competition}</small>
            <strong>
              {first.home_team} <span className="leg-match-vs">vs</span>{" "}
              {first.away_team}
            </strong>
          </div>
          <small className={`summary-match-status ${displayState}`}>
            {matchStatusText(first)}
          </small>
        </div>
        <div className="match-card-metrics">
          <span><small>Kickoff</small><b>{formatKickoff(first.kickoff_at)}</b></span>
          <span><small>Q-score</small><b>Q {best.q_score.toFixed(1)}</b></span>
          <span><small>Markets</small><b>{selections.length}</b></span>
          <span><small>Spread</small><b className={`spread-badge ${spreadTone(best.model_agreement)}`}>{best.model_agreement == null ? "—" : `${(best.model_agreement * 100).toFixed(1)} pp`}</b></span>
        </div>
        <div className="match-card-statuses" aria-label="Match and market outcomes">
          {displayState === "live" && <b className="match-result-badge inplay">In play</b>}
          {displayState === "scheduled" && <b className="match-result-badge pending">Pending</b>}
          {displayState === "postponed" && <b className="match-result-badge void">Postponed</b>}
          {displayState === "cancelled" && <b className="match-result-badge void">Cancelled</b>}
          {displayState === "finished" && outcomeCounts.won > 0 && <b className="match-result-badge won">{outcomeCounts.won > 1 ? `${outcomeCounts.won} Won` : "Won"}</b>}
          {displayState === "finished" && outcomeCounts.lost > 0 && <b className="match-result-badge lost">{outcomeCounts.lost > 1 ? `${outcomeCounts.lost} Lost` : "Lost"}</b>}
          {displayState === "finished" && outcomeCounts.void > 0 && <b className="match-result-badge void">{outcomeCounts.void > 1 ? `${outcomeCounts.void} Void` : "Void"}</b>}
          {displayState === "finished" && outcomeCounts.pending > 0 && <b className="match-result-badge pending">{outcomeCounts.pending > 1 ? `${outcomeCounts.pending} Pending` : "Pending"}</b>}
          <span className="match-card-expand">View markets <b aria-hidden="true">⌄</b></span>
        </div>
      </summary>
      <div className="market-table">
        <div className="market-table-head">
          <span className="market-sort-label">Sort markets</span>
          {button("market", "Market")}
          {button("q", "Q-score")}
          {button("odds", "Captured odds")}
          {button("spread", "Spread")}
          {button("edge", "Edge")}
        </div>
        {sorted.map((row) => {
          const blockers = accumulatorBlockers(row);
          const freshness = quoteFreshness(row);
          return <div className={`market-row${blockers.length > 0 ? " blocked" : ""}`} key={row.prediction_id}>
            <div>
              <strong>{formatMarket(row.market)}</strong>
              <small>
                {formatSelection(row.selection)}{" "}
                <b className={`grade-badge grade-${gradeTone(row.q_grade)}`}>
                  {row.q_grade ?? "Qualified"}
                </b>
                <b className={`market-result ${row.result ?? "pending"}`}>{row.result === "won" ? "Won" : row.result === "lost" ? "Lost" : row.result === "void" ? "Void" : "Pending"}</b>
              </small>
              <div className="market-chance-evidence">
                <span>Model chance <b>{percent(row.model_probability)}</b></span>
                <span>Bookmaker implied <b>{percent(row.source_implied_probability ?? row.avg_implied)}</b> <em>margin included</em></span>
              </div>
            </div>
            <span className="market-stat">
              <small>Q-score</small>
              <b>Q {row.q_score.toFixed(1)}</b>
            </span>
            <span className="market-stat">
              <small>Captured odds</small>
              <b>{row.best_odds ? `${row.best_odds.toFixed(2)}×` : "—"}</b>
              <small className="market-quote-meta">{row.source_bookmaker ?? "Bookmaker unavailable"} · {quoteTime(row.source_odds_at)} · <b className={freshness.state}>{freshness.label}</b></small>
            </span>
            <span
              className={`market-stat spread-cell ${spreadTone(row.model_agreement)}`}
              title={`Model spread: ${spreadLabel(row.model_agreement)}`}
            >
              <small>Spread</small>
              <b>
                {row.model_agreement == null
                  ? "—"
                  : `${(row.model_agreement * 100).toFixed(1)} pp`}
              </b>
            </span>
            <span className={`market-stat ${row.edge && row.edge > 0 ? "positive" : ""}`}>
              <small>Edge</small>
              <b>{row.edge != null ? `${(row.edge * 100).toFixed(1)}%` : "—"}</b>
            </span>
            <div className="market-row-actions">
              {allowAdd && <button
                className="btn-ghost btn-sm"
                onClick={() => onAdd(row)}
                disabled={blockers.length > 0}
                title={blockers.length > 0 ? `Unavailable: ${blockers.map(reasonLabel).join(", ")}` : "Add to personal accumulator"}
              >
                {blockers.length > 0 ? "Unavailable" : "Add"}
              </button>}
              <button className="btn-ghost btn-sm" onClick={() => onSelect(row)}>
                Details
              </button>
            </div>
            {blockers.length > 0 && <small className="market-row-blockers">Not addable · {blockers.map(reasonLabel).join(" · ")}</small>}
          </div>
        })}
      </div>
    </details>
  );
}
