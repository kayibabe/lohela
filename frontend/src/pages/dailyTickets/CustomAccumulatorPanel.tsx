import { useState } from "react";
import { formatDate, formatKickoff, formatMarket } from "../../lib/api";
import { fmt } from "../../utils/currency";
import type { CustomAccumulator, DailyTab } from "./types";

export function CustomAccumulatorPanel({
  date,
  tickets,
  onCreate,
  onUpdate,
  onDelete,
  onNavigate,
}: {
  date: string;
  tickets: CustomAccumulator[];
  onCreate: (name: string) => Promise<void>;
  onUpdate: (ticket: CustomAccumulator, update: Partial<CustomAccumulator>) => Promise<void>;
  onDelete: (ticket: CustomAccumulator) => Promise<void>;
  onNavigate: (tab: DailyTab) => void;
}) {
  const [name, setName] = useState("");
  const create = async () => {
    const title = name.trim() || `${formatDate(date)} accumulator`;
    try {
      await onCreate(title);
      setName("");
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Unable to save draft");
    }
  };
  const updateTicket = async (ticket: CustomAccumulator, update: Partial<CustomAccumulator>) => {
    try {
      await onUpdate(ticket, update);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Unable to save accumulator");
    }
  };
  const clearTicket = (ticket: CustomAccumulator) => {
    if (ticket.status === "placed" || ticket.legs.length === 0) return;
    if (!window.confirm(`Clear all selections from “${ticket.name}”?`)) return;
    void updateTicket(ticket, { legs: [], stake: null });
  };
  const deleteTicket = async (ticket: CustomAccumulator) => {
    if (!window.confirm(`Delete “${ticket.name}”? This removes the saved draft.`)) return;
    try {
      await onDelete(ticket);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Unable to delete draft");
    }
  };
  return (
    <section className="selection-panel custom-accumulators">
      <div className="section-heading">
        <div>
          <h2>My accumulators</h2>
          <span className="section-note">
            Personal accumulators are persisted with immutable selection snapshots and settle automatically after placement
          </span>
        </div>
        <form className="custom-create-form" onSubmit={(event) => { event.preventDefault(); create(); }}>
          <label htmlFor="new-accumulator-name">New draft</label>
          <input
            id="new-accumulator-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Name (optional)"
            aria-label="New accumulator name"
          />
          <button className="btn-primary btn-sm" type="submit">
            + Create draft
          </button>
        </form>
      </div>
      {tickets.length === 0 ? (
        <div className="custom-empty-state">
          <strong>No drafts yet</strong>
          <p>Create a draft, then add selections from the research views.</p>
          <div className="custom-empty-actions">
            <button className="btn-ghost btn-sm" onClick={() => onNavigate("strongest")}>View strongest picks</button>
            <button className="btn-ghost btn-sm" onClick={() => onNavigate("matches")}>Browse all matches</button>
          </div>
        </div>
      ) : (
        <div className="custom-ticket-list">
          {tickets.map((ticket) => {
            const odds = ticket.legs.reduce(
              (total, leg) => total * (leg.best_odds ?? 1),
              1,
            );
            const potentialReturn = ticket.stake != null ? ticket.stake * odds : null;
            const canPlace = ticket.legs.length >= 2 && ticket.stake != null && ticket.stake > 0;
            const statusLabel = ticket.status === "draft" ? "Draft" : ticket.status === "placed" ? "Placed" : "Settled";
            const isLocked = ticket.status !== "draft";
            return (
              <article className={`custom-ticket ${ticket.status}`} key={ticket.id}>
                <div className="custom-ticket-header">
                  <div>
                    <strong>{ticket.name}</strong>
                    <span>{formatDate(ticket.date)} · {ticket.legs.length} leg{ticket.legs.length === 1 ? "" : "s"}</span>
                  </div>
                  <span className={`custom-status-badge ${ticket.status}`}>{statusLabel}</span>
                </div>
                <div className="custom-ticket-metrics">
                  <span><small>Combined odds</small><b>{ticket.legs.length ? `${odds.toFixed(2)}×` : "—"}</b></span>
                  <span><small>Stake</small><b>{ticket.stake != null ? fmt(ticket.stake) : "—"}</b></span>
                  <span><small>Potential return</small><b>{potentialReturn != null ? fmt(potentialReturn) : "—"}</b></span>
                  <span><small>Potential P&amp;L</small><b className={potentialReturn != null && ticket.stake != null && potentialReturn > ticket.stake ? "positive" : ""}>{potentialReturn != null && ticket.stake != null ? fmt(potentialReturn - ticket.stake) : "—"}</b></span>
                </div>
                {ticket.legs.length === 0 ? (
                  <div className="custom-ticket-empty">
                    <strong>No selections yet</strong>
                    <span>Add at least two selections before marking this accumulator placed.</span>
                    <div className="custom-empty-actions">
                      <button className="btn-ghost btn-sm" onClick={() => onNavigate("strongest")}>Add from strongest</button>
                      <button className="btn-ghost btn-sm" onClick={() => onNavigate("matches")}>Browse all matches</button>
                    </div>
                  </div>
                ) : (
                  <div className="custom-ticket-legs">
                    {ticket.legs.map((leg) => (
                      <div className="custom-ticket-leg" key={leg.prediction_id}>
                        <div className="custom-ticket-leg-main">
                          <strong>{leg.home_team} <span>vs</span> {leg.away_team}</strong>
                          <small>{leg.competition} · {formatKickoff(leg.kickoff_at)} · {formatMarket(leg.market)}</small>
                        </div>
                        <div className="custom-ticket-leg-evidence">
                          <span>{leg.best_odds != null ? `${leg.best_odds.toFixed(2)}×` : "—"}</span>
                          <span>Q {leg.q_score.toFixed(1)}</span>
                          <span>{leg.model_agreement != null ? `${(leg.model_agreement * 100).toFixed(1)} pp` : "—"}</span>
                          <button
                            type="button"
                            disabled={isLocked}
                            title={isLocked ? "Return to draft before removing selections" : "Remove selection"}
                            aria-label={`Remove ${leg.home_team} versus ${leg.away_team}`}
                            onClick={() => void updateTicket(ticket, { legs: ticket.legs.filter((row) => row.prediction_id !== leg.prediction_id) })}
                          >
                            ×
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                <div className="custom-ticket-controls">
                  <label htmlFor={`stake-${ticket.id}`}>
                    <span>Stake amount</span>
                    <input
                      id={`stake-${ticket.id}`}
                      type="number"
                      min="0"
                      step="0.01"
                      inputMode="decimal"
                      value={ticket.stake ?? ""}
                      disabled={isLocked}
                      placeholder="0.00"
                      onChange={(event) => void updateTicket(ticket, { stake: event.target.value ? Number(event.target.value) : null })}
                    />
                  </label>
                  <div className="custom-ticket-actions">
                    <button
                      className="btn-primary btn-sm"
                      disabled={isLocked || (ticket.status === "draft" && !canPlace)}
                      title={ticket.status === "draft" && !canPlace ? "Add at least two selections and enter a stake" : undefined}
                      onClick={() => void updateTicket(ticket, { status: ticket.status === "draft" ? "placed" : "draft" })}
                    >
                      {ticket.status === "draft" ? "Mark placed" : ticket.status === "placed" ? "Placed" : `Settled · ${ticket.status}`}
                    </button>
                    <button className="btn-ghost btn-sm" disabled={isLocked || ticket.legs.length === 0} onClick={() => clearTicket(ticket)}>
                      Clear selections
                    </button>
                    <button className="btn-ghost btn-sm danger-action" onClick={() => deleteTicket(ticket)}>
                      Delete draft
                    </button>
                  </div>
                </div>
                {ticket.status === "draft" && !canPlace && ticket.legs.length > 0 && (
                  <p className="custom-ticket-validation">Add at least one more selection and enter a stake before marking placed.</p>
                )}
                {isLocked && <p className="custom-ticket-locked">Placed accumulator locked · settlement will happen automatically when all matches finish.</p>}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
