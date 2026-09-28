"""remove the Aggressive ticket tier and purge its history

The Aggressive tier is retired: the builder no longer produces it. This purges
every stored Aggressive ticket (with its legs, settlement results and audit
events) so no performance, calibration, ledger or loss analysis can include it,
scrubs "aggressive" from generation/pipeline metadata, recomputes each
generation's status against the two remaining public tiers, drops the
Aggressive-only `high_risk_label` column, and removes AGGRESSIVE from the
Postgres `tickettype` enum.

The purge is irreversible: downgrade restores the schema (enum value and
column) but not the deleted rows. Restore those from a database backup.

Revision ID: f2b4d6e8a0c3
Revises: e7a2c4b6d8f1
"""

import json
import re

from alembic import op
import sqlalchemy as sa


revision = "f2b4d6e8a0c3"
down_revision = "e7a2c4b6d8f1"
branch_labels = None
depends_on = None

_RETIRED = "aggressive"
_PUBLIC_TYPES = {"safe", "balanced"}

_generations = sa.table(
    "ticket_generations",
    sa.column("id", sa.Integer),
    sa.column("status"),
    sa.column("output_count", sa.Integer),
    sa.column("config_snapshot", sa.JSON),
)
_audit_events = sa.table(
    "audit_events",
    sa.column("id", sa.Integer),
    sa.column("entity_type", sa.String),
    sa.column("payload", sa.JSON),
)
_stage_runs = sa.table(
    "pipeline_stage_runs",
    sa.column("id", sa.Integer),
    sa.column("stage_details", sa.JSON),
)
_alerts = sa.table(
    "automation_alerts",
    sa.column("id", sa.Integer),
    sa.column("dedupe_key", sa.String),
    sa.column("resolved", sa.Boolean),
    sa.column("resolved_at", sa.DateTime(timezone=True)),
    sa.column("title", sa.String),
    sa.column("detail", sa.Text),
    sa.column("context", sa.JSON),
)


def _mentions_retired(record: dict) -> bool:
    """A record about the retired tier: any *ticket_type field names it."""
    return any(
        str(key).endswith("ticket_type") and str(item).lower() == _RETIRED
        for key, item in record.items()
    )


def _scrub(value):
    """Drop every trace of the retired tier from a JSON document."""
    if isinstance(value, dict):
        return {
            key: _scrub(item)
            for key, item in value.items()
            if str(key).lower() != _RETIRED
        }
    if isinstance(value, list):
        return [
            _scrub(item)
            for item in value
            if not (isinstance(item, str) and item.lower() == _RETIRED)
            and not (isinstance(item, dict) and _mentions_retired(item))
        ]
    return value


def _contains_retired(value) -> bool:
    return _RETIRED in json.dumps(value, default=str).lower()


def _run_chunked(bind, sql: str, ids: list) -> list:
    """Execute `sql` (with an `IN :ids` clause) over `ids` in chunks."""
    rows = []
    statement = sa.text(sql).bindparams(sa.bindparam("ids", expanding=True))
    for start in range(0, len(ids), 500):
        result = bind.execute(statement, {"ids": ids[start:start + 500]})
        if result.returns_rows:
            rows += [row[0] for row in result.all()]
    return rows


def _purge_tickets(bind) -> None:
    ticket_ids = [row[0] for row in bind.execute(
        sa.text("SELECT id FROM accumulator_tickets WHERE LOWER(CAST(ticket_type AS TEXT)) = :retired"),
        {"retired": _RETIRED},
    ).all()]
    if not ticket_ids:
        return
    selection_ids = _run_chunked(bind, "SELECT id FROM ticket_selections WHERE ticket_id IN :ids", ticket_ids)

    def run(sql: str, ids: list) -> None:
        _run_chunked(bind, sql, ids)

    # Real-money journal entries are the user's own record: keep them, but
    # detach them from the legs being deleted (they keep match/market/selection).
    run("UPDATE bets SET source_selection_id = NULL WHERE source_selection_id IN :ids", selection_ids)
    run("DELETE FROM audit_events WHERE entity_type = 'ticket_selection' AND entity_id IN :ids",
        [str(i) for i in selection_ids])
    run("DELETE FROM audit_events WHERE entity_type = 'accumulator_ticket' AND entity_id IN :ids",
        [str(i) for i in ticket_ids])
    run("UPDATE ticket_results SET supersedes_result_id = NULL WHERE ticket_id IN :ids", ticket_ids)
    run("DELETE FROM ticket_results WHERE ticket_id IN :ids", ticket_ids)
    run("DELETE FROM ticket_selections WHERE ticket_id IN :ids", ticket_ids)
    run("DELETE FROM accumulator_tickets WHERE id IN :ids", ticket_ids)


def _scrub_metadata(bind) -> None:
    remaining = dict(bind.execute(sa.text(
        "SELECT generation_id, COUNT(*) FROM accumulator_tickets GROUP BY generation_id"
    )).all())
    for row in bind.execute(sa.select(_generations)).mappings().all():
        snapshot = _scrub(row["config_snapshot"] or {})
        summary = snapshot.get("publication_summary")
        status = row["status"]
        if summary is not None and str(status).upper() in {"COMPLETED", "PARTIAL"}:
            published = set(summary.get("published_ticket_types", []))
            status = "COMPLETED" if _PUBLIC_TYPES <= published else "PARTIAL"
        bind.execute(
            _generations.update().where(_generations.c.id == row["id"]).values(
                config_snapshot=snapshot,
                output_count=remaining.get(row["id"], 0),
            )
        )
        if status != row["status"]:
            # Untyped text bind: Postgres coerces it to the runstatus enum.
            bind.execute(
                sa.text("UPDATE ticket_generations SET status = :status WHERE id = :id"),
                {"status": status, "id": row["id"]},
            )

    for row in bind.execute(
        sa.select(_audit_events).where(_audit_events.c.entity_type == "ticket_generation")
    ).mappings().all():
        payload = _scrub(row["payload"] or {})
        if "published_ticket_types" in payload:
            payload["published_ticket_count"] = len(payload["published_ticket_types"])
        bind.execute(_audit_events.update().where(_audit_events.c.id == row["id"]).values(payload=payload))

    for row in bind.execute(sa.select(_stage_runs)).mappings().all():
        details = row["stage_details"] or {}
        if not _contains_retired(details):
            continue
        if isinstance(details, dict) and "by_ticket_type" in details:
            # A frozen performance_summary() snapshot: its headline totals
            # (P&L, hit rate, Brier...) were computed with Aggressive tickets
            # and cannot be un-mixed. Drop it; the live endpoints recompute
            # from the purged ledger. Calibration snapshots are
            # prediction-based and stay.
            scrubbed = {
                key: details[key] for key in ("calibration", "current_model_version") if key in details
            }
            scrubbed["performance_summary_purged"] = (
                "Frozen snapshot removed with a retired ticket tier; "
                "recompute from the live performance endpoints."
            )
        else:
            scrubbed = _scrub(details)
            if "missing_public_ticket_types" in scrubbed and "public_published" in scrubbed:
                scrubbed["public_published"] = len(_PUBLIC_TYPES) - len(scrubbed["missing_public_ticket_types"])
        bind.execute(_stage_runs.update().where(_stage_runs.c.id == row["id"]).values(stage_details=scrubbed))

    # Re-express "too few public tickets" alerts against the two remaining
    # tiers; one raised only because Aggressive was missing is resolved.
    required = len(_PUBLIC_TYPES)
    for row in bind.execute(
        sa.select(_alerts).where(_alerts.c.dedupe_key.like("min_daily_tickets:%"))
    ).mappings().all():
        context = _scrub(row["context"] or {})
        if "missing_public_ticket_types" not in context:
            continue
        missing = [t for t in context["missing_public_ticket_types"] if t in _PUBLIC_TYPES]
        published = required - len(missing)
        detail = re.sub(r"published \d+ of \d+ required",
                        f"published {published} of {required} required", row["detail"] or "")
        detail = re.sub(r"Missing tiers: \[[^\]]*\]", f"Missing tiers: {missing}", detail)
        values = {
            "context": context,
            "title": f"Only {published}/{required} public tickets published",
            "detail": detail,
        }
        if not row["resolved"] and not missing:
            values.update(resolved=True, resolved_at=sa.func.now())
        bind.execute(_alerts.update().where(_alerts.c.id == row["id"]).values(**values))


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "accumulator_tickets" not in tables:
        return
    _purge_tickets(bind)
    _scrub_metadata(bind)

    columns = {c["name"] for c in sa.inspect(bind).get_columns("accumulator_tickets")}
    if "high_risk_label" in columns:
        op.drop_column("accumulator_tickets", "high_risk_label")

    if bind.dialect.name == "postgresql":
        labels = bind.execute(sa.text("SELECT unnest(enum_range(NULL::tickettype))::text")).scalars().all()
        if "AGGRESSIVE" in labels:
            op.execute("ALTER TYPE tickettype RENAME TO tickettype_old")
            op.execute("CREATE TYPE tickettype AS ENUM ('SAFE', 'BALANCED', 'BEST_VALUE')")
            op.execute(
                "ALTER TABLE accumulator_tickets ALTER COLUMN ticket_type "
                "TYPE tickettype USING ticket_type::text::tickettype"
            )
            op.execute("DROP TYPE tickettype_old")


def downgrade():
    # Schema only: the purged Aggressive tickets are not recoverable here.
    bind = op.get_bind()
    if "accumulator_tickets" not in set(sa.inspect(bind).get_table_names()):
        return
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE tickettype ADD VALUE IF NOT EXISTS 'AGGRESSIVE' AFTER 'BALANCED'")
    columns = {c["name"] for c in sa.inspect(bind).get_columns("accumulator_tickets")}
    if "high_risk_label" not in columns:
        op.add_column(
            "accumulator_tickets",
            sa.Column("high_risk_label", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
