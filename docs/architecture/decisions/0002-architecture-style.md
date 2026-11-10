# ADR-0002 — Architecture style and process topology

**Status:** Accepted

## Context
Core product logic (billing, entitlements, publication) must be testable without Telegram. Long-running work (discovery, AI, publishing) must not share a failure domain with update polling. Single VPS, small team.

## Decision
**Modular monolith, hexagonal layering, one container image, multiple process roles.**

```
src/opengenerate/
  domain/        pure rules, value objects, errors (no I/O, no Telegram, no SQL)
  application/   use-cases/workflows, transaction boundaries, state machines, ports (Protocols)
  infrastructure/ adapters: db (SQLAlchemy 2 async), telegram, http (SSRF-safe), llm, crypto, media, metrics
  interfaces/    telegram UI adapters (handlers, views, keyboards), ops HTTP, CLI
  bootstrap/     composition root, settings, role entrypoints
```
Dependency rule (enforced by `import-linter` in CI): `domain ← application ← interfaces/infrastructure`; `domain` imports nothing from the others; `application` imports only `domain` and its own ports; Telegram types never appear in `domain`/`application`.

**Roles (same image, different command):**
| Role | Responsibility | Scaling rule |
|---|---|---|
| `bot` | Telegram updates (long polling by default; webhook optional), UI handlers, interactive workflows | exactly **1** polling instance |
| `worker` | scheduler tick, discovery/extraction, AI pipeline, publisher, outbox dispatcher, janitors, backups trigger | scheduler authority = advisory-lock leader; job executors may run N |
| `migrate` (one-shot) | Alembic upgrade + preflight | run before role start |
The ops HTTP server (`/livez /readyz /healthz /metrics`) is embedded in each role on an internal port.

## Alternatives rejected
- **Microservices:** no independent scaling/ownership requirement; adds network failure modes and distributed transactions around money.
- **Single process:** one stuck extraction or AI call competes with polling and shutdown.

## Consequences
Clear module ownership (see blueprint §4); future extraction of any module to its own service stays possible via ports, but is not planned.

## Revisit trigger
Sustained need to scale a role independently beyond one VPS, or a team split with separate release cadences.
