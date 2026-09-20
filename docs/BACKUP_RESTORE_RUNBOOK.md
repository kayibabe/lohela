# Backup / restore runbook

Status: tooling built and rehearsed locally on 2026-09-20 with a full
backup→restore→row-count-verification round trip, **then rehearsed against
production itself on 2026-09-20** (after the deadlock/backup-tooling
deploy) — a real backup was taken, pulled off-box, and restored into a
disposable container with row counts matching production exactly. See the
evidence log for details. No dump files were retained afterward.

## Important: dev/prod Postgres version drift

Local dev (`docker-compose.yml`) runs **Postgres 15** (`postgres:15-alpine`).
Production (Railway) runs **Postgres 18.6**, confirmed via a read-only query
against the live database on 2026-09-20. `pg_dump`/`pg_restore` are not
guaranteed to work correctly against a server *newer* than the client tool
itself (Debian trixie's default `postgresql-client` package only ships
v17), so the Dockerfile now pulls `postgresql-client-18` from the official
PGDG apt repository to match production exactly. This version drift between
dev and prod is itself worth a decision at some point (upgrade local dev to
Postgres 18, or accept the gap) — flagged here, not fixed, since it's outside
what was asked.

## What exists

- [`backend/scripts/backup_db.py`](../backend/scripts/backup_db.py) — runs
  `pg_dump --format=custom`, then validates the file with `pg_restore --list`
  before declaring success. A dump that fails validation is deleted, not left
  behind looking like a good backup.
- [`backend/scripts/restore_db.py`](../backend/scripts/restore_db.py) —
  wraps `pg_restore`. Has no default target (you must always name
  `--target-url` explicitly) and refuses to run without `--yes`. Defaults to
  restoring into the current state of the target (errors loudly on any
  conflicting object) — pass `--clean` only when you intend to drop and
  recreate everything in the dump.
- [`backend/Dockerfile`](../backend/Dockerfile) now installs
  `postgresql-client-18` (via PGDG's apt repo, since Debian trixie's own
  repos top out at 17), so both scripts run inside the deployed `web`/
  `worker` containers once that image ships.

## What does NOT exist yet (open decisions, not done)

- **No automated/scheduled backups.** This is manual tooling for now. Wiring
  a Celery Beat task to run `backup_db.py` on a schedule needs a durable
  off-box destination decided first (Railway volume vs. S3-compatible object
  storage vs. Railway's own managed-Postgres backup feature) — that's a
  storage/cost decision, not made here.
- **Railway's built-in Postgres backups** (if enabled on the plan) have not
  been checked in the dashboard. Worth checking before building a redundant
  system — this repo previously had *zero* backup mechanism, in-house or
  managed.
- **Dev/prod Postgres version parity** (15 vs 18) — not addressed here.

## Local rehearsal (safe — no production contact)

Full round trip rehearsed against the local dev stack's database, restoring
into a disposable throwaway container (never the dev DB itself), using a
`postgresql-client-15` toolchain to match both ends exactly:

```bash
python scripts/backup_db.py --url postgresql://lohela:lohela_pass@postgres:5432/lohela --output-dir /tmp/backups --label rehearsal
python scripts/restore_db.py --file <dump> --target-url postgresql://postgres:test@<disposable-container>:5432/restore_check --yes
```

Result: dump validated (32 tables with data, 1.27 MB), restore completed
clean, and post-restore row counts matched the source exactly (predictions:
2,496 / 2,496; matches: 8,280 / 8,280). Full output in the evidence log.

An earlier attempt using a `postgresql-client-17` toolchain against the same
v15 target failed with `unrecognized configuration parameter
"transaction_timeout"` — a real client/server version incompatibility (that
parameter is v17+ only), not a bug in the scripts. This is exactly the
failure mode `postgresql-client-18` in the Dockerfile is pinned to avoid
against production's actual v18 server.

## Running against production

Production's `DATABASE_URL` points at `postgres.railway.internal`, which is
**only reachable from inside a Railway service** — not from a local machine,
and not through `railway run` (which injects env vars but still executes
locally). Two ways to actually run this against prod:

1. **After the Dockerfile change is deployed** (recommended — this is what
   it was built for): the `web`/`worker` containers will have
   `postgresql-client-18` baked in, exactly matching the server.
   ```
   railway ssh --service worker -- python scripts/backup_db.py --output-dir /tmp/backups --label pre_migration
   ```
   The dump lands in the container's ephemeral filesystem — it does **not**
   survive a restart/redeploy, so pull it off-box immediately (e.g. base64
   over `railway ssh` into a local file) before doing anything else.

2. **From an operator machine with the public connection string**, if
   Railway's Postgres has public networking enabled (check the Railway
   dashboard for a `DATABASE_PUBLIC_URL`-style variable). Run
   `backend/scripts/backup_db.py --url <that URL>` directly with a
   `postgresql-client-18` toolchain. **Never paste that URL into chat, a
   commit, or a log** — it contains the database password.

This has now been run once (2026-09-20), via option 1, after the Dockerfile
change was deployed. See the evidence log.

## Migration safety procedure (ties to the "pending migrations" requirement)

Before running `alembic upgrade head` against production:

1. Take a backup (`backup_db.py`, labeled `pre_migration_<revision>`).
2. Record the JSON summary it prints (file, size, table count) in the
   evidence log for that migration.
3. Apply the migration.
4. Verify: `alembic current` shows the new head, spot-check the
   new/changed columns, run the app's smoke path.
5. If verification fails: `restore_db.py --clean` from the pre-migration
   dump, re-investigate before retrying.

As of this writing, production's `alembic_version` is already at head
(`c3f8a1d9e2b7`) — there is no migration currently pending. This procedure
is the plan for the *next* one, not a fix for an outstanding backlog.

## Evidence log

| Date | Action | Result |
| --- | --- | --- |
| 2026-09-20 | Confirmed prod Postgres version via read-only query | 18.6 (Debian 18.6-1.pgdg13+2) — dev is 15.17 |
| 2026-09-20 | `backup_db.py` against local dev DB (v15 client) | Verified — 1,273,253 bytes, 32 tables with data, validated via `pg_restore --list` |
| 2026-09-20 | `restore_db.py` into disposable throwaway container (v15 client) | Verified — restore completed clean, row counts matched source exactly (predictions 2496/2496, matches 8280/8280) |
| 2026-09-20 | Same rehearsal with mismatched v17 client against v15 target | Failed as expected (`unrecognized configuration parameter "transaction_timeout"`) — confirms why the Dockerfile pins the exact server-matching client version |
| 2026-09-20 | Deployed deadlock fix + `postgresql-client-18` to production (`web` 14:08:46, `worker` 14:11:02 SAST) | Verified — `pg_dump --version` on the worker container reports 18.6, matching the server exactly |
| 2026-09-20 | `backup_db.py` against **production** via `railway ssh --service worker` | Verified — 3,031,922 bytes, 32 tables with data, validated via `pg_restore --list` inside the container |
| 2026-09-20 | Pulled the production dump off-box (base64 over `railway ssh`) and byte-verified locally | Verified — 3,031,922 bytes both ends, identical |
| 2026-09-20 | Restored the production dump into a disposable `postgres:18-alpine` container (matching client/server versions) and compared row counts to the live figures confirmed earlier this session | Verified exactly — predictions 11,444, matches 1,521, odds 43,931, users 3 |
| 2026-09-20 | Cleanup after the production rehearsal | Done — disposable container removed, local decoded dump + base64 transfer file deleted, remote copy removed from the worker container's `/tmp/backups`. No production data retained anywhere outside Postgres itself. |
