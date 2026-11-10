# ADR-0003 — PostgreSQL 16 as the system of record

**Status:** Accepted. **Supersedes** the provisional statement in `current-state.md` (Phase 1) that SQLite-WAL would be the primary engine; that wording is corrected in the same commit.

## Context
Concurrent writers (bot, scheduler, AI/publish workers, outbox dispatcher), money/ledger invariants, multi-process work claiming, backup/PITR, and a prompt requirement not to treat several replicas on one SQLite file as scalability. Cloudflare D1 is out (network-bound, no multi-statement local transaction guarantees equal to a local engine; baseline itself marked it unsupported).

## Evidence gathered (this phase, on the operator's workstation, Windows + Python 3.13, no Docker)
| Capability needed | Observed |
|---|---|
| Real PostgreSQL available for local/CI tests without Docker | `pixeltable-pgserver` wheel installs; **PostgreSQL 16.15** started in ≈19.5 s cold |
| Job claiming without double-processing | two concurrent transactions with `FOR UPDATE SKIP LOCKED LIMIT 2` received disjoint rows (`[1,2]` vs `[3]`) |
| Scheduler leader election | `pg_try_advisory_lock` returned `true` |
| Invariant enforcement in DB | `CHECK (balance >= 0)` raised `CheckViolation` |
(`pgserver` itself had no wheel for this platform; the maintained fork did. Spike code kept out of the repo.)

## Decision
- **PostgreSQL 16**, accessed via **SQLAlchemy 2.x (async) + asyncpg**, schema managed by **Alembic** (explicit SQL-visible migrations, expand/contract policy). Raw SQL is allowed for hot/atomic statements (`UPDATE … RETURNING`, claims).
- **No dual-dialect support.** Tests run against real PostgreSQL only: locally through the `pgserver` fork (fixture starts a throw-away cluster), in CI through a `postgres:16` service container. SQLite is used only as the *source* format of the legacy-data ETL (ADR-0013).
- Required schema practices: `STRICT`-like typing (`BIGINT` money, `TIMESTAMPTZ` UTC, `TEXT` + `CHECK` enums or native enums), FKs everywhere with explicit `ON DELETE`, unique constraints for every idempotency key and dedupe invariant (`published(channel_id, article_hash)`), partial unique indexes for "one active owner/pending payment per user", `JSONB` only for genuinely schemaless blobs (channel settings versioned with a schema version, rich AST).
- Pool sizing explicit per role; statement timeout and lock timeout set per session; `application_name` for diagnostics.
- Tuning profile for a 2 GB VPS provided in Phase 8 (`shared_buffers`, `max_connections`≤40 with pooling in-app).

## Alternatives rejected
SQLite WAL (single writer, multi-process contention, no PITR, no `SKIP LOCKED`; kept simpler operations but fails the concurrency and recovery requirements); D1; dual-dialect abstraction (doubles test surface and hides dialect-specific atomicity).

## Consequences
+1 container (~150–300 MB), one-time ETL, backup tooling is `pg_dump`/WAL instead of file copy. Memory/footprint measured in Phase 9 against the 2 GB reference VPS (open item OI-5: operator to confirm target VPS size).

## Revisit trigger
Evidence that expected scale is so small that operational simplicity outweighs concurrency needs — would require operator sign-off because it weakens guarantees.
