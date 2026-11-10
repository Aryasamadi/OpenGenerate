# Architecture Decision Records

Format: **Status · Context · Decision · Alternatives rejected · Consequences · Revisit trigger.**
Scoring evidence lives in [`../decision-matrix.md`](../decision-matrix.md).

| ADR | Title | Status |
|---|---|---|
| [0001](0001-language-and-runtime.md) | Language & runtime: Python 3.13; Rust gated by benchmarks | Accepted |
| [0002](0002-architecture-style.md) | Modular monolith, one image, separate process roles | Accepted |
| [0003](0003-database.md) | PostgreSQL 16 as the system of record (supersedes SQLite note in current-state) | Accepted |
| [0004](0004-money-and-ledger.md) | Integer minor units, append-only ledger, DB-enforced invariants | Accepted |
| [0005](0005-jobs-outbox-scheduler.md) | PostgreSQL job queue + transactional outbox + advisory-lock scheduler | Accepted |
| [0006](0006-telegram-transport.md) | PTB 22.x transport + raw Bot API client behind a gateway port | Accepted |
| [0007](0007-ai-provider-integration.md) | Thin per-wire-format LLM adapters, routing, breakers, budgets | Accepted |
| [0008](0008-content-formatting-and-rich-messages.md) | Rich-content AST, domain-adaptive formats, lossless downgrade | Accepted |
| [0009](0009-security-baseline.md) | AuthZ, SSRF-safe egress, encryption at rest, redaction | Accepted |
| [0010](0010-media-and-storage.md) | Media pipeline and storage (no object storage yet) | Accepted |
| [0011](0011-observability.md) | Logs, metrics, health semantics, durable alerts | Accepted |
| [0012](0012-deployment-and-release.md) | Compose, immutable images by digest, backup→migrate→verify→rollback | Accepted |
| [0013](0013-migration-strategy.md) | Legacy SQLite → PostgreSQL ETL with reconciliation gates | Accepted |
| [0014](0014-i18n-and-ui-framework.md) | Typed action registry, callback routing, bilingual catalog | Accepted |
