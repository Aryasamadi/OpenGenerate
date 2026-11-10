# ADR-0005 — Jobs, transactional outbox and scheduler

**Status:** Accepted

## Context
Work that must not be lost or duplicated: notifications about money events, publication, discovery/AI processing, support routing, broadcast. A broker separate from the database would create dual-write inconsistencies with payments/ledger.

## Decision
- **`jobs` table** (kind, payload, `run_at`, `lease_until`, `attempts`, `max_attempts`, `status`, `dedupe_key` unique-where-active) claimed with `SELECT … FOR UPDATE SKIP LOCKED`; lease renewal heartbeat; expired leases returned to the queue; exponential backoff with full jitter; classified errors (`Retryable`, `Permanent`, `UnknownOutcome`).
- **`outbox` (notifications)**: rows are inserted **in the same transaction** as the business change that requires them; a dispatcher sends via the `TelegramGateway`, distinguishes transient errors from permanent (`blocked`, `chat not found`) and moves exhausted rows to `dead_letter`, surfaced in health and alerts. Telegram `429 retry_after` is honoured per chat and globally.
- **Scheduler authority:** a single leader per deployment via `pg_try_advisory_lock(key)` held on a dedicated connection; heartbeat stored in `system_heartbeats`. Non-leaders still run job executors. Losing the lock stops scheduling immediately.
- **Fair scheduling:** each tick selects due channels ordered by `last_run_at` (oldest first) with a hard per-tick cap; per-channel concurrency 1; global concurrency semaphores per stage (fetch, extract, AI, publish); per-stage deadlines; source cooldown after failures.
- **Publication** uses a write-ahead `publication_journal` (phases `reserved → in_flight → acknowledged → settled`, plus `delivery_unknown`, `failed_safe`). `delivery_unknown` is never auto-resent; it requires admin action or reconciliation evidence.
- **Graceful shutdown:** stop claiming, wait up to `SHUTDOWN_GRACE` for in-flight, release remaining leases.
- **Startup recovery:** reclaim expired leases; settle `acknowledged` journals; mark orphan `in_flight` journals `delivery_unknown`; requeue `processing` payments whose lease expired for *inspection* (never auto-reapply).

## Alternatives rejected
Redis/Celery/arq (dual write), RabbitMQ/Kafka (unjustified), in-memory APScheduler state (lost on restart).

## Consequences
Queue throughput is bounded by Postgres (ample for hundreds of channels; measured in Phase 9). No new infrastructure.

## Revisit trigger
Sustained queue depth/latency beyond targets after tuning, or multi-region deployment.
