import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../../auth";
import type { CustomAccumulator as ApiCustomAccumulator, DailyTickets, Leg, MatchFixture, SelectionSummary, Ticket } from "../../lib/api";
import {
  createCustomAccumulator,
  fetchDailyTickets,
  fetchMatchFixtures,
  fetchQualifiedSelections,
  fetchRejectedSelections,
  fetchCustomAccumulators,
  formatDate,
  formatKickoff,
  formatMarket,
  formatSelection,
  selectionRepeatsMarket,
  selectionTone,
  updateCustomAccumulator,
  formatStage,
} from "../../lib/api";
import TicketCard from "../../components/TicketCard";
import SelectionDetail, {
  type DetailSelection,
} from "../../components/SelectionDetail";
import { BandMixPicks } from "./BandMixPicks";
import { ParameterSweepPicks } from "./ParameterSweepPicks";
import { fetchDailyMarketRoi, MarketRoiDaily, type MarketRoiDailyData } from "./MarketRoiDaily";
import { TicketGridSkeleton } from "./TicketGridSkeleton";
import { AllMatches } from "./AllMatches";
import { FixtureExplorer } from "./FixtureExplorer";
import {
  accumulatorBlockers,
  apiAccumulatorToUi,
  reasonLabel,
  TODAY,
  withRejectionEvidence,
} from "./helpers";
import type { CustomAccumulator, DailyTab, DailyTicketsPageProps } from "./types";

export default function DailyTicketsPage({ date, initialTab = "tickets", onOpenMatch, onOpenTracker }: DailyTicketsPageProps & { onOpenMatch?: (matchId: number) => void }) {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const canExploreMarkets = isAdmin || user?.plan === "pro";
  const [data, setData] = useState<DailyTickets | null>(null);
  const [rejected, setRejected] = useState<SelectionSummary[]>([]);
  const [matches, setMatches] = useState<SelectionSummary[]>([]);
  const [marketRoi, setMarketRoi] = useState<MarketRoiDailyData | null>(null);
  const [marketRoiError, setMarketRoiError] = useState<string | null>(null);
  const [fixtures, setFixtures] = useState<MatchFixture[]>([]);
  const [tab, setTab] = useState<DailyTab>(initialTab);
  const [custom, setCustom] = useState<CustomAccumulator[]>(() => {
    return [];
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<{
    selection: DetailSelection;
    modelVersion?: string;
    pricing?: "market" | "model";
    publishedAt?: string;
  } | null>(null);
  const [selectionMessage, setSelectionMessage] = useState<string | null>(null);
  const [bankroll, setBankroll] = useState("");

  const suggestedStake = (ticket: Ticket | null) => {
    const bank = Number(bankroll);
    if (!ticket || !(bank > 0) || ticket.combined_odds == null || ticket.adjusted_probability == null || ticket.combined_odds <= 1) return null;
    const b = ticket.combined_odds - 1;
    const kelly =
      (ticket.adjusted_probability * b - (1 - ticket.adjusted_probability)) / b;
    return Math.min(Math.max(0, kelly * 0.5 * bank), bank * 0.05);
  };

  const load = useCallback(async (targetDate: string) => {
    setLoading(true);
    setError(null);
    if (isAdmin) {
      setMarketRoi(null);
      setMarketRoiError(null);
    }
    try {
      const [tickets, fixtureRows, allRows, rejectedRows, customRows, marketRoiRows] = await Promise.all([
        fetchDailyTickets(targetDate),
        fetchMatchFixtures(targetDate).catch(() => []),
        canExploreMarkets ? fetchQualifiedSelections(targetDate) : Promise.resolve([]),
        isAdmin ? fetchRejectedSelections(targetDate) : Promise.resolve([]),
        isAdmin && initialTab === "matches" ? fetchCustomAccumulators(targetDate) : Promise.resolve([]),
        isAdmin
          ? fetchDailyMarketRoi(targetDate).then((value) => {
              setMarketRoiError(null);
              return value;
            }).catch((cause) => {
              setMarketRoi(null);
              setMarketRoiError(cause instanceof Error ? cause.message : "Daily market ROI is unavailable");
              return null;
            })
          : Promise.resolve(null),
      ]);
      const evidencedRows = withRejectionEvidence(allRows, rejectedRows);
      setData(tickets);
      setFixtures(fixtureRows);
      setMatches(evidencedRows);
      setMarketRoi(marketRoiRows);
      setRejected(rejectedRows);
      setCustom(customRows.map(apiAccumulatorToUi));
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Failed to load research ledger",
      );
    } finally {
      setLoading(false);
    }
  }, [canExploreMarkets, isAdmin]);

  useEffect(() => {
    load(date);
    const timer = window.setInterval(() => load(date), 300_000);
    return () => window.clearInterval(timer);
  }, [date, load]);
  const replaceCustom = (row: ApiCustomAccumulator) => {
    const uiRow = apiAccumulatorToUi(row);
    setCustom((current) => [...current.filter((item) => item.id !== uiRow.id), uiRow]);
  };

  const addOrUpdateCustom = async (
    ticket: CustomAccumulator | null,
    name: string,
    legs: SelectionSummary[],
    stake: number | null,
    status: "draft" | "placed",
  ) => {
    const payload = {
      name,
      target_date: date,
      prediction_ids: legs.map((leg) => leg.prediction_id),
      stake,
      status,
    } as const;
    const saved = ticket
      ? await updateCustomAccumulator(ticket.id, payload)
      : await createCustomAccumulator(payload);
    replaceCustom(saved);
    return apiAccumulatorToUi(saved);
  };

  const addToAccumulator = async (row: SelectionSummary) => {
    const blockers = accumulatorBlockers(row);
    if (blockers.length > 0) {
      setSelectionMessage(`Selection not added: ${blockers.map(reasonLabel).join(", ")}`);
      return;
    }
    const current = custom.find(
      (ticket) => ticket.date === date && ticket.status === "draft" && !ticket.automatic,
    );
    if (current) {
      if (current.legs.some((leg) => leg.match_id === row.match_id)) return;
      await addOrUpdateCustom(current, current.name, [...current.legs, row], current.stake, "draft");
    } else {
      await addOrUpdateCustom(null, `${formatDate(date)} accumulator`, [row], null, "draft");
    }
    setSelectionMessage(null);
    onOpenTracker?.();
  };

  const openTicketLeg = (leg: Leg, ticket: Ticket) => {
    setDetail({
      selection: leg,
      modelVersion: ticket.model_version,
      publishedAt: ticket.published_at,
      pricing: ticket.pricing,
    });
  };

  const openSelection = (selection: SelectionSummary) => {
    setDetail({
      selection: {
        ...selection,
        source_odds_at: selection.source_odds_at ?? null,
        result: selection.result ?? undefined,
      },
    });
  };

  // Keep all public-page status and publication metadata scoped to active
  // release tiers.
  const publicPublishedTickets = data
    ? [
        data.conservative,
        data.balanced,
        data.high_odds,
      ].filter((ticket): ticket is Ticket => ticket != null)
    : [];
  const dailyTabs: [DailyTab, string][] = [
    ...(initialTab === "tickets" ? [["tickets", "Recommendations"] as [DailyTab, string]] : []),
    ...(initialTab === "matches" ? [["matches", "Match explorer"] as [DailyTab, string]] : []),
    ...(isAdmin && initialTab === "odds-policy" ? [
      ["best-mix", "Best-mix picks"],
      ["odds-policy", "Research policy"],
      ["market-roi", "Market ROI"],
      ["passed", "Passed / why"],
    ] as [DailyTab, string][] : []),
  ];
  const publicationTimes = publicPublishedTickets
    .map((ticket) => ticket.published_at)
    .filter(Boolean)
    .sort();
  const latestPublication = publicationTimes[publicationTimes.length - 1];
  const modelVersions = [
    ...new Set(publicPublishedTickets.map((ticket) => ticket.model_version)),
  ];
  const qScoreMatchCount = new Set(matches.map((row) => row.match_id)).size;
  const q85Count = matches.filter((row) => row.q_score >= 85).length;
  const addableCount = matches.filter((row) => accumulatorBlockers(row).length === 0).length;
  const rejectionReasonCounts = matches.reduce<Record<string, number>>((counts, row) => {
    for (const reason of row.reason_codes ?? []) counts[reason] = (counts[reason] ?? 0) + 1;
    return counts;
  }, {});
  const leadingRejectionReasons = Object.entries(rejectionReasonCounts)
    .sort(([, left], [, right]) => right - left)
    .slice(0, 4);
  const generationFinishedWithoutTickets = Boolean(
    data?.generation_id != null &&
      ["completed", "partial", "failed"].includes(
        data.generation_status ?? "",
      ) &&
      data.generated_ticket_count === 0,
  );
  const generationUnavailable = Boolean(
    data && data.generation_id == null && publicPublishedTickets.length === 0,
  );
  const pipelineActive = Boolean(
    data?.pipeline_run_id != null &&
      ["pending", "running"].includes(data.pipeline_status ?? ""),
  );
  const pipelineStateTitle = pipelineActive
    ? "The full ticket pipeline is running automatically."
    : data?.pipeline_run_id != null
      ? "The latest full pipeline stopped before ticket generation."
      : date === TODAY
        ? "No full pipeline has run yet; automatic recovery is enabled."
        : "No full pipeline was recorded for this date.";
  const pipelineStateBadge = pipelineActive
    ? "Pipeline running"
    : data?.pipeline_run_id != null
      ? "Pipeline incomplete"
      : "Pipeline not run";

  // Describe each tier by the rules its generation actually applied.
  const fairPriced = data?.pricing === "market";
  const publicTiers = [
    {
      key: "conservative" as const,
      name: "Conservative",
      desc: fairPriced ? "Most likely ticket · 3–4 legs · 2.0–3.0×" : "Q ≥85 · 3–6 legs · 3–5×",
      color: "var(--conservative)",
    },
    {
      key: "balanced" as const,
      name: "Balanced",
      desc: fairPriced ? "Most likely ticket · 3–5 legs · 3.0–5.0×" : "Q ≥80 · 60% Grade A/A+",
      color: "var(--balanced)",
    },
    {
      key: "high_odds" as const,
      name: "High Odds",
      desc: fairPriced ? "Higher-risk ticket · 2–8 legs · 5.0×+" : "Higher-risk research ticket",
      color: "var(--high-odds)",
    },
  ];

  const roiCandidateKeys = new Set((marketRoi?.candidates ?? []).map((row) => `${row.match_id}|${row.market}|${row.selection}`));
  const roiGatedMatches = isAdmin
    ? matches.filter((row) => roiCandidateKeys.has(`${row.match_id}|${row.market}|${row.selection}`))
    : matches;
  const qualifyingMarketLabels = marketRoi?.qualifying_markets
    .map((market) => market.replace(/_(\d+)_(\d+)/g, " $1.$2").replace(/_/g, " ")) ?? [];
  const decisionRows = roiGatedMatches
    .filter((row) => row.best_odds != null)
    .sort((left, right) => (right.edge ?? -1) - (left.edge ?? -1))
    .slice(0, 6);
  const matchGroups = Object.entries(roiGatedMatches.reduce<Record<number, SelectionSummary[]>>((groups, row) => {
    (groups[row.match_id] ??= []).push(row);
    return groups;
  }, {})).map(([id, rows]) => [Number(id), rows] as [number, SelectionSummary[]]);

  return (
    <>
      <div className="meta-bar">
        <div>
          <h1 className="meta-date">
            <span className="event-date">{data ? formatDate(data.target_date) : formatDate(date)}</span>
          </h1>
          <p className="research-disclaimer">
            {isAdmin
              ? "Internal research and paper trading only. No ticket is a promise of profit."
              : "Recommendations are informational. Football outcomes are uncertain; no result is guaranteed."}
          </p>
        </div>
        {isAdmin && data && (
          <div className="meta-context" aria-label="Publication context">
            <span className="meta-pool">
              {data.qualified_pool} strict candidates
            </span>
            {(data.horizon_pool ?? 0) > 0 && (
              <span title="Additional candidates admitted by the bounded rolling-horizon fallback">
                +{data.horizon_pool} rolling-horizon
              </span>
            )}
            {Object.keys(data.relaxed_ticket_types ?? {}).length > 0 && (
              <span title="One or more tiers used the bounded relaxation policy">
                Bounded relaxation
              </span>
            )}
            {latestPublication && (
              <span>
                Published{" "}
                {new Date(latestPublication).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            )}
            {modelVersions.length > 0 && (
              <span>Model {modelVersions.join(", ")}</span>
            )}
          </div>
        )}
      </div>

      {error && (
        <div className="error-bar" role="alert">
          <div>
            <strong>{isAdmin ? "Internal research ledger unavailable" : "Recommendations unavailable"}</strong>
            <span>{error}</span>
          </div>
          <button onClick={() => load(date)}>Try again</button>
        </div>
      )}

      {loading ? (
        <TicketGridSkeleton />
      ) : (
        <>
          <nav className="daily-tabs" aria-label="Daily ticket workspace">
            {dailyTabs.map(([key, label]) => (
              <button
                key={key}
                className={tab === key ? "active" : ""}
                onClick={() => setTab(key)}
              >
                {label}
              </button>
            ))}
          </nav>
          {tab === "matches" && selectionMessage && (
            <p className="pipeline-banner-message tickets-operation-message negative" role="status">
              {selectionMessage}
            </p>
          )}
          {/* Legacy candidate tabs intentionally removed from the ticket workspace.
            <div className="quality-legend" aria-label="Quality legend">
              <span className="legend-title">Quality guide</span>
              <button className={`quality-guide-item${qualityFilter === "A+" ? " active" : ""}`} onClick={() => { setQualityFilter(qualityFilter === "A+" ? null : "A+"); setTab("matches") }}>
                <b className="grade-badge grade-a-plus">A+</b> strongest
              </button>
              <button className={`quality-guide-item${qualityFilter === "A" ? " active" : ""}`} onClick={() => { setQualityFilter(qualityFilter === "A" ? null : "A"); setTab("matches") }}><b className="grade-badge grade-a">A</b> high confidence</button>
              <button className={`quality-guide-item${qualityFilter === "B+" ? " active" : ""}`} onClick={() => { setQualityFilter(qualityFilter === "B+" ? null : "B+"); setTab("matches") }}><b className="grade-badge grade-b-plus">B+</b> qualified</button>
              <button className={`quality-guide-item${qualityFilter === "B" ? " active" : ""}`} onClick={() => { setQualityFilter(qualityFilter === "B" ? null : "B"); setTab("matches") }}><b className="grade-badge grade-b">B</b> acceptable</button>
              <span>
                <b className="spread-dot strong" /> ≤5 pp aligned
              </span>
              <span>
                <b className="spread-dot caution" /> 10–15 pp caution
              </span>
              <span>
                <b className="spread-dot high" /> &gt;15 pp downgrade
              </span>
            </div>
          */}
          {tab === "tickets" && <div className="settlement-legend" aria-label="Ticket and settlement definitions"><span><b>Score</b> = match result</span><span><b>Market</b> = selected outcome</span><span><b>Ticket</b> = accumulator result</span>{publicPublishedTickets.some((ticket) => ticket.pricing === "market") && <span>Market-priced chance removes bookmaker margin; expected return estimates long-run return per unit staked.</span>}</div>}
          {tab === "matches" && canExploreMarkets && <div className="view-intro"><div><span className="eyebrow">Research candidates</span><h2 className="section-title">Qualified match markets</h2></div><p>These candidate signals are for inspection. A match appearing here does not mean it was selected for a published ticket.</p></div>}
          {isAdmin && tab === "matches" && marketRoi && (
            <div className="market-roi-match-gate" role="status">
              <b>Daily ROI market gate</b>
              <span>
                Showing today’s Q-score matches only when the exact market and selection have a valid pre-kickoff BET/WATCH quote and their historical market ROI is at least 20%. Quote age is shown in Market ROI.
                {qualifyingMarketLabels.length > 0
                  ? ` Qualifying markets: ${qualifyingMarketLabels.join(", ")}.`
                  : " No market currently clears the ROI threshold."}
              </span>
            </div>
          )}
          {isAdmin && tab === "matches" && marketRoiError && (
            <div className="market-roi-match-error" role="alert">
              Daily ROI gate unavailable ({marketRoiError}); no market-qualified matches are shown until it refreshes.
            </div>
          )}
          {isAdmin && tab === "matches" && (
            <DecisionSummary date={date} matches={decisionRows} onSelect={openSelection} />
          )}
          {tab === "matches" && canExploreMarkets && <AllMatches rows={matchGroups} onSelect={openSelection} onAdd={addToAccumulator} allowAdd={isAdmin} />}
          {tab === "matches" && !canExploreMarkets && <FixtureExplorer fixtures={fixtures} />}
          {tab === "tickets" && (
            <div className="view-intro">
              <div>
                <span className="eyebrow">{isAdmin ? "Internal view" : "Published output"}</span>
                <h2 className="section-title">Published ticket tiers</h2>
              </div>
              <p>{isAdmin
                ? "These are the immutable ticket versions published for this date. Research candidates are shown under Matches."
                : "Only tickets published through the existing evidence and publication gates are shown."}</p>
            </div>
          )}
          {isAdmin && tab === "best-mix" && (
            <BandMixPicks
              date={date}
              rejectedRows={rejected}
              onSelect={openSelection}
              onAdd={addToAccumulator}
            />
          )}
          {isAdmin && tab === "odds-policy" && (
            <ParameterSweepPicks
              date={date}
              onSelect={openSelection}
              onAdd={addToAccumulator}
            />
          )}
          {isAdmin && tab === "market-roi" && <MarketRoiDaily data={marketRoi} error={marketRoiError} />}
          {isAdmin && tab === "passed" && (
            <PassedMatches rows={rejected} onSelect={openSelection} />
          )}
          {tab === "tickets" && (
            <>
              {isAdmin && generationUnavailable && (
                <section className="publication-diagnostics" role="status" aria-label="Pipeline diagnostics">
                  <div className="publication-diagnostics-heading">
                    <div>
                      <span className="generation-status-badge">{pipelineStateBadge}</span>
                      <h3>{pipelineStateTitle}</h3>
                    </div>
                    {data?.pipeline_run_id != null && <span>Run {data.pipeline_run_id}</span>}
                  </div>
                  <p>
                    {pipelineActive
                      ? `Current stage: ${formatStage(data?.pipeline_current_stage ?? "starting")}. This page refreshes automatically while the persisted run advances.`
                      : data?.pipeline_run_id != null
                        ? `No generation record exists. Last stage: ${formatStage(data?.pipeline_current_stage ?? "unknown")}.${data?.pipeline_error ? ` ${data.pipeline_error}` : " The automation watchdog will retry a stale incomplete run within its bounded safety limit."}`
                        : date === TODAY
                          ? "Lohela checks the latest due 00:15 or 05:00 CAT window on startup and every five minutes. If it was missed, it queues one full catch-up automatically."
                          : "The ticket cards are intentionally hidden because there is no evidence that candidate generation or publication ran for this date."}
                  </p>
                </section>
              )}
              {isAdmin && generationFinishedWithoutTickets && (
                <section className="publication-diagnostics" role="status" aria-label="Publication diagnostics">
                  <div className="publication-diagnostics-heading">
                    <div>
                      <span className="generation-status-badge">Partial · no ticket published</span>
                      <h3>Candidates were found, but none passed every publication gate.</h3>
                    </div>
                    <span>Generation {data?.generation_id}</span>
                  </div>
                  <div className="publication-funnel" aria-label="Candidate publication funnel">
                    <span><b>{data?.qualified_pool ?? 0}</b><small>model candidates</small></span>
                    <i aria-hidden="true">→</i>
                    <span><b>{matches.length}</b><small>Q ≥75 selections</small></span>
                    <i aria-hidden="true">→</i>
                    <span><b>{qScoreMatchCount}</b><small>distinct matches</small></span>
                    <i aria-hidden="true">→</i>
                    <span><b>{q85Count}</b><small>Q ≥85 selections</small></span>
                    <i aria-hidden="true">→</i>
                    <span className={addableCount === 0 ? "blocked" : ""}><b>{addableCount}</b><small>conservative draft legs</small></span>
                    <i aria-hidden="true">→</i>
                    <span className="blocked"><b>0</b><small>published tickets</small></span>
                  </div>
                  {leadingRejectionReasons.length > 0 && (
                    <div className="publication-reasons">
                      <strong>Leading gate failures</strong>
                      {leadingRejectionReasons.map(([reason, count]) => (
                        <span key={reason}><b>{count}</b> {reasonLabel(reason)}</span>
                      ))}
                    </div>
                  )}
                  <p>Personal drafts cannot accept stale, invalid, or already-started selections.</p>
                </section>
              )}
              {isAdmin && (data?.superseded_versions?.length ?? 0) > 0 && (
                <div className="ticket-version-notice">
                  <strong>Latest versions shown.</strong> Earlier published tickets remain immutable in history; regenerated tickets may have different legs.
                </div>
              )}
              {publicPublishedTickets.length > 0 && (
                <details className="stake-planner">
                  <summary>
                    <strong>Stake planner</strong>
                    <span>Set a bankroll to see a suggested amount on eligible ticket cards.</span>
                  </summary>
                  <div className="stake-planner-controls">
                    <label className="bankroll-input">
                      Bankroll
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        value={bankroll}
                        onChange={(event) => setBankroll(event.target.value)}
                        placeholder="e.g. 1000"
                      />
                    </label>
                    <span>Planning estimate only · half-Kelly sizing, capped at 5% of bankroll · does not place a bet.</span>
                  </div>
                </details>
              )}
              {publicPublishedTickets.length > 0 && <div className="ticket-grid public-ticket-grid">
              {publicTiers.map((tier) => (
                <TicketCard
                  key={tier.key}
                  ticket={data?.[tier.key] ?? null}
                  tierName={tier.name}
                  tierDesc={tier.desc}
                  color={tier.color}
                  onSelectLeg={canExploreMarkets ? openTicketLeg : undefined}
                  suggestedStake={suggestedStake(data?.[tier.key] ?? null)}
                  showMarketSelections={user?.plan === "pro" || isAdmin}
                />
              ))}
            </div>}
            </>
          )}

          {!isAdmin && tab === "tickets" && !error && data && publicPublishedTickets.length === 0 && (
            <section className="publication-diagnostics" role="status">
              <div className="publication-diagnostics-heading"><h3>No published recommendations for this date</h3></div>
              <p>Recommendations appear here only when the publication process has produced a ticket for the selected date.</p>
            </section>
          )}

        </>
      )}
      {detail && (
        <SelectionDetail
          selection={detail.selection}
          modelVersion={detail.modelVersion}
          publishedAt={detail.publishedAt}
          pricing={detail.pricing}
          onClose={() => setDetail(null)}
          onOpenMatch={isAdmin ? onOpenMatch : undefined}
        />
      )}
    </>
  );
}

function PassedMatches({ rows, onSelect }: { rows: SelectionSummary[]; onSelect: (row: SelectionSummary) => void }) {
  const rejectionOutcome = (result: SelectionSummary["result"]) => {
    if (result === "lost") return { label: "Passed", tone: "passed" };
    if (result === "won") return { label: "Failed", tone: "failed" };
    if (result === "void") return { label: "Void", tone: "void" };
    return { label: "Pending", tone: "pending" };
  };

  return <section className="passed-matches-panel" aria-labelledby="passed-matches-title">
    <div className="section-heading">
      <div><span className="eyebrow">Transparent rejection ledger</span><h2 id="passed-matches-title">Rejected opportunities and why</h2><span className="section-note">A passed rejection avoided a losing selection; a failed rejection excluded a winning selection.</span></div>
      <span className="section-note"><b>{rows.length}</b> reviewed</span>
    </div>
    {rows.length === 0 ? <div className="decision-empty"><strong>No rejected candidates recorded</strong><span>When the candidate ledger is empty, there is no pass explanation to display.</span></div> : <div className="decision-table-wrap passed-matches-table-wrap">
      <table className="decision-table passed-matches-table">
        <thead><tr><th>Match</th><th>Selection</th><th>Q-score · Odds · Edge</th><th>Rejection reason</th><th>Match result</th><th>Rejection check</th></tr></thead>
        <tbody>{rows.map(row => {
          const outcome = rejectionOutcome(row.result);
          const score = row.home_goals != null && row.away_goals != null
            ? `${row.home_goals}–${row.away_goals}`
            : null;
          const resultLabel = row.result
            ? `${row.result.charAt(0).toUpperCase()}${row.result.slice(1)}`
            : (row.match_status?.toLowerCase() === "finished" ? "Unavailable" : "Pending");
          return <tr key={row.prediction_id}>
            <td><button className="decision-match" onClick={() => onSelect(row)}>{row.home_team} vs {row.away_team}<small>{row.competition} · <span className="match-date">{formatDate(row.kickoff_at)}</span> · <span className="match-kickoff">{formatKickoff(row.kickoff_at)}</span></small></button></td>
            <td>{!selectionRepeatsMarket(row.market, row.selection) && <span className="decision-market">{formatMarket(row.market)}</span>}<span className={`market-pick ${selectionTone(row.selection)}`}>{formatSelection(row.selection)}</span></td>
            <td><div className="passed-match-metrics"><span><small>Q-score</small><b>{row.q_score.toFixed(1)}</b></span><span><small>Odds</small><b>{row.best_odds == null ? "Unavailable" : row.best_odds.toFixed(2)}</b></span><span><small>Edge</small><b>{row.edge == null ? "Unavailable" : `${(row.edge * 100).toFixed(1)}%`}</b></span></div></td>
            <td className="passed-match-reason-cell">{(row.reason_codes?.length ? row.reason_codes : ["Publication gate did not pass"]).map(reasonLabel).join(" · ")}</td>
            <td><span className={`result-pill ${row.result ?? "pending"}`}>{resultLabel}</span>{score && <small className="result-score">{score}</small>}</td>
            <td><span className={`rejection-outcome-pill ${outcome.tone}`}>{outcome.label}</span></td>
          </tr>;
        })}</tbody>
      </table>
    </div>}
  </section>
}

function DecisionSummary({
  date,
  matches,
  onSelect,
}: {
  date: string;
  matches: SelectionSummary[];
  onSelect: (selection: SelectionSummary) => void;
}) {
  const top = matches[0] ?? null;
  const kickoffState = (selection: SelectionSummary) => {
    const kickoff = Date.parse(selection.kickoff_at);
    return !Number.isFinite(kickoff) ? "unknown" : kickoff <= Date.now() ? "past" : "upcoming";
  };
  const isBettable = (selection: SelectionSummary) => selection.recommendation_status ? selection.recommendation_status === "BET" : selection.q_score >= 85 && (selection.edge ?? 0) > 0 && selection.data_quality_status === "good" && (selection.active_models?.length ?? 0) > 0;
  const topKickoffState = top ? kickoffState(top) : "unknown";
  const topFinished = top?.match_status?.toLowerCase() === "finished";
  const topScore = top?.home_goals != null && top.away_goals != null
    ? `${top.home_goals}–${top.away_goals}`
    : null;
  const pickOutcome = top?.result === "won"
    ? "won"
    : top?.result === "lost"
      ? "lost"
      : top?.result === "void"
        ? "void"
        : null;
  const action = top
    ? topKickoffState === "past"
      ? "HISTORICAL"
      : topKickoffState === "unknown"
        ? "UNAVAILABLE"
        : top.recommendation_status ?? (isBettable(top) ? "BET" : "WATCH")
    : "PASS";
  const actionTone = action.toLowerCase();
  const reason = top
    ? topFinished && topScore && pickOutcome
      ? `Full time: ${top.home_team} ${topScore} ${top.away_team}. Our pick ${pickOutcome}.`
      : topFinished
        ? "This match has finished, but its final score or pick outcome is not available yet."
        : action === "HISTORICAL"
          ? "This selection has already kicked off. It remains available for review and is not an upcoming pick."
          : action === "UNAVAILABLE"
            ? "Kickoff time is unavailable, so this selection cannot be labelled as an upcoming decision."
            : action === "BET"
              ? "Qualified quality score and positive priced edge. Review the risks before logging a decision."
              : "A qualified signal exists, but the evidence is not strong enough for a direct BET label."
    : "No priced selection passed the current publication and evidence gates.";

  return (
    <section className="decision-summary" aria-labelledby="decision-summary-title">
      <div className="decision-summary-header">
        <div>
          <span className="eyebrow">Featured research candidate · {date}</span>
          <h2 id="decision-summary-title">Candidate decision</h2>
          <p>{reason}</p>
        </div>
        <span className={`decision-status ${actionTone}`} aria-label={`Featured candidate decision: ${action}`}>
          {action}
        </span>
      </div>
      <p className="decision-footnote">Featured research candidate for {date}. This is not a published ticket.</p>
      {top && (
        <div className="decision-lead">
          <div>
            <strong>{top.home_team} <span>vs</span> {top.away_team}</strong>
            <small>{top.competition} · <span className="kickoff-time">{formatKickoff(top.kickoff_at)}</span></small>
            {topFinished && <small className="decision-final-result">
              {topScore ? `Full time · ${topScore}` : "Full time · Score unavailable"}
              {pickOutcome && <b className={`decision-pick-outcome ${pickOutcome}`}>Pick {pickOutcome}</b>}
            </small>}
          </div>
          <div className="decision-lead-selection">
            {!selectionRepeatsMarket(top.market, top.selection) && <span>{formatMarket(top.market)}</span>}
            <b>{formatSelection(top.selection)}</b>
          </div>
          <button className="btn-ghost btn-sm" onClick={() => onSelect(top)}>Why this?</button>
        </div>
      )}
    </section>
  );
}
