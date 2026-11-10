# OpenGenerate v2.0 — Intelligent Scientific & Editorial Telegram Channel Automation Platform

OpenGenerate is a production-grade, multi-tenant Telegram Channel Automation SaaS built on a **Hexagonal Modular Monolith** architecture with **PostgreSQL 16**, **Multi-Provider AI Orchestration** (OpenAI, Anthropic, Gemini), **Domain-Adaptive Scientific Formatting & Rich Message Rendering**, an **Immutable Double-Entry Financial Ledger**, and a **6-Phase Crash-Safe Publication Engine (`pub_journal`)**.

---

## Key Capabilities

- **5-Strategy SSRF-Safe Source Discovery & Multi-Page Extraction:** Automatic detection across RSS/Atom (XXE-hardened), JSON Feed 1.1, XML Sitemap, HTML article links, and Custom JSON APIs, followed by multi-page pagination merging and 64-bit SimHash near-duplicate filtering.
- **Domain-Adaptive Scientific & Editorial Structuring:** Automatically detects each article's scientific discipline (`medical_clinical`, `computer_science`, `economics_finance`, `physics_engineering`, `empirical_research`, `tutorial_howto`, `general`) and formats channel posts in the discipline's ideal layout (`tabular`, `itemized`, `sequential`, `statistical`, `analytical`, `narrative`), with interactive full-article continuation inside the bot (`dl_<key>`) supporting full Rich Message blocks and lossless HTML fallback.
- **Crash-Safe 6-Phase Publication Engine (`pub_journal`):** Guarantees zero duplicate channel posts and zero lost daily quotas across network timeouts and process crashes.
- **Double-Entry Financial Ledger & Multi-Currency Subscriptions (`TMN` / `USD` / `LEGACY`):** Database-enforced invariant `wallet_balances.balance == SUM(ledger_entries.delta)` with atomic payment approvals, proration credits, discount caps, and bounded referral rewards (`0..50%`).
- **Ergonomic Bilingual Telegram Interface (`fa` / `en`):** Visual RTL/LTR breadcrumbs, Unicode quota progress gauges, two-step confirmation dialogs for destructive operations, and 100% coverage of all 55 Action IDs and 61 triggers.
- **25-Table Legacy SQLite → PostgreSQL Migrator & 10-Gate Reconciler:** Read-only snapshot inspector, deterministic ETL, and automated 10-gate verification (`G1`..`G10`).

---

## Documentation Index

1. **Discovery & Product Specification (Stage A):**
   - [Current-State Architecture Analysis](docs/architecture/current-state.md)
   - [Exhaustive Feature Inventory](docs/product/feature-inventory.md)
   - [UI Action Register (Markdown)](docs/product/ui-action-register.md) & [UI Action Register (JSON)](docs/product/ui-action-register.json)
   - [Current Risk Register](docs/architecture/current-risks.md)
2. **Target Architecture & Decisions (Stage B):**
   - [Architecture Decision Matrix](docs/architecture/decision-matrix.md)
   - [ADRs 0001–0014 Index](docs/architecture/decisions/README.md)
   - [Target Blueprint & Sequence Diagrams](docs/architecture/target-blueprint.md)
   - [25-Table Migration & Reconciliation Plan](docs/architecture/migration-plan.md)
3. **Operations & Final Release Handover (Stages G & H):**
   - [Operator Manual & Production Runbook](docs/operations/runbook.md)
   - [Final 11-Deliverable Engineering & Release Handover Report](docs/release/final-handover-report.md)

---

## Quick Start (Development & Testing)

```bash
# Run static quality gates
ruff check src tests tools
mypy
lint-imports

# Run the complete unit, integration, and E2E test suite
pytest -v
```
