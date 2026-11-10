# Comprehensive Product Capability & Feature Inventory (`Stage A`)

This document inventories every functional, editorial, financial, administrative, security, and operational capability specified in the Master Prompt and operator directives.

## Status Classification Legend (Per Section 3)

- `IMPLEMENTED_AND_VERIFIED` — Specification behavior is complete, unambiguous, and mandatory for parity verification.
- `IMPLEMENTED_BUT_NOT_END_TO_END_VERIFIED` — Present in baseline design but requires full end-to-end integration/recovery testing in `OpenGenerate`.
- `PARTIAL` — Baseline had incomplete edges (e.g., queued plan chaining or refund edge policies) that must be completed deterministically in `OpenGenerate`.
- `DOCUMENTED_BUT_NOT_IMPLEMENTED` — Claimed in legacy README/docs without working implementation; resolved explicitly in `OpenGenerate`.
- `PRESENT_BUT_UNSUPPORTED_OR_EXPERIMENTAL` — Experimental paths (such as Cloudflare D1) officially retired or gated.
- `LEGACY_OR_DEPRECATED` — Deprecated artifacts (such as unknown-currency `LEGACY` balances) preserved strictly as frozen/read-only state until reconciled.
- `UNKNOWN_REQUIRES_INVESTIGATION` — External dependency behaviors (such as Telegram Bot API `sendRichMessage` runtime availability) handled via dynamic capability probing and automatic fallback.

---

## 1. End-User / Subscriber Capabilities (Section 5)

| Feature ID | Capability | Baseline Status | Target `OpenGenerate` Module & Contract | Verification Requirement |
|---|---|---|---|---|
| `FEAT-USR-01` | `/start` Registration & Deep-Link Router (`ref_<user_id>`, full-article deep-link keys) | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.onboarding` / `ui.handlers.user` | E2E test for new user, returning user, referral payload, and article deep-link rendering. |
| `FEAT-USR-02` | First-Time Language Prompt & Bilingual UI (`fa` RTL + Persian numerals, `en` LTR) | `IMPLEMENTED_AND_VERIFIED` | `domain.i18n` / `ui.views.user` | Localization parity test verifying all keys in `fa` and `en` and RTL layout. |
| `FEAT-USR-03` | Core Slash Commands (`/lang`, `/help`, `/about`, `/ref`, `/man`, `/cancel`, `/create`) | `IMPLEMENTED_AND_VERIFIED` | `ui.handlers.commands` | Command routing & stateful session cancellation test suite. |
| `FEAT-USR-04` | Mandatory Channel Membership Gate (Language-Specific Join Link & Server-Side Verification) | `IMPLEMENTED_AND_VERIFIED` | `domain.membership` / `ui.middlewares.authz` | Tests for joined, unjoined, disabled gate, and fail-safe transient Telegram API error handling. |
| `FEAT-USR-05` | Active Plan Catalog & Detailed Plan Inspection (Quotas, Duration, Bilingual Tariffs) | `IMPLEMENTED_AND_VERIFIED` | `domain.plans` / `ui.views.plans` | Catalog filtering (`is_active=1`), sorting (`sort_order`), and currency-by-language display tests. |
| `FEAT-USR-06` | One-Time Free Plan Claim & Repeat-Claim Prevention (`free_plan_used`) | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.subscription` | Concurrency test proving a user cannot claim a free plan twice. |
| `FEAT-USR-07` | Subscription Renewal, Remaining-Time Proration Upgrade, and Downgrade Prevention | `IMPLEMENTED_AND_VERIFIED` | `domain.billing.proration` / `app.workflows.subscription` | Unit & integration tests for exact proration math, same-plan renewal, and blocked paid downgrade. |
| `FEAT-USR-08` | Queued Next-Plan Chaining (`assign_plan`) | `PARTIAL` | `app.workflows.subscription` | Fully implemented in `OpenGenerate`: deterministic renewal/upgrade + scheduled expiry transition. |
| `FEAT-USR-09` | Discount Code Application & Removal on Quote Screen | `IMPLEMENTED_AND_VERIFIED` | `domain.billing.discounts` | Quote calculation tests with active, expired, exhausted, and removed discount codes. |
| `FEAT-USR-10` | Multi-Currency Wallet & Quote Breakdown (`TMN`, `USD`, Frozen `LEGACY`) | `IMPLEMENTED_AND_VERIFIED` | `domain.billing.wallet` | Invariant tests ensuring zero cross-currency mixing and non-spendability of `LEGACY` balance. |
| `FEAT-USR-11` | Negotiated / Text-Only Plan Handling | `IMPLEMENTED_AND_VERIFIED` | `domain.plans` / `ui.views.plans` | Ensures plans without numeric tariff do not generate fake numeric quotes and route to support/manual flow. |
| `FEAT-USR-12` | Payment Receipt Submission (Text, Photo, Document) & Pending-State Lock | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.payments` | Tests MIME/size limits, duplicate pending payment prevention, and superadmin outbox notification. |
| `FEAT-USR-13` | Referral Link Generation, First-Touch Attribution & Reward Dashboard | `IMPLEMENTED_AND_VERIFIED` | `domain.referrals` | Tests self-referral rejection, nonexistent referrer rejection, immutable first attribution, and stats display. |
| `FEAT-USR-14` | Durable Two-Way Customer Support Conversation (Text & Media) | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.support` | Tests user->superadmin forwarding, superadmin reply routing across restart (`support_map`), close, and rate limits. |

---

## 2. Channel Administrator Capabilities (Section 6)

| Feature ID | Capability | Baseline Status | Target `OpenGenerate` Module & Contract | Verification Requirement |
|---|---|---|---|---|
| `FEAT-ADM-01` | Admin Home Dashboard (Plan Status, Daily Usage, Channel/Source Counts, Channel List) | `IMPLEMENTED_AND_VERIFIED` | `ui.views.admin_home` | Verified against live usage counters and plan quota limits. |
| `FEAT-ADM-02` | Channel Registration via Forwarded Post, `@username`, or Negative Chat ID | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.channels` | Verifies target is a channel, bot has post permissions, user is admin/creator, and plan channel quota allows it. |
| `FEAT-ADM-03` | Channel Ownership Conflict Resolution: First-Registrant Recovery & 10-Digit One-Time Transfer Token (`ch_tokens`) | `IMPLEMENTED_AND_VERIFIED` | `domain.channels.ownership` | Tests token TTL, attempt cap, one-time consumption, active-publish lock, audit log, and old-owner/superadmin alerts. |
| `FEAT-ADM-04` | Manual Channel Test Cycle (`a:test:<channel_id>`) | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.channel_test` | Executes bounded discovery/AI cycle, enforces daily test quota, and returns stage-level diagnostics. |
| `FEAT-ADM-05` | Channel Activity & Diagnostic Report (`a:rep:<channel_id>`) | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.reports` | Displays recent publications, queue depth, source health, and remaining daily quotas. |
| `FEAT-ADM-06` | Source CRUD, Pagination, Fetch vs. Publish Toggles & Manual Recheck Diagnostics | `IMPLEMENTED_AND_VERIFIED` | `domain.sources` / `app.workflows.discovery` | Tests adding URL, pagination, independent `fetch_enabled` & `publish_enabled` switches, and diagnostic probe. |
| `FEAT-ADM-07` | Source Custom JSON API Endpoint & Encrypted API Key Configuration | `IMPLEMENTED_AND_VERIFIED` | `domain.sources` / `infra.crypto` | Verifies recursive JSON article detection, Fernet encryption at rest, key clearing, and zero key leakage in logs/UI. |
| `FEAT-ADM-08` | Per-Channel Editorial Configuration (Topic, Output Lang, Prompt, Signature, Min Score, Max Words, Max Caption, Switches) | `IMPLEMENTED_AND_VERIFIED` | `domain.channels.settings` | Verifies per-channel isolation and preservation of custom values across UI language switches. |
| `FEAT-ADM-09` | Category Collection Management (Name, Emoji, Style Instruction, 15/page Pagination) | `IMPLEMENTED_AND_VERIFIED` | `domain.channels.categories` | CRUD + pagination tests and prompt injection of category style instructions. |
| `FEAT-ADM-10` | Evaluation Criteria Collection Management (Name, Numeric Weight, 15/page Pagination) | `IMPLEMENTED_AND_VERIFIED` | `domain.channels.criteria` | CRUD + pagination tests and weighted score threshold calculation tests. |
| `FEAT-ADM-11` | Scheduling, Quiet Hours, IANA Timezone (DST-Safe), Lookback, Undated Policy & Auto/Manual Mode | `IMPLEMENTED_AND_VERIFIED` | `domain.scheduling` | Tests quiet-hour boundaries across DST transitions, daily cap enforcement (<= plan limit), and mode toggle. |
| `FEAT-ADM-12` | Ready Queue & Rejected Queue Inspection, Sorting (Time/Score), Inline Post Edit, Manual Publish, Restore & Queue Reset | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.queue` | Tests distinct state badges (retryable AI error vs. editorial rejection vs. delivery-unknown), manual publish, and confirmed reset. |
| `FEAT-ADM-13` | Personal AI Model Management (Base URL, Encrypted Key/Unauthenticated Mode, Model ID, Temp, Max Tokens, Probe) | `IMPLEMENTED_AND_VERIFIED` | `domain.ai.models` | Tests owner-only visibility, live health probe, precedence over/fallback to global models, and secret masking. |
| `FEAT-ADM-14` | Channel Deletion with Destructive Confirmation & Historical Data Semantics | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.channels` | Requires explicit confirmation; cleans up active schedules while preserving financial/audit integrity. |

---

## 3. Content Engine, Domain-Adaptive AI, Media & Publishing (Section 7 + Operator Directives)

| Feature ID | Capability | Baseline Status | Target `OpenGenerate` Module & Contract | Verification Requirement |
|---|---|---|---|---|
| `FEAT-ENG-01` | 5-Strategy Discovery Engine (RSS/Atom, JSON Feed, XML/News Sitemap, HTML Heuristics, Custom JSON API) | `IMPLEMENTED_AND_VERIFIED` | `infra.discovery` | Integration tests for all 5 source formats + ETag / `Last-Modified` conditional fetching and cooldown backoff. |
| `FEAT-ENG-02` | Full-Text Multi-Page Extraction & Feed Reconciliation | `IMPLEMENTED_AND_VERIFIED` | `infra.extraction` | Tests multi-page pagination bounds, canonical URL normalization, date parsing, and merging feed vs. page body. |
| `FEAT-ENG-03` | URL/Content Fingerprint Deduplication & Local Heuristic Ad Screening | `IMPLEMENTED_AND_VERIFIED` | `domain.content.screening` | Prevents duplicate processing/publishing and filters obvious promotional spam prior to AI token spend. |
| `FEAT-ENG-04` | Multi-Provider AI Orchestration (OpenAI, Anthropic, Gemini Compatible) with Circuit Breakers & Fallback | `IMPLEMENTED_AND_VERIFIED` | `infra.ai.orchestrator` | Tests priority routing, transient vs. permanent failure classification, quarantine, jittered backoff, and budget limits. |
| `FEAT-ENG-05` | Content + Prompt Fingerprint AI Caching & Long-Article Chunking | `IMPLEMENTED_AND_VERIFIED` | `domain.ai.cache` / `domain.ai.chunker` | Verifies cache hits, automatic invalidation on prompt/settings change, and honest chunked summarization. |
| `FEAT-ENG-06` | **Domain-Adaptive Scientific & Editorial Structuring (Operator Requirement)** | `IMPLEMENTED_AND_VERIFIED` | `domain.content.scientific_adapter` | Classifies scientific/editorial discipline and formats channel post appropriately (`tabular`, `itemized`, `sequential`, `statistical`, `analytical`, `narrative`). |
| `FEAT-ENG-07` | **Full Rich Message & Markdown Continuation in Bot Deep Links (Operator Requirement)** | `IMPLEMENTED_AND_VERIFIED` | `domain.content.rich_format` / `ui.handlers.deeplink` | Supports full Rich Message & Markdown vocabulary (tables, expandable quotes, collapsible details, code blocks, lists) for extended articles in bot. |
| `FEAT-ENG-08` | Native Telegram `sendRichMessage` with Graceful Legacy HTML/Text Fallback (`RICH_MESSAGES=auto\|on\|off`) | `UNKNOWN_REQUIRES_INVESTIGATION` | `infra.telegram.rich_send` | Probed dynamically at runtime; guarantees zero loss of tables, quotes, links, or text when downgrading to HTML/text. |
| `FEAT-ENG-09` | Source Media Extraction, Logo/Pixel Filtering, Secondary Image Fallback & Safe Upload | `IMPLEMENTED_AND_VERIFIED` | `infra.media.pipeline` | Extracts valid OG/JSON-LD/article media, rejects tiny/tracking pixels, falls back to alternate images or text-only post. |
| `FEAT-ENG-10` | Optional AI Image Generation Capability Contract | `DOCUMENTED_BUT_NOT_IMPLEMENTED` | `infra.media.ai_illustration` (Optional/Gated) | Explicitly documented as not present in baseline; architected as a clean, opt-in, disabled-by-default extension hook. |
| `FEAT-ENG-11` | Crash-Safe Publication Journal (`pub_journal`) & Ambiguous Delivery Protection | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.publication` | Tests pre-send write-ahead journaling, crash before local settlement, `delivery_unknown` quarantine (no blind resend), and 429 `retry_after`. |

---

## 4. Superadmin Control Plane (Section 8)

| Feature ID | Capability | Baseline Status | Target `OpenGenerate` Module & Contract | Verification Requirement |
|---|---|---|---|---|
| `FEAT-SUP-01` | Platform Report & 3-State System Health Dashboard (`ok`, `degraded`, `unknown`) | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.health` / `ui.views.superadmin` | Surfaces all 16 operational metrics (heartbeat age, queue depth, journals, unnotified payments, dead letters, AI health, DB integrity, RSS memory). |
| `FEAT-SUP-02` | Plan Lifecycle Management (Create, Edit Bilingual Fields/Tariffs, Toggle, Free Plan, Safe Deactivation/Delete) | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.superadmin_plans` | Enforces deactivation instead of physical deletion when users or payments reference a plan. |
| `FEAT-SUP-03` | Discount Code Management & Atomic Cap Enforcement | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.superadmin_discounts` | Concurrency tests verifying `used_count` never exceeds `max_uses`. |
| `FEAT-SUP-04` | User & Admin Management (Search, Paginate, Inspect, Assign/Revoke Plan, Ban/Unban, Direct Message, Reset Counters) | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.superadmin_users` | Enforces audit logging and prevents accidental self/peer superadmin lockout. |
| `FEAT-SUP-05` | Global AI Model Management (CRUD, Priority, Default Test Model, Health Probe) | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.superadmin_models` | Verifies secret redaction, priority routing, and live probe diagnostics. |
| `FEAT-SUP-06` | Atomic Payment Approval, Rejection & Audited Refund Engine | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.payments` | Tests double-click prevention, live quote revalidation, wallet ledgering, referral reward cap, and idempotent refund policy. |
| `FEAT-SUP-07` | Mandatory Membership Configuration (`fa` & `en` Independent Toggle and Channel ID/Username) | `IMPLEMENTED_AND_VERIFIED` | `domain.membership` | Server-side enforcement and configuration tests. |
| `FEAT-SUP-08` | Bilingual System Text / Localization Editor with HTML Validation & Reset-to-Default | `IMPLEMENTED_AND_VERIFIED` | `domain.i18n.overrides` | Rejects malformed HTML tags and supports instant reset to built-in defaults. |
| `FEAT-SUP-09` | Non-Blocking Rate-Limited Broadcast Messaging with Preview, Progress & Audit | `IMPLEMENTED_AND_VERIFIED` | `app.workflows.broadcast` | Runs asynchronously via background batch worker without blocking bot event loop. |
| `FEAT-SUP-10` | Global Automation Switch (With Confirmation) & Referral Economics Configuration | `IMPLEMENTED_AND_VERIFIED` | `domain.settings` | Enforces 0–50% referral bound, per-currency cap tracking, and confirmed global pause/resume. |

---

## 5. Legacy & Experimental Dispositions

| Item | Classification | Disposition in `OpenGenerate` |
|---|---|---|
| Cloudflare D1 Database Adapter | `PRESENT_BUT_UNSUPPORTED_OR_EXPERIMENTAL` | Retired. Replaced by PostgreSQL 16 (ADR-0003); legacy SQLite is only the ETL source format. |
| Unknown-Currency Wallet Balances (`legacy`) | `LEGACY_OR_DEPRECATED` | Preserved in `wallet` with `currency='LEGACY'` as non-spendable until explicitly reconciled by superadmin migration tooling. |
