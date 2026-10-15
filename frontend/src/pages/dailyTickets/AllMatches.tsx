import { useEffect, useState } from "react";
import type { MatchFilterCriteria, MatchFilterPreset, SelectionSummary } from "../../lib/api";
import {
  createMatchFilterPreset,
  deleteMatchFilterPreset,
  fetchMatchFilterPresets,
  formatKickoff,
  formatMarket,
  selectionRepeatsMarket,
  formatSelection,
} from "../../lib/api";
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
import { useFavoriteMatches } from "./useFavoriteMatches";

type MatchSortKey = "match" | "kickoff" | "league" | "q" | "markets" | "spread" | "edge";
const MATCH_STATUSES: MatchDisplayState[] = ["finished", "live", "scheduled", "postponed", "cancelled"];
const MATCH_SORT_KEYS: MatchSortKey[] = ["match", "kickoff", "league", "q", "markets", "spread", "edge"];

function matchUrlParam(name: string) {
  return new URLSearchParams(window.location.search).get(`match_${name}`);
}

function numberParam(name: string, fallback = "") {
  const value = matchUrlParam(name);
  if (value == null || value.trim() === "") return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? String(parsed) : fallback;
}

function numberOrNull(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

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
  const [statusFilter, setStatusFilter] = useState<"all" | MatchDisplayState>(() => {
    const value = matchUrlParam("status");
    return value && MATCH_STATUSES.includes(value as MatchDisplayState) ? value as MatchDisplayState : "all";
  });
  const [query, setQuery] = useState(() => matchUrlParam("q") ?? "");
  const [leagueFilter, setLeagueFilter] = useState(() => matchUrlParam("league") ?? "all");
  const [marketFilter, setMarketFilter] = useState(() => matchUrlParam("market") ?? "all");
  const [favoriteOnly, setFavoriteOnly] = useState(() => matchUrlParam("favorites") === "1");
  const [minProbability, setMinProbability] = useState(() => numberParam("pmin"));
  const [maxProbability, setMaxProbability] = useState(() => numberParam("pmax"));
  const [minOdds, setMinOdds] = useState(() => numberParam("omin"));
  const [maxOdds, setMaxOdds] = useState(() => numberParam("omax"));
  const [minEdge, setMinEdge] = useState(() => numberParam("emin"));
  const [minQScore, setMinQScore] = useState(() => numberParam("qmin", "75"));
  const [maxQScore, setMaxQScore] = useState(() => numberParam("qmax"));
  const { favorites, favoriteIds, toggleFavorite } = useFavoriteMatches();
  const [presets, setPresets] = useState<MatchFilterPreset[]>([]);
  const [presetId, setPresetId] = useState("");
  const [presetName, setPresetName] = useState("");
  const [presetMessage, setPresetMessage] = useState("");
  const [sort, setSort] = useState<{ key: MatchSortKey; direction: "asc" | "desc" }>(() => {
    const key = matchUrlParam("sort");
    const direction = matchUrlParam("order");
    return {
      key: key && MATCH_SORT_KEYS.includes(key as MatchSortKey) ? key as MatchSortKey : "kickoff",
      direction: direction === "desc" ? "desc" : "asc",
    };
  });
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    const url = new URL(window.location.href);
    const params: Record<string, string | null> = {
      match_q: query.trim() || null,
      match_league: leagueFilter === "all" ? null : leagueFilter,
      match_market: marketFilter === "all" ? null : marketFilter,
      match_status: statusFilter === "all" ? null : statusFilter,
      match_favorites: favoriteOnly ? "1" : null,
      match_pmin: minProbability || null,
      match_pmax: maxProbability || null,
      match_omin: minOdds || null,
      match_omax: maxOdds || null,
      match_emin: minEdge || null,
      match_qmin: minQScore === "75" ? null : minQScore || null,
      match_qmax: maxQScore || null,
      match_sort: sort.key === "kickoff" && sort.direction === "asc" ? null : sort.key,
      match_order: sort.key === "kickoff" && sort.direction === "asc" ? null : sort.direction,
    };
    for (const [key, value] of Object.entries(params)) {
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  }, [query, leagueFilter, marketFilter, statusFilter, favoriteOnly, minProbability, maxProbability, minOdds, maxOdds, minEdge, minQScore, maxQScore, sort]);
  useEffect(() => {
    let active = true;
    fetchMatchFilterPresets().then((items) => {
      if (active) {
        setPresets(items);
      }
    }).catch(() => {
      if (active) setPresetMessage("Saved filters could not be loaded.");
    });
    return () => { active = false; };
  }, []);
  const leagues = [...new Set(rows.map(([, selections]) => selections[0]?.competition).filter(Boolean))].sort();
  const leagueCounts = rows.reduce<Record<string, number>>((counts, [, selections]) => {
    const league = selections[0]?.competition;
    if (league) counts[league] = (counts[league] ?? 0) + 1;
    return counts;
  }, {});
  const topLeagues = Object.entries(leagueCounts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 8);
  const markets = [...new Set(rows.flatMap(([, selections]) => selections.map((row) => formatMarket(row.market))))].sort();
  const marketCounts = rows.reduce<Record<string, number>>((counts, [, selections]) => {
    for (const market of new Set(selections.map((row) => formatMarket(row.market)))) {
      counts[market] = (counts[market] ?? 0) + 1;
    }
    return counts;
  }, {});
  const quickMarkets = Object.entries(marketCounts)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 4)
    .map(([market]) => market);
  const queryText = query.trim().toLocaleLowerCase();
  const discoveredRows = rows.flatMap(([id, selections]) => {
    const first = selections[0];
    const matchesText = !queryText || `${first.home_team} ${first.away_team} ${first.competition}`.toLocaleLowerCase().includes(queryText);
    const marketRows = (marketFilter === "all" ? selections : selections.filter((row) => formatMarket(row.market) === marketFilter))
      .filter((row) => {
        const probability = numberOrNull(minProbability);
        const probabilityMax = numberOrNull(maxProbability);
        const odds = numberOrNull(minOdds);
        const oddsMax = numberOrNull(maxOdds);
        const edge = numberOrNull(minEdge);
        const qMin = numberOrNull(minQScore) ?? 75;
        const qMax = numberOrNull(maxQScore);
        if (probability != null && (row.model_probability == null || row.model_probability < probability / 100)) return false;
        if (probabilityMax != null && (row.model_probability == null || row.model_probability > probabilityMax / 100)) return false;
        if (odds != null && (row.best_odds == null || row.best_odds < odds)) return false;
        if (oddsMax != null && (row.best_odds == null || row.best_odds > oddsMax)) return false;
        if (edge != null && (row.edge == null || row.edge < edge / 100)) return false;
        if (row.q_score < qMin || (qMax != null && row.q_score > qMax)) return false;
        return true;
      });
    if (!matchesText || (leagueFilter !== "all" && first.competition !== leagueFilter) || marketRows.length === 0) return [];
    return [[id, marketRows] as [number, SelectionSummary[]]];
  });
  const filteredRows = (statusFilter === "all"
    ? discoveredRows
    : discoveredRows.filter(([, selections]) => matchDisplayState(selections[0]) === statusFilter))
    .filter(([matchId]) => !favoriteOnly || favoriteIds.has(matchId));
  const activeFilterCount = Number(Boolean(query.trim()))
    + Number(leagueFilter !== "all")
    + Number(marketFilter !== "all")
    + Number(statusFilter !== "all")
    + Number(favoriteOnly)
    + Number(Boolean(minProbability || maxProbability || minOdds || maxOdds || minEdge || (minQScore && minQScore !== "75") || maxQScore));
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
        edge: [Math.max(...a.map((row) => row.edge ?? -999)), Math.max(...b.map((row) => row.edge ?? -999))],
      };
    const [first, second] = values[sort.key];
    const result = first < second ? -1 : first > second ? 1 : 0;
    return sort.direction === "asc" ? result : -result;
  });
  const changeSort = (key: MatchSortKey) =>
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: key === "kickoff" ? "asc" : "desc" },
    );
  const clearFilters = () => {
    setStatusFilter("all");
    setQuery("");
    setLeagueFilter("all");
    setMarketFilter("all");
    setFavoriteOnly(false);
    setMinProbability("");
    setMaxProbability("");
    setMinOdds("");
    setMaxOdds("");
    setMinEdge("");
    setMinQScore("75");
    setMaxQScore("");
    setPresetId("");
  };
  const currentCriteria: MatchFilterCriteria = {
    query: query.trim(),
    competition: leagueFilter,
    market: marketFilter,
    status: statusFilter,
    favorite_only: favoriteOnly,
    min_probability: numberOrNull(minProbability) == null ? null : Number(minProbability) / 100,
    max_probability: numberOrNull(maxProbability) == null ? null : Number(maxProbability) / 100,
    min_odds: numberOrNull(minOdds),
    max_odds: numberOrNull(maxOdds),
    min_edge: numberOrNull(minEdge) == null ? null : Number(minEdge) / 100,
    min_q_score: numberOrNull(minQScore) ?? 75,
    max_q_score: numberOrNull(maxQScore),
  };
  const applyPreset = (preset: MatchFilterPreset) => {
    const criteria = preset.criteria;
    setQuery(criteria.query);
    setLeagueFilter(criteria.competition);
    setMarketFilter(criteria.market);
    setStatusFilter(criteria.status);
    setFavoriteOnly(criteria.favorite_only);
    setMinProbability(criteria.min_probability == null ? "" : String(criteria.min_probability * 100));
    setMaxProbability(criteria.max_probability == null ? "" : String(criteria.max_probability * 100));
    setMinOdds(criteria.min_odds == null ? "" : String(criteria.min_odds));
    setMaxOdds(criteria.max_odds == null ? "" : String(criteria.max_odds));
    setMinEdge(criteria.min_edge == null ? "" : String(criteria.min_edge * 100));
    setMinQScore(String(criteria.min_q_score));
    setMaxQScore(criteria.max_q_score == null ? "" : String(criteria.max_q_score));
  };
  const savePreset = async () => {
    if (!presetName.trim()) return;
    setPresetMessage("");
    try {
      const preset = await createMatchFilterPreset(presetName.trim(), currentCriteria);
      setPresets((current) => [preset, ...current]);
      setPresetId(String(preset.id));
      setPresetName("");
      setPresetMessage("Filter saved to your account.");
    } catch {
      setPresetMessage("Could not save this filter. Check your connection and try again.");
    }
  };
  const removePreset = async () => {
    const selected = presets.find((item) => String(item.id) === presetId);
    if (!selected) return;
    setPresetMessage("");
    try {
      await deleteMatchFilterPreset(selected.id);
      setPresets((current) => current.filter((item) => item.id !== selected.id));
      setPresetId("");
      setPresetMessage("Saved filter removed.");
    } catch {
      setPresetMessage("Could not remove this filter. Check your connection and try again.");
    }
  };
  const copyViewLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
    window.setTimeout(() => setCopyState("idle"), 2000);
  };
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
          Showing <b>{sortedRows.length}</b> of <b>{rows.length}</b> matches
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
              <div className="market-quick-filters" role="group" aria-label="Quick market filters">
                <button className={marketFilter === "all" ? "active" : ""} onClick={() => setMarketFilter("all")} aria-pressed={marketFilter === "all"}>All markets</button>
                {quickMarkets.map((market) => (
                  <button key={market} className={marketFilter === market ? "active" : ""} onClick={() => setMarketFilter(market)} aria-pressed={marketFilter === market}>
                    {market}<span>{marketCounts[market]}</span>
                  </button>
                ))}
              </div>
              {markets.some((market) => !quickMarkets.includes(market)) && (
                <label><span className="sr-only">Filter by another market</span><select value={quickMarkets.includes(marketFilter) || marketFilter === "all" ? "" : marketFilter} onChange={(event) => { if (event.target.value) setMarketFilter(event.target.value); }}>
                  <option value="">More markets…</option>{markets.filter((market) => !quickMarkets.includes(market)).map((market) => <option key={market} value={market}>{market}</option>)}
                </select></label>
              )}
            </div>
            <div className="match-status-filters" aria-label="Filter matches by status">
              {filterButton("all", "All", discoveredRows.length)}
              {filterButton("finished", "Finished", statusCounts.finished)}
              {filterButton("live", "In play", statusCounts.live)}
              {filterButton("scheduled", "Pending", statusCounts.scheduled)}
              {statusCounts.postponed > 0 && filterButton("postponed", "Postponed", statusCounts.postponed)}
              {statusCounts.cancelled > 0 && filterButton("cancelled", "Cancelled", statusCounts.cancelled)}
              <button className={`fixture-favorites-filter${favoriteOnly ? " active" : ""}`} onClick={() => setFavoriteOnly((current) => !current)} aria-pressed={favoriteOnly} title="Save matches to your account when signed in">
                <span aria-hidden="true">★</span> Favourites <b>{favorites.length}</b>
              </button>
            </div>
            <div className="match-sort-controls" aria-label="Sort matches">
              <span>Sort</span>
              {sortButton("kickoff", "Kickoff")}
              {sortButton("match", "Match")}
              {sortButton("league", "League")}
              {sortButton("q", "Q-score")}
              {sortButton("markets", "Markets")}
              {sortButton("spread", "Spread")}
              {sortButton("edge", "Model edge")}
            </div>
            <button className="match-clear-filters" onClick={clearFilters} disabled={activeFilterCount === 0}>
              Clear filters{activeFilterCount > 0 ? ` · ${activeFilterCount}` : ""}
            </button>
            <button className="match-copy-link" onClick={copyViewLink} title="Copy a link with the selected date and filters">
              {copyState === "copied" ? "Link copied" : copyState === "failed" ? "Copy unavailable" : "Copy view link"}
            </button>
            <div className="match-filter-presets">
              <label><span>Saved filters</span><select value={presetId} onChange={(event) => {
                setPresetId(event.target.value);
                const selected = presets.find((item) => String(item.id) === event.target.value);
                if (selected) applyPreset(selected);
              }}>
                <option value="">Choose a saved filter</option>
                {presets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}
              </select></label>
              <label><span className="sr-only">Name this filter</span><input value={presetName} onChange={(event) => setPresetName(event.target.value)} maxLength={80} placeholder="Name this filter" /></label>
              <button className="match-copy-link" onClick={savePreset} disabled={!presetName.trim()}>Save filter</button>
              <button className="match-clear-filters" onClick={removePreset} disabled={!presetId}>Delete saved filter</button>
              {presetMessage && <span className="match-filter-message" role="status">{presetMessage}</span>}
            </div>
            <div className="match-advanced-filters" aria-label="Advanced match filters">
              <label><span>Model probability min %</span><input type="number" min="0" max="100" step="1" value={minProbability} onChange={(event) => setMinProbability(event.target.value)} placeholder="Any" /></label>
              <label><span>Model probability max %</span><input type="number" min="0" max="100" step="1" value={maxProbability} onChange={(event) => setMaxProbability(event.target.value)} placeholder="Any" /></label>
              <label><span>Odds min</span><input type="number" min="1" max="1000" step="0.01" value={minOdds} onChange={(event) => setMinOdds(event.target.value)} placeholder="Any" /></label>
              <label><span>Odds max</span><input type="number" min="1" max="1000" step="0.01" value={maxOdds} onChange={(event) => setMaxOdds(event.target.value)} placeholder="Any" /></label>
              <label><span>Edge min %</span><input type="number" min="-100" max="100" step="0.1" value={minEdge} onChange={(event) => setMinEdge(event.target.value)} placeholder="Any" /></label>
              <label><span>Q-score min</span><input type="number" min="0" max="100" step="1" value={minQScore} onChange={(event) => setMinQScore(event.target.value)} /></label>
              <label><span>Q-score max</span><input type="number" min="0" max="100" step="1" value={maxQScore} onChange={(event) => setMaxQScore(event.target.value)} placeholder="Any" /></label>
            </div>
          </div>
          {sortedRows.length === 0 ? (
            <p className="panel-empty">{favoriteOnly && favorites.length === 0 ? "No favourites yet. Select a star beside a match to save it here." : "No qualified matches match these filters."}</p>
          ) : (
            <div className="match-card-grid">
              {sortedRows.map(([matchId, selections]) => (
                <MatchMarketGroup
                  key={matchId}
                  selections={selections}
                  onSelect={onSelect}
                  onAdd={onAdd}
                  allowAdd={allowAdd}
                  isFavorite={favoriteIds.has(matchId)}
                  onToggleFavorite={() => toggleFavorite(matchId)}
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
  isFavorite,
  onToggleFavorite,
}: {
  selections: SelectionSummary[];
  onSelect: (row: SelectionSummary) => void;
  onAdd: (row: SelectionSummary) => void;
  allowAdd: boolean;
  isFavorite: boolean;
  onToggleFavorite: () => void;
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
          <button
            type="button"
            className={`match-favorite${isFavorite ? " active" : ""}`}
            onClick={(event) => { event.preventDefault(); event.stopPropagation(); onToggleFavorite(); }}
            aria-label={`${isFavorite ? "Remove" : "Add"} ${first.home_team} vs ${first.away_team} ${isFavorite ? "from" : "to"} favourites`}
            aria-pressed={isFavorite}
            title={isFavorite ? "Remove from favourites" : "Add to favourites"}
          >★</button>
        </div>
        <div className="match-card-metrics">
          <span><small>Kickoff</small><b className="kickoff-time">{formatKickoff(first.kickoff_at)}</b></span>
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
              {!selectionRepeatsMarket(row.market, row.selection) && <strong>{formatMarket(row.market)}</strong>}
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
