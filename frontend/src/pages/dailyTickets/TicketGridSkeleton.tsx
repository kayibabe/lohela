export function TicketGridSkeleton() {
  return (
    <div
      className="ticket-grid public-ticket-grid skeleton-grid"
      aria-label="Loading published ledger"
      aria-busy="true"
    >
      {[0, 1, 2].map((index) => (
        <div className="ticket-card skeleton-card" key={index}>
          <div className="ticket-header">
            <span className="skeleton-block skeleton-label" />
            <div className="skeleton-stats">
              <span className="skeleton-block" />
              <span className="skeleton-block" />
              <span className="skeleton-block" />
            </div>
          </div>
          {[0, 1, 2].map((row) => (
            <span className="skeleton-block skeleton-row" key={row} />
          ))}
        </div>
      ))}
      <span className="sr-only">Loading published ledger…</span>
    </div>
  );
}
