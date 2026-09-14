import { useCallback, useEffect, useMemo, useState } from "react";
import type { CustomAccumulator as ApiCustomAccumulator, DailyTickets, Leg, SelectionSummary, Ticket } from "../../lib/api";
import {
  createCustomAccumulator,
  deleteCustomAccumulator,
  fetchDailyTickets,
  fetchQualifiedSelections,
  fetchRejectedSelections,
  fetchStrongestSelections,
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
import { AllMatches } from "./AllMatches";
import { CustomAccumulatorPanel } from "./CustomAccumulatorPanel";
import { SelectionPanel } from "./SelectionPanel";
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
  const [data, setData] = useState<DailyTickets | null>(null);
  const [strongest, setStrongest] = useState<SelectionSummary[]>([]);
  const [rejected, setRejected] = useState<SelectionSummary[]>([]);
  const [matches, setMatches] = useState<SelectionSummary[]>([]);
  const [tab, setTab] = useState<DailyTab>("tickets");
  const [qualityFilter, setQualityFilter] = useState<string | null>(null);
  const [custom, setCustom] = useState<CustomAccumulator[]>(() => {
    return [];
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<{
    selection: DetailSelection;
    modelVersion?: string;
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
    if (!ticket || !(bank > 0) || ticket.combined_odds <= 1) return null;
    const b = ticket.combined_odds - 1;
    const kelly =
      (ticket.adjusted_probability * b - (1 - ticket.adjusted_probability)) / b;
    return Math.min(Math.max(0, kelly * 0.5 * bank), bank * 0.05);
  };

  const load = useCallback(async (targetDate: string) => {
    setLoading(true);
    setError(null);
    try {
      const [tickets, allRows, strongRows, rejectedRows, customRows] = await Promise.all([
        fetchDailyTickets(targetDate),
        fetchQualifiedSelections(targetDate),
        fetchStrongestSelections(targetDate),
        fetchRejectedSelections(targetDate),
        fetchCustomAccumulators(targetDate),
      ]);
      const evidencedRows = withRejectionEvidence(allRows, rejectedRows);
      const evidencedStrongest = withRejectionEvidence(strongRows, rejectedRows);
      setData(tickets);
      setMatches(evidencedRows);
      setStrongest(evidencedStrongest.slice(0, 8));
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

  const filteredMatches = useMemo(() => qualityFilter
    ? matches.filter((row) => row.q_grade === qualityFilter)
    : matches, [matches, qualityFilter]);
  const matchGroups = useMemo(
    () =>
      Array.from(
        filteredMatches
          .reduce((map, row) => {
            const list = map.get(row.match_id) ?? [];
            list.push(row);
            map.set(row.match_id, list);
            return map;
          }, new Map<number, SelectionSummary[]>())
          .entries(),
      ),
    [filteredMatches],
  );
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
      (ticket) => ticket.date === date && ticket.status === "draft",
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
    });
  };

  const openSelection = (selection: SelectionSummary) => {
    setDetail({
      selection: {
        ...selection,
        source_odds_at: null,
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

  const publishedTickets = data
    ? [
        data.conservative,
        data.balanced,
        data.aggressive,
        data.best_value,
      ].filter((ticket): ticket is Ticket => ticket != null)
    : [];
  const publicationTimes = publishedTickets
    .map((ticket) => ticket.published_at)
    .filter(Boolean)
    .sort();
  const latestPublication = publicationTimes[publicationTimes.length - 1];
  const modelVersions = [
    ...new Set(publishedTickets.map((ticket) => ticket.model_version)),
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
    data && data.generation_id == null && publishedTickets.length === 0,
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

  const publicTiers = [
    {
      key: "conservative" as const,
      name: "Conservative",
      desc: "Q â‰¥85 Â· 3â€“6 legs Â· 3â€“5Ã—",
      color: "var(--conservative)",
    },
    {
      key: "balanced" as const,
      name: "Balanced",
      desc: "Q â‰¥80 Â· 60% Grade A/A+",
      color: "var(--balanced)",
    },
    {
      key: "aggressive" as const,
      name: "Aggressive",
      desc: "Q â‰¥75 Â· adjusted probability â‰¥5%",
      color: "var(--aggressive)",
    },
  ];

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
              {data.qualified_pool} generation candidate legs
            </span>
            {data.generation_status && (
              <span>Generation {data.generation_status}</span>
            )}
            {data.pipeline_status && (
              <span>Pipeline {data.pipeline_status}</span>
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
          <div className="ticket-analysis-control">
            <span className="action-group-label">Stake analysis</span>
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
          </div>
          <div className="ticket-operations-control">
            <span className="action-group-label">Operations</span>
            <div>
              <button
                className="btn-primary"
                onClick={runTodayPipeline}
                disabled={pipelineRunning}
                title="Run the pipeline for today"
              >
                {pipelineRunning ? "Queuingâ€¦" : "â–¶ Run Todayâ€™s Pipeline"}
              </button>
              <button
                className="btn-ghost"
                onClick={settleRecentResults}
                disabled={settlementRunning}
                title="Refresh finished results and settle pending tickets from yesterday and today"
              >
                {settlementRunning ? "Queuingâ€¦" : "â†» Settle Recent Results"}
              </button>
            </div>
          </div>
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
                ["matches", "All matches"],
                ["strongest", "Strongest picks"],
                ["my-accumulators", "My accumulators"],
                ["rejected", "Rejected"],
              ] as [DailyTab, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                className={tab === key ? "active" : ""}
                onClick={() => setTab(key)}
              >
                {label}
                {key === "matches" && matches.length > 0
                  ? ` Â· ${new Set(matches.map((row) => row.match_id)).size}`
                  : ""}
              </button>
            ))}
          </nav>
          {(tab === "matches" || tab === "strongest" || tab === "rejected") && (
            <div className="quality-legend" aria-label="Quality legend">
              <span className="legend-title">Quality guide</span>
              <button className={`quality-guide-item${qualityFilter === "A+" ? " active" : ""}`} onClick={() => { setQualityFilter(qualityFilter === "A+" ? null : "A+"); setTab("matches") }}>
                <b className="grade-badge grade-a-plus">A+</b> strongest
              </button>
              <button className={`quality-guide-item${qualityFilter === "A" ? " active" : ""}`} onClick={() => { setQualityFilter(qualityFilter === "A" ? null : "A"); setTab("matches") }}><b className="grade-badge grade-a">A</b> high confidence</button>
              <button className={`quality-guide-item${qualityFilter === "B+" ? " active" : ""}`} onClick={() => { setQualityFilter(qualityFilter === "B+" ? null : "B+"); setTab("matches") }}><b className="grade-badge grade-b-plus">B+</b> qualified</button>
              <button className={`quality-guide-item${qualityFilter === "B" ? " active" : ""}`} onClick={() => { setQualityFilter(qualityFilter === "B" ? null : "B"); setTab("matches") }}><b className="grade-badge grade-b">B</b> acceptable</button>
              <span>
                <b className="spread-dot strong" /> â‰¤5 pp aligned
              </span>
              <span>
                <b className="spread-dot caution" /> 10â€“15 pp caution
              </span>
              <span>
                <b className="spread-dot high" /> &gt;15 pp downgrade
              </span>
            </div>
          )}
          {(tab === "tickets" || tab === "matches") && <div className="settlement-legend" aria-label="Settlement legend"><span><b>Score</b> = match result</span><span><b>Market</b> = selected outcome</span><span><b>Ticket</b> = accumulator result</span></div>}
          {qualityFilter && tab === "matches" && <div className="filter-summary quality-filter-summary">Showing matches with <strong>Grade {qualityFilter}</strong> selections <button className="btn-ghost btn-sm" onClick={() => setQualityFilter(null)}>Clear quality filter</button></div>}
          {tab === "tickets" && (
            <div className="view-intro">
              <div>
                <span className="eyebrow">Primary research output</span>
                <h2 className="section-title">Published recommendations</h2>
              </div>
              <p>
                Start with a ticket tier. Open any leg for the supporting model
                evidence; use All matches to compare Q-score candidates before
                the final Acca eligibility and combination checks.
              </p>
            </div>
          )}
          {tab === "matches" && (
            <AllMatches
              rows={matchGroups}
              onSelect={openSelection}
              onAdd={addToAccumulator}
            />
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
                      <span className="generation-status-badge">Partial Â· no ticket published</span>
                      <h3>Candidates were found, but none passed every publication gate.</h3>
                    </div>
                    <span>Generation {data?.generation_id}</span>
                  </div>
                  <div className="publication-funnel" aria-label="Candidate publication funnel">
                    <span><b>{data?.qualified_pool ?? 0}</b><small>model candidates</small></span>
                    <i aria-hidden="true">â†’</i>
                    <span><b>{matches.length}</b><small>Q â‰¥75 selections</small></span>
                    <i aria-hidden="true">â†’</i>
                    <span><b>{qScoreMatchCount}</b><small>distinct matches</small></span>
                    <i aria-hidden="true">â†’</i>
                    <span><b>{q85Count}</b><small>Q â‰¥85 selections</small></span>
                    <i aria-hidden="true">â†’</i>
                    <span className={addableCount === 0 ? "blocked" : ""}><b>{addableCount}</b><small>safe draft legs</small></span>
                    <i aria-hidden="true">â†’</i>
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
                  <p>Open All matches for candidate evidence or Rejected for the complete rule-level audit. Personal drafts cannot accept stale, invalid, or already-started selections.</p>
                </section>
              )}
              {(data?.superseded_versions?.length ?? 0) > 0 && (
                <div className="ticket-version-notice">
                  <strong>Latest versions shown.</strong> Earlier published tickets remain immutable in history; regenerated tickets may have different legs.
                </div>
              )}
              {!((generationFinishedWithoutTickets || generationUnavailable) && publishedTickets.length === 0) && <div className="ticket-grid public-ticket-grid">
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
              <TicketCard
                ticket={data?.best_value ?? null}
                tierName="Best Value"
                tierDesc="Q â‰¥85"
                color="var(--purple)"
                research
                onSelectLeg={openTicketLeg}
                suggestedStake={suggestedStake(data?.best_value ?? null)}
              />
            </div>}
            </>
          )}

          {tab === "strongest" && (
            <SelectionPanel
              title="Strongest selections"
              rows={strongest}
              empty="No Grade A model candidates are available for this date."
              onSelect={openSelection}
              onAdd={addToAccumulator}
            />
          )}
          {tab === "rejected" && (
            <SelectionPanel
              title="Rejected matches"
              rows={rejected}
              empty="No rejected selections are recorded for this date."
              rejected
              onSelect={openSelection}
            />
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
          onClose={() => setDetail(null)}
        />
      )}
    </>
  );
}
