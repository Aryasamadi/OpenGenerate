# ADR-0011 — Observability

**Status:** Accepted

## Decision
- **Logs:** `structlog` JSON to stdout; fields `ts, level, event, role, correlation_id, update_id, user_id (hashed where required), channel_id, job_id`; redaction processor (ADR-0009); Docker log rotation configured in Compose.
- **Correlation:** a `correlation_id` is created per Telegram update/job and propagated through use-cases, jobs, outbox rows and outbound HTTP.
- **Metrics (Prometheus text on internal `/metrics`):** queue depth/age per kind, cycle duration, source errors by class, AI attempts/outcomes/latency per provider, publication outcomes and journal phases, rich-send native vs fallback with reason, outbox pending/dead-letter, payments awaiting notification, DB pool/latency, process RSS, event-loop lag. Metrics are optional to scrape; the **health view and alerts do not depend on an external Prometheus**.
- **Probes:** `/livez` (process + event loop responsive), `/readyz` (DB reachable, migrations at expected head, role-specific readiness, e.g., polling started / scheduler leader-or-follower state), `/healthz` (detailed JSON with three-state `ok|degraded|unknown`; `unknown` = telemetry missing, never rendered as a healthy zero).
- **Health inputs (superadmin view):** scheduler heartbeat age and tick interval, concurrent cycles, last successful publication age, channels and ready queue, pending/unknown journals, approved payments with incomplete customer notification, receipts not yet delivered to any superadmin, pending/dead-letter outbox, healthy/total models, rich-mode and fallback counters, DB size and integrity/age of last backup, RSS, recovery interval, code version (git SHA).
- **Durable alerts** (rows in `alerts` + outbox to superadmins, de-duplicated and rate-limited, auto-resolved): stalled scheduler, dead-letter growth, repeated AI failure, unnotified money event, backup failure/age, low disk, no successful publication within configured window.

## Consequences
Operators diagnose from Telegram alone; Prometheus/Grafana is an optional add-on, not a dependency.
