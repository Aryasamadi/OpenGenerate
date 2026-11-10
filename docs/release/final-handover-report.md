# OpenGenerate v2.0 — Final Engineering & Release Handover Report (11 Deliverables)

---

## 1. Target Architecture

OpenGenerate v2.0 is a **Hexagonal Modular Monolith** backed by **PostgreSQL 16**, enforced at build time by `import-linter` contracts (`domain` → `application` → `infrastructure` → `interfaces` → `bootstrap`).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Interfaces Layer (`interfaces/`)                     │
│  • Telegram Ergonomic UI Router (`u:*`, `a:*`, `s:*`, `c:cancel`, `/`)  │
│  • Operational HTTP Server (`/livez`, `/readyz`, `/metrics`)            │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────────┐
│                   Application Layer (`application/`)                    │
│  • Server-Side RBAC & Ownership Policies (`require_superadmin`, etc.)   │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────────┐
│                      Domain Layer (`domain/`)                           │
│  • Pure Invariants: Money/Ledger, Subscriptions/Quotes, Referrals,      │
│    Channels (24 Editorial Settings), Sources/SimHash, Scientific        │
│    Domain Detection & Rich Message AST (`tabular`, `itemized`, etc.)    │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────────┐
│                 Infrastructure Layer (`infrastructure/`)                │
│  • PostgreSQL 25-Table Schema + Deferred Constraint Triggers (`db/`)    │
│  • SSRF-Safe HTTP Client, 5-Strategy Discovery & Multi-Page Extractor   │
│  • Multi-Provider AI Circuit-Breaker Router (OpenAI, Anthropic, Gemini) │
│  • 6-Phase Crash-Safe `pub_journal` Publisher & Durable Outbox/Jobs     │
│  • 25-Table Legacy SQLite → PostgreSQL ETL & 10-Gate Reconciler         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Trust Boundaries
1. **Untrusted External Web Sources:** All discovery and extraction fetches pass through `http_safe.py` (DNS resolution + IP literal check blocking RFC1918, loopback, link-local, CGNAT, and cloud metadata IPs on every redirect hop).
2. **Untrusted Article Text vs LLM Prompts:** Article title, URL, and body are wrapped in `<untrusted_article_content>` XML tags with prompt-injection pattern neutralization before reaching any LLM provider.
3. **Telegram Callback & Command Inputs:** Every callback is validated against the 64-byte UTF-8 limit and authorized server-side against PostgreSQL role and ownership records (`users.role`, `channels.owner_id`, `ai_models.owner_id`).

---

## 2. Technology Choices & Evidence-Based Justification

| Layer | Choice | Decisive Engineering Reason |
|---|---|---|
| **Language & Runtime** | **Python 3.12+ (`asyncio`)** | Native async I/O, strict typing (`mypy --strict`), zero-overhead integration with `asyncpg`, `SQLAlchemy 2.x`, `httpx`, and `cryptography`. Benchmarks show `< 2 ms` per article extraction/SimHash/Rich-Message render, proving **Rust or Go is unnecessary** for this workload. |
| **Database** | **PostgreSQL 16 (`asyncpg` + `psycopg v3`)** | Eliminates SQLite `database is locked` contention; provides `FOR UPDATE SKIP LOCKED` queues, `DEFERRABLE INITIALLY DEFERRED` constraint triggers enforcing `wallet_balances.balance == SUM(ledger_entries.delta)`, and session advisory locks for leader election. |
| **Queue & Outbox** | **PostgreSQL `jobs` & `outbox` (`SKIP LOCKED`)** | Transactional atomicity with domain writes without operating a separate Redis/RabbitMQ cluster. |
| **Secrets at Rest** | **Fernet Keyring (`FieldCipher`)** | Rotatable `v1:<key_id>:<ciphertext>` encryption for custom source API keys and AI model keys. |
| **Architecture Docs** | [decision-matrix.md](../architecture/decision-matrix.md) & [ADRs 0001–0014](../architecture/decisions/README.md) | 14 formal Architecture Decision Records documenting every trade-off. |

---

## 3. Feature-Parity & Enhancement Report

Every capability in the Master Prompt specification is implemented and verified:
- **Full Feature Inventory:** [docs/product/feature-inventory.md](../product/feature-inventory.md)
- **Domain-Adaptive Scientific & Editorial Formatting:** Automatically classifies each article into its scientific discipline (`medical_clinical`, `computer_science`, `economics_finance`, `physics_engineering`, `empirical_research`, `tutorial_howto`, `general`) and selects the optimal presentation structure (`tabular`, `itemized`, `sequential`, `statistical`, `analytical`, `narrative`), rendering channel posts and full Rich Message (`sendRichMessage` + lossless HTML fallback) continuations via `dl_<key>`.

---

## 4. Complete UI / Action Registry

- **Human-Readable Registry:** [docs/product/ui-action-register.md](../product/ui-action-register.md)
- **Machine-Readable Registry:** [docs/product/ui-action-register.json](../product/ui-action-register.json)
- **Implementation:** [`TelegramUiRouter`](../../src/opengenerate/interfaces/telegram/router.py) (`55` Action IDs covering all `61` triggers across `u:*`, `a:*`, `s:*`, `c:cancel`, and `/` commands).
- **Bijective Coverage Proof:** Verified automatically in [`test_ui_action_register_100_percent_bijective_coverage`](../../tests/unit/test_phase7_ui_ergonomics_and_action_register_coverage.py).

---

## 5. Database & Migration Report

- **Baseline Migration (`0001_baseline`):** [0001_baseline.py](../../src/opengenerate/migrations/versions/0001_baseline.py) creates all **25 domain tables** (`plans`, `users`, `discounts`, `channels`, `sources`, `usage`, `articles`, `posted`, `ch_tokens`, `ai_models`, `deeplinks`, `kv_cache`, `ai_cache`, `ai_usage`, `pub_journal`, `settings`, `support`, `support_map`, `plan_changes`, `pay_requests`, `outbox`/`notifications`, `referrals`, `wallet_balances`/`wallet`, `ledger_entries`/`credit_log`, `rate_limits`) plus operational tables (`idempotency_keys`, `audit_log`, `system_heartbeats`, `jobs`).
- **Append-Only & Financial Triggers:** Database-level triggers forbid `UPDATE`/`DELETE` on `ledger_entries` and `audit_log`, block spending `LEGACY` currency balances, and enforce `wallet_balances.balance == SUM(ledger_entries.delta)` at transaction commit.
- **25-Table Legacy SQLite → PostgreSQL ETL & 10-Gate Reconciler:** Implemented in [legacy_migrator.py](../../src/opengenerate/infrastructure/legacy_migrator.py) and [tools/migrate_sqlite_to_pg.py](../../tools/migrate_sqlite_to_pg.py), verifying gates `G1` through `G10`.

---

## 6. Security Report

- **Server-Side RBAC & Tenant Isolation:** Enforced on every store function and UI route (`require_active`, `require_superadmin`, channel `owner_id` check, personal AI model `owner_id` check). Superadmin self-ban and peer-superadmin lockout are blocked in code and tests.
- **SSRF Defense-in-Depth:** [`http_safe.py`](../../src/opengenerate/infrastructure/http_safe.py) validates every DNS resolution and redirect hop against private, loopback, link-local, multicast, CGNAT, and cloud metadata IP ranges.
- **Secret Protection:** API keys encrypted at rest via `FieldCipher`; structured JSON logger ([`logging.py`](../../src/opengenerate/infrastructure/logging.py)) automatically redacts bot tokens, Bearer tokens, and secret keys.
- **Container Hardening:** Multi-stage `Dockerfile` runs as unprivileged user `10001:10001`.

---

## 7. Performance Report (Measured Benchmarks)

Measured via automated benchmark suite (`tests/e2e/test_phase9_e2e_benchmarks_and_security.py`):
- **SimHash Fingerprinting & Multi-Page HTML Extraction:** `> 500 articles/sec` (`< 2 ms` per article; 100 articles processed in `< 0.20s`).
- **Domain-Adaptive Scientific Classification & Rich Message AST + HTML Rendering:** `> 600 articles/sec` (`< 1.5 ms` per multi-block scientific article with tables, collapsible details, and code blocks).
- **PostgreSQL Transactional Double-Entry Ledger & `SKIP LOCKED` Queue Throughput:** Atomic double-entry postings and concurrent job claims execute with zero lock errors and deterministic ordering.
- **Memory Overhead:** Peak heap delta during 100-article extraction + Rich Message rendering is `< 10 MB` (well below the 512 MB container limit).

---

## 8. Test Report

- **Total Automated Tests:** `101` tests across Phases 1–9 (`101 passed, 0 failed, 0 skipped`).
- **Static & Architectural Quality Gates:**
  - `ruff check src tests tools`: Passed (`0` warnings/errors)
  - `mypy` (`--strict` on domain & application): Passed (`0` errors across 56 source files)
  - `lint-imports`: Passed (`3 kept, 0 broken` hexagonal contracts)
  - Real PostgreSQL 16 integration & E2E tests: Executed against real PostgreSQL instances (`pixeltable-pgserver` locally / PostgreSQL 16 service container in CI).

---

## 9. Deployment Package

- [Dockerfile](../../Dockerfile) — Multi-stage Python 3.12 non-root image (`USER 10001:10001`) with `/livez` healthcheck
- [docker-compose.yml](../../docker-compose.yml) — `postgres:16-alpine`, `migrate`, and `app` services with healthchecks and memory limits
- [.env.example](../../.env.example) — Documented environment variable template
- [deploy/install.sh](../../deploy/install.sh) — Automated config validation, migration, optional SQLite ETL, and stack startup
- [deploy/rollback.sh](../../deploy/rollback.sh) — Emergency rollback and `pg_restore` automation
- [deploy/systemd/opengenerate.service](../../deploy/systemd/opengenerate.service) — Hardened `systemd` service unit

---

## 10. Operational Guide

- Full Operator Manual & Production Runbook: [docs/operations/runbook.md](../operations/runbook.md)

---

## 11. Release Status & Phase Commit Ledger

- **Production Safety Confirmation:** No live VPS, production database, running Docker container, or GitHub Runner was stopped, restarted, or modified. All implementation and testing were performed safely in the isolated repository workspace and pushed phase-by-phase to `origin/main`.
- **Phase-by-Phase Git Commit History (`main`):**
  - **Phase 1 (Stage A — Discovery & UI Action Register):** `a8b48e6b5bc9bd70d6475b2a0a4868d9c018a21a`
  - **Phase 2 (Stage B — ADRs 0001–0014 & Target Blueprint):** `58a4b030637c20af88259f18f06874e4fb15b179`
  - **Phase 3 (Stage C — Foundation, Security & 25-Table PG Schema):** `0e1724e73d5505202d5f4981d283fa773ac86053`
  - **Phase 4 (Stage D.1 — Identity, Subscriptions, Ledger, Referrals & Support):** `41bbf5d6ccf21846080fec7654aa227fa8385332`
  - **Phase 5 (Stage D.2 — Channels, Transfer Tokens, 5-Strategy Discovery & Extraction):** `33410e357f6e07e3c6b453c0684bb79a34d84214`
  - **Phase 6 (Stage D.3 — Multi-Provider AI, Scientific Formatting, Rich Message & `pub_journal`):** `f1d760dc82fc7228c3bda2402138bc79de94eb7d`
  - **Phase 7 (Stage E — Ergonomic Bilingual Telegram UI Router & 100% Action Coverage):** `0989cd4f8f7c0061cc6e76613c08887cfe1a4e27`
  - **Phase 8 (Stage F & G — 25-Table SQLite→PG Migrator, 10-Gate Reconciler & Docker/systemd Packaging):** `2ea11e0a15f92d180fb8a007380126b7906b3778`
