# Decision Matrix (Stage B)

Scoring: 1 (poor) – 5 (excellent) per criterion. Weights reflect the operator brief: correctness, reliability and security dominate; raw speed and fashion do not.
Scores are engineering judgments backed by the evidence column and by the spikes/ADRs referenced; where a score rests on an unmeasured assumption it is marked `(A)` and a benchmark is scheduled in Phase 9.

Criteria (11, per Master Prompt §15 Stage B): **Correctness, Reliability, Performance, Memory, Operating cost, Development velocity, Maintainability, Deployment complexity, Ecosystem maturity, Migration risk, Future scaling.**

Weights (sum = 100): Correctness 20, Reliability 18, Maintainability 10, Deployment complexity 9, Ecosystem maturity 8, Development velocity 8, Migration risk 7, Future scaling 6, Operating cost 5, Memory 5, Performance 4.

## D1. Language / runtime

| Criterion (weight) | Python 3.13 (asyncio) | Go | Rust | TypeScript (Node) |
|---|---|---|---|---|
| Correctness (20) | 4 (typing + pydantic + mypy strict) | 4 | 5 | 3 |
| Reliability (18) | 4 | 5 | 5 | 3 |
| Maintainability (10) | 5 | 4 | 3 | 4 |
| Deployment (9) | 4 | 5 | 4 | 4 |
| Ecosystem (8) — Telegram, HTML extraction, feeds, LLM SDK/HTTP | 5 | 3 | 2 | 4 |
| Velocity (8) | 5 | 3 | 2 | 4 |
| Migration risk (7) — domain logic is Python-shaped (PTB, trafilatura-class extraction) | 5 | 2 | 1 | 3 |
| Scaling (6) | 3 (A) | 5 | 5 | 3 |
| Cost (5) | 4 | 5 | 5 | 4 |
| Memory (5) | 3 (A) | 5 | 5 | 3 |
| Performance (4) | 3 (A) | 5 | 5 | 3 |
| **Weighted total /500** | **418** | 417 | 395 | 340 |

**Result: Python 3.13.** Python and Go are effectively tied on the score (418 vs 417), so the score alone is *not* the reason: the decisive, non-numeric factors are the Python-native extraction/feed/Telegram ecosystem and the absence of any measured need for Go's memory/CPU advantage. Memory/Performance/Scaling scores for Python are unmeasured assumptions `(A)`; if Phase 9 measurements miss the targets in `target-blueprint.md §10`, ADR-0001's Rust gate (isolated component behind a port) is the remedy, not a rewrite. Rust is *not* adopted now.

## D2. Telegram framework

| Criterion | python-telegram-bot 22.x | aiogram 3 | raw httpx client only |
|---|---|---|---|
| Correctness / typing of Bot API | 4 | 4 | 2 |
| Maturity / maintenance | 5 | 4 | 3 |
| Fit with layered design (thin adapters) | 4 | 4 | 5 |
| Handles new Bot API methods | 2 (lags) → mitigated by raw client | 3 | 5 |
| Rate-limit/`retry_after` handling | 4 | 3 | 2 (must build) |

**Result: PTB 22.x for transport + a small in-house `RawBotApi` (httpx) for methods PTB lacks (`sendRichMessage`, `sendRichMessageDraft`) behind one `TelegramGateway` port.**

## D3. Database

| Criterion (weight) | PostgreSQL 16 | SQLite (WAL) single node |
|---|---|---|
| Correctness (20): constraints, `CHECK`, FK, deferred constraints, strict types, serializable/row locks | 5 | 4 (weak typing unless `STRICT`; table-level write lock) |
| Reliability (18): PITR, replication, crash safety under multi-process | 5 | 3 (single writer; multi-process writers contend; one disk) |
| Maintainability (10) | 4 | 5 |
| Deployment (9) | 3 (one extra container) | 5 |
| Ecosystem (8) | 5 | 4 |
| Velocity (8) | 4 | 5 |
| Migration risk (7) from legacy SQLite | 3 (ETL needed, tested) | 5 |
| Scaling (6): multiple workers, leader election, `SKIP LOCKED` queue | 5 | 1 |
| Cost (5) | 4 | 5 |
| Memory (5) | 3 (~150–300 MB tuned (A)) | 5 |
| Performance (4) | 4 | 4 |
| **Weighted total /500** | **431** | 408 |

**Result: PostgreSQL 16.** Deciding factors: (1) bot process, scheduler, AI workers and outbox dispatcher all write concurrently and must claim work without double-processing (`FOR UPDATE SKIP LOCKED`, advisory-lock leader election); (2) money/ledger invariants enforced by database constraints; (3) real backup/PITR story; (4) the prompt explicitly forbids calling several replicas on one SQLite file "scalable".
Cost accepted: one more container (~200 MB RAM) and a one-off ETL from the legacy SQLite file.

## D4. Background execution / durable queue / scheduler

| Option | Correctness | Reliability | Deployment complexity | Verdict |
|---|---|---|---|---|
| PostgreSQL jobs + outbox tables (`SKIP LOCKED`), in-process asyncio workers | 5 | 5 (same transaction as business write) | 5 (no new infra) | **Chosen** |
| Redis + Celery/RQ/arq | 3 (dual-write between DB and broker) | 3 | 2 | Rejected: dual-write breaks money/outbox atomicity |
| RabbitMQ/Kafka | 3 | 4 | 1 | Rejected: unjustified for one VPS |
| APScheduler in-memory | 2 | 2 (lost on restart) | 5 | Rejected for state; only a tick timer is used |

## D5. Process topology

| Option | Verdict |
|---|---|
| Single process (bot + scheduler + workers) | Simple but one slow task shares the event loop with polling → rejected |
| **Modular monolith, one image, three roles (`bot`, `worker`, `ops`-embedded), selected by command** | **Chosen** |
| Microservices | Rejected: no independent scaling/ownership need; adds failure modes |

## D6. AI provider integration

| Option | Verdict |
|---|---|
| Vendor SDKs (openai, anthropic, google-genai) | Rejected: three heavy deps, divergent error models, hard to apply uniform SSRF/timeouts/redaction |
| LiteLLM | Rejected: very large dependency surface and own routing semantics that conflict with our circuit-breaker/budget ownership |
| **Thin httpx adapters per wire format (OpenAI-compatible chat, Anthropic messages, Gemini generateContent) behind `LlmProvider` port** | **Chosen** — one SSRF-safe transport, uniform error taxonomy |

## D7. Media / object storage

Single VPS, receipts are kept as Telegram `file_id` + metadata only; downloaded article media is transient (temp dir with size/age caps, cleaned by janitor). **No object storage (S3/MinIO) now.** A `MediaStore` port keeps S3 an additive later change.

## D8. Secrets & configuration

| Option | Verdict |
|---|---|
| **Env/`*_FILE` secrets validated by pydantic-settings; at-rest field encryption with `cryptography` `MultiFernet` (rotatable)** | **Chosen** |
| Vault / cloud KMS | Rejected for now (operational cost); `KeyProvider` port allows it later |

## D9. Observability

structlog (JSON, correlation ids, redaction processor) + Prometheus-format `/metrics` + `/livez` `/readyz` `/healthz` on an internal port; durable alerts via the notification outbox to superadmins. No hosted APM dependency.

## D10. Deployment & upgrade

| Option | Verdict |
|---|---|
| **Docker Compose; immutable images tagged by Git SHA built in CI, pushed to GHCR, deployed by digest; scripted backup → preflight → migrate → switch → health → rollback** | **Chosen** |
| Kubernetes/Nomad | Rejected: one VPS |
| Bind-mounted source | Rejected: ambiguous runtime code (Master Prompt §12.2) |
| systemd + venv | Rejected as primary: weaker rollback of dependencies |
