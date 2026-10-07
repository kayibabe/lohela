import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../../auth";
import type { CustomAccumulator as ApiCustomAccumulator, DailyTickets, Leg, SelectionSummary, Ticket } from "../../lib/api";
import {
  createCustomAccumulator,
  deleteCustomAccumulator,
  fetchDailyTickets,
  fetchQualifiedSelections,
  fetchRejectedSelections,
  fetchCustomAccumulators,
  formatDate,
  updateCustomAccumulator,
  formatStage,
} from "../../lib/api";
import TicketCard from "../../components/TicketCard";
import SelectionDetail, {
  type DetailSelection,
} from "../../components/SelectionDetail";
import { addDays } from "../../utils";
import { BandMixPicks } from "./BandMixPicks";
import { ParameterSweepPicks } from "./ParameterSweepPicks";
import { CustomAccumulatorPanel } from "./CustomAccumulatorPanel";
import { TicketGridSkeleton } from "./TicketGridSkeleton";
import {
  accumulatorBlockers,
  apiAccumulatorToUi,
  localDateString,
  reasonLabel,
  TODAY,
  withRejectionEvidence,
} from "./helpers";
import type { CustomAccumulator, DailyTab, DailyTicketsPageProps } from "./types";

export default function DailyTicketsPage({ date }: DailyTicketsPageProps) {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [data, setData] = useState<DailyTickets | null>(null);
  const [rejected, setRejected] = useState<SelectionSummary[]>([]);
  const [matches, setMatches] = useState<SelectionSummary[]>([]);
  const [tab, setTab] = useState<DailyTab>("tickets");
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
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [settlementRunning, setSettlementRunning] = useState(false);
  const [operationMessage, setOperationMessage] = useState<{
    ok: boolean;
    text: string;
  } | null>(null);
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
    try {
      const [tickets, allRows, rejectedRows, customRows] = await Promise.all([
        fetchDailyTickets(targetDate),
        fetchQualifiedSelections(targetDate),
        fetchRejectedSelections(targetDate),
        fetchCustomAccumulators(targetDate),
      ]);
      const evidencedRows = withRejectionEvidence(allRows, rejectedRows);
      setData(tickets);
      setMatches(evidencedRows);
      setRejected(rejectedRows);
      setCustom(customRows.map(apiAccumulatorToUi));
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Failed to load research ledger",
      );
    } finally {
      setLoading(false);
    }
  }, []);

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
      setOperationMessage({
        ok: false,
        text: `Selection not added: ${blockers.map(reasonLabel).join(", ")}`,
      });
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
    setTab("my-accumulators");
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

  async function runTodayPipeline() {
    const targetDate = localDateString();
    if (
      !window.confirm(
        `Run the full pipeline for ${targetDate}? This will refresh data and may publish new ticket versions.`,
      )
    )
      return;
    setPipelineRunning(true);
    setOperationMessage(null);
    try {
      const res = await fetch(
        `/api/v1/admin/pipeline/trigger?target_date=${targetDate}`,
        { method: "POST" },
      );
      const response = await res.json().catch(() => ({}));
      setOperationMessage({
        ok: res.ok,
        text: res.ok
          ? `Pipeline queued for ${targetDate}`
          : (response.detail ?? "Unable to queue pipeline"),
      });
    } catch {
      setOperationMessage({
        ok: false,
        text: "Unable to reach the pipeline service",
      });
    } finally {
      setPipelineRunning(false);
    }
  }

  async function settleRecentResults() {
    const today = new Date();
    const periodEnd = localDateString(today);
    const periodStart = addDays(periodEnd, -1);
    setSettlementRunning(true);
    setOperationMessage(null);
    try {
      const res = await fetch("/api/v1/admin/historical/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          period_start: periodStart,
          period_end: periodEnd,
        }),
      });
      const response = await res.json().catch(() => ({}));
      setOperationMessage({
        ok: res.ok,
        text: res.ok
          ? `Result refresh and settlement queued for ${periodStart} to ${periodEnd}`
          : (response.detail ?? "Unable to queue settlement"),
      });
    } catch {
      setOperationMessage({
        ok: false,
        text: "Unable to reach the settlement service",
      });
    } finally {
      setSettlementRunning(false);
    }
  }

  // Keep all public-page status and publication metadata scoped to active
  // release tiers.
  const publicPublishedTickets = data
    ? [
        data.conservative,
        data.balanced,
        data.high_odds,
      ].filter((ticket): ticket is Ticket => ticket != null)
    : [];
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

  const decisionRows = matches
    .filter((row) => row.best_odds != null)
    .sort((left, right) => (right.edge ?? -1) - (left.edge ?? -1))
    .slice(0, 6);

  return (
    <>
      <div className="meta-bar">
        <div>
          <h1 className="meta-date">
            {data ? formatDate(data.target_date) : formatDate(date)}
          </h1>
          <p className="research-disclaimer">
            Internal research and paper trading only. No ticket is a promise of
            profit.
          </p>
        </div>
        {data && (
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
        <div className="tickets-banner-actions">
          <details className="ticket-analysis-control">
            <summary>Plan stake</summary>
            <label className="bankroll-input">
              Bankroll{" "}
              <input
                type="number"
                min="0"
                step="0.01"
                value={bankroll}
                onChange={(event) => setBankroll(event.target.value)}
                placeholder="e.g. 1000"
              />
            </label>
          </details>
          {isAdmin && <div className="ticket-operations-control">
            <span className="action-group-label">Operations</span>
            <div>
              <button
                className="btn-primary"
                onClick={runTodayPipeline}
                disabled={pipelineRunning}
                title="Run the pipeline for today"
              >
                {pipelineRunning ? "Queuing…" : "▶ Run Today’s Pipeline"}
              </button>
              <button
                className="btn-ghost"
                onClick={settleRecentResults}
                disabled={settlementRunning}
                title="Refresh finished results and settle pending tickets from yesterday and today"
              >
                {settlementRunning ? "Queuing…" : "↻ Settle Recent Results"}
              </button>
            </div>
          </div>}
        </div>
      </div>
      {operationMessage && (
        <div
          className={`pipeline-banner-message tickets-operation-message ${operationMessage.ok ? "positive" : "negative"}`}
          role="status"
        >
          {operationMessage.text}. Check Admin for persisted progress and
          publication.
        </div>
      )}

      {error && (
        <div className="error-bar" role="alert">
          <div>
            <strong>Research ledger unavailable</strong>
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
            {(
              [
                ["tickets", "Recommendations"],
                ["best-mix", "Best-mix picks"],
                ["odds-policy", "Dynamic odds policy"],
                ["passed", "Passed / why"],
                ["my-accumulators", "My accumulators"],
              ] as [DailyTab, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                className={tab === key ? "active" : ""}
                onClick={() => setTab(key)}
              >
                {label}
              </button>
            ))}
          </nav>
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
          {tab === "tickets" && <div className="settlement-legend" aria-label="Settlement legend"><span><b>Score</b> = match result</span><span><b>Market</b> = selected outcome</span><span><b>Ticket</b> = accumulator result</span></div>}
          {tab === "tickets" && (
            <div className="view-intro">
              <div>
                <span className="eyebrow">Primary research output</span>
                <h2 className="section-title">Published recommendations</h2>
              </div>
              <p>
                Start with a ticket tier. Open any leg for the supporting model
                evidence before making a manual tracker decision.
              </p>
            </div>
          )}
          {tab === "tickets" && (
            <DecisionSummary
              date={date}
              matches={decisionRows}
              onSelect={openSelection}
            />
          )}
          {tab === "best-mix" && (
            <BandMixPicks
              date={date}
              rejectedRows={rejected}
              onSelect={openSelection}
              onAdd={addToAccumulator}
            />
          )}
          {tab === "odds-policy" && (
            <ParameterSweepPicks
              date={date}
              onSelect={openSelection}
              onAdd={addToAccumulator}
            />
          )}
          {tab === "passed" && (
            <PassedMatches rows={rejected} onSelect={openSelection} />
          )}
          {tab === "tickets" && (
            <>
              {generationUnavailable && (
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
              {generationFinishedWithoutTickets && (
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
              {(data?.superseded_versions?.length ?? 0) > 0 && (
                <div className="ticket-version-notice">
                  <strong>Latest versions shown.</strong> Earlier published tickets remain immutable in history; regenerated tickets may have different legs.
                </div>
              )}
              {!((generationFinishedWithoutTickets || generationUnavailable) && publicPublishedTickets.length === 0) && <div className="ticket-grid public-ticket-grid">
              {publicTiers.map((tier) => (
                <TicketCard
                  key={tier.key}
                  ticket={data?.[tier.key] ?? null}
                  tierName={tier.name}
                  tierDesc={tier.desc}
                  color={tier.color}
                  onSelectLeg={openTicketLeg}
                  suggestedStake={suggestedStake(data?.[tier.key] ?? null)}
                />
              ))}
            </div>}
            </>
          )}

          {tab === "my-accumulators" && (
            <CustomAccumulatorPanel
              date={date}
              tickets={custom.filter((ticket) => ticket.date === date)}
              onCreate={async (name) => { await addOrUpdateCustom(null, name, [], null, "draft"); }}
              onUpdate={async (ticket, update) => { const nextStatus = update.status === "placed" || update.status === "draft" ? update.status : ticket.status === "placed" ? "placed" : "draft"; await addOrUpdateCustom(ticket, update.name ?? ticket.name, update.legs ?? ticket.legs, update.stake === undefined ? ticket.stake : update.stake, nextStatus); }}
              onDelete={async (ticket) => { await deleteCustomAccumulator(ticket.id); setCustom((current) => current.filter((item) => item.id !== ticket.id)); }}
              onNavigate={setTab}
            />
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
        />
      )}
    </>
  );
}

function PassedMatches({ rows, onSelect }: { rows: SelectionSummary[]; onSelect: (row: SelectionSummary) => void }) {
  return <section className="passed-matches-panel" aria-labelledby="passed-matches-title">
    <div className="section-heading">
      <div><span className="eyebrow">Transparent rejection ledger</span><h2 id="passed-matches-title">Passed opportunities and why</h2><span className="section-note">Candidates remain visible so the system teaches the user what failed a publication gate.</span></div>
      <span className="section-note"><b>{rows.length}</b> reviewed</span>
    </div>
    {rows.length === 0 ? <div className="decision-empty"><strong>No rejected candidates recorded</strong><span>When the candidate ledger is empty, there is no pass explanation to display.</span></div> : <div className="passed-matches-list">
      {rows.map(row => <article className="passed-match-row" key={row.prediction_id}>
        <div className="passed-match-main"><button onClick={() => onSelect(row)}><strong>{row.home_team} vs {row.away_team}</strong><small>{row.competition} · {new Date(row.kickoff_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small></button><span>{row.market} · {row.selection}</span></div>
        <div className="passed-match-metrics"><span><small>Q-score</small><b>{row.q_score.toFixed(1)}</b></span><span><small>Odds</small><b>{row.best_odds == null ? "Unavailable" : row.best_odds.toFixed(2)}</b></span><span><small>Edge</small><b>{row.edge == null ? "Unavailable" : `${(row.edge * 100).toFixed(1)}%`}</b></span></div>
        <div className="passed-match-reasons"><strong>PASS because</strong><span>{(row.reason_codes?.length ? row.reason_codes : ["Publication gate did not pass"]).map(reasonLabel).join(" · ")}</span></div>
      </article>)}
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
  const isBettable = (selection: SelectionSummary) => selection.recommendation_status ? selection.recommendation_status === "BET" : selection.q_score >= 85 && (selection.edge ?? 0) > 0 && selection.data_quality_status === "good" && (selection.active_models?.length ?? 0) > 0;
  const action = top ? (top.recommendation_status ?? (isBettable(top) ? "BET" : "WATCH")) : "PASS";
  const actionTone = action.toLowerCase();
  const reason = top
    ? action === "BET"
      ? "Qualified quality score and positive priced edge. Review the risks before logging a decision."
      : "A qualified signal exists, but the evidence is not strong enough for a direct BET label."
    : "No priced selection passed the current publication and evidence gates.";

  return (
    <section className="decision-summary" aria-labelledby="decision-summary-title">
      <div className="decision-summary-header">
        <div>
          <span className="eyebrow">Decision workspace · {date}</span>
          <h2 id="decision-summary-title">BET / WATCH / PASS</h2>
          <p>{reason}</p>
        </div>
        <span className={`decision-status ${actionTone}`} aria-label={`Recommendation ${action}`}>
          {action}
        </span>
      </div>
      <div className="decision-principles" aria-label="Decision definitions">
        <span><b>Grade</b> opportunity quality</span>
        <span><b>Confidence</b> prediction certainty</span>
        <span><b>Risk</b> invalidation context</span>
      </div>
      {top && (
        <div className="decision-lead">
          <div>
            <strong>{top.home_team} <span>vs</span> {top.away_team}</strong>
            <small>{top.competition} · {new Date(top.kickoff_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small>
          </div>
          <div className="decision-lead-selection">
            <span>{top.market}</span>
            <b>{top.selection}</b>
          </div>
          <button className="btn-ghost btn-sm" onClick={() => onSelect(top)}>Why this?</button>
        </div>
      )}
      <div className="decision-table-wrap">
        {matches.length === 0 ? (
          <div className="decision-empty"><strong>No qualified priced opportunities</strong><span>PASS is a valid outcome when odds, provenance, or edge evidence is unavailable.</span></div>
        ) : (
          <table className="decision-table">
            <thead><tr><th>Match</th><th>Selection</th><th>Model</th><th>Odds</th><th>Edge</th><th>Grade</th><th>Data</th><th>Decision</th></tr></thead>
            <tbody>{matches.map((row) => {
              const rowAction = row.recommendation_status ?? (isBettable(row) ? "BET" : "WATCH");
              return <tr key={`${row.match_id}-${row.prediction_id}`}>
                <td><button className="decision-match" onClick={() => onSelect(row)}>{row.home_team} vs {row.away_team}<small>{row.competition}</small></button></td>
                <td><span className="decision-market">{row.market}</span><strong>{row.selection}</strong></td>
                <td>{row.model_probability == null ? "—" : `${(row.model_probability * 100).toFixed(1)}%`}</td>
                <td>{row.best_odds == null ? "—" : row.best_odds.toFixed(2)}</td>
                <td className={(row.edge ?? 0) > 0 ? "positive" : "negative"}>{row.edge == null ? "—" : `${(row.edge * 100).toFixed(1)}%`}</td>
                <td><span className="decision-grade">{row.q_grade ?? "Q"} · {row.q_score}</span></td>
                <td><span className={`data-quality-pill ${row.data_quality_status ?? "unknown"}`}>{row.data_quality_score == null ? "—" : `${row.data_quality_score.toFixed(0)}/100`}</span></td>
                <td><span className={`decision-pill ${rowAction.toLowerCase()}`}>{rowAction}</span></td>
              </tr>;
            })}</tbody>
          </table>
        )}
      </div>
      <p className="decision-footnote">Historical performance and calibration live in Validate. This surface shows current evidence only; it does not guarantee an outcome.</p>
    </section>
  );
}
