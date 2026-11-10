# Architectural, Financial, Security & Operational Risk Register (`Stage A`)

This register catalogs the critical failure modes, concurrency hazards, security threats, and operational risks identified in the baseline specification, paired with the mandatory mitigation controls enforced in `OpenGenerate`.

---

## 1. Financial, Ledger & Entitlement Risks

| Risk ID | Risk Description | Severity | Enforced Mitigation in `OpenGenerate` |
|---|---|---|---|
| `RISK-FIN-01` | **Concurrent Payment Double-Approval:** Two superadmins (or a double-click) approve the same `pay_requests` record simultaneously, granting two subscriptions, debiting/crediting wallets twice, or paying duplicate referral rewards. | **CRITICAL** | Atomic state claim (`UPDATE pay_requests SET status='processing', reviewer_id=:id WHERE payment_id=:pid AND status='pending'`) inside a `BEGIN IMMEDIATE` transaction, paired with unique `idempotency_key` constraints on `credit_log`, `plan_changes`, and referral reward settlements. |
| `RISK-FIN-02` | **Stale Quote / Post-Submission Balance Mutation:** A customer submits a payment receipt relying on wallet credit or a discount code, and before superadmin approval, the wallet balance is spent elsewhere, the plan tariff changes, or the discount expires/exhausts. | **HIGH** | Payment approval revalidates live wallet balance, discount validity/remaining cap, and quote invariants inside the approval transaction before committing. |
| `RISK-FIN-03` | **Cross-Currency Contamination (`TMN` vs `USD` vs `LEGACY`):** Mixing Persian Toman balances with USD balances, or allowing legacy un-denominated balances to be spent automatically. | **CRITICAL** | Composite primary key `(user_id, currency)` on `wallet` with explicit currency enum (`TMN`, `USD`, `LEGACY`), `CHECK(balance >= 0)`, zero automatic exchange rates, and hard rejection of `LEGACY` spending until superadmin reconciliation. |
| `RISK-FIN-04` | **Discount Cap Race Condition:** Multiple concurrent payment approvals use the final remaining slot of a limited discount code (`max_uses > 0`). | **HIGH** | Atomic conditional update `UPDATE discounts SET used_count = used_count + 1 WHERE code = :c AND is_active = 1 AND (max_uses = 0 OR used_count < max_uses)` checked within the approval transaction. |
| `RISK-FIN-05` | **Improvised / Duplicate Refund Reversals:** Refunding an already refunded payment, failing to handle referral reward clawback/cap accounting, or leaving active entitlements inconsistent. | **HIGH** | Deterministic, single-application refund transition (`status='approved' -> 'refunded'`) with documented entitlement revocation/proration policy, referral reward adjustment, and immutable `credit_log` + `plan_changes` audit rows. |
| `RISK-FIN-06` | **Referential Integrity Loss on Plan Deletion:** Deleting a plan that active subscribers or historical payment records still reference. | **MEDIUM** | Foreign key checks enforce soft deactivation (`is_active = 0`) whenever `users`, `pay_requests`, or `plan_changes` reference the target `plan_id`. |

---

## 2. Publication, Concurrency & Reliability Risks

| Risk ID | Risk Description | Severity | Enforced Mitigation in `OpenGenerate` |
|---|---|---|---|
| `RISK-PUB-01` | **Ambiguous Telegram Delivery & Blind Resend (Duplicate Channel Posts):** A network timeout occurs after the HTTP request to Telegram `sendMessage`/`sendPhoto`/`sendRichMessage` is transmitted; a naive retry publishes the same article twice to the customer's channel. | **CRITICAL** | Write-ahead `pub_journal` records `in_flight` before network transmission. If a post-send timeout or ambiguous transport error occurs, the journal transitions to `delivery_unknown` and quarantines the item for operator inspection instead of blindly resending. |
| `RISK-PUB-02` | **Process Crash Between Telegram Send and Local DB Settlement:** Telegram successfully posts the message, but the container restarts before `articles.status` and `posted` are updated. | **HIGH** | Startup and periodic recovery workers inspect `pub_journal` entries in `acknowledged` or `in_flight` states, reconcile `telegram_msg_id` into `posted` and `articles`, and settle reserved quotas idempotently. |
| `RISK-PUB-03` | **Head-of-Line Blocking in Scheduler:** One slow RSS source, multi-page scraper, or stalled AI provider blocks the entire scheduler loop and starves other channels. | **HIGH** | Bounded per-tick work queues, per-channel and global concurrency semaphores, strict per-request HTTP/AI deadlines, source cooldown backoff, and fair round-robin channel scheduling. |
| `RISK-PUB-04` | **Channel Ownership Transfer During Active Publication:** Ownership changes mid-cycle while a post is being published under the old owner's quota/settings. | **MEDIUM** | Channel transfer token consumption checks `pub_journal` for active `reserved`/`in_flight` entries on the target channel and serializes ownership transfer cleanly. |
| `RISK-PUB-05` | **Misclassification of Infrastructure Errors as Editorial Rejections:** AI provider 429/5xx timeouts or malformed JSON responses permanently mark a good article as editorially rejected. | **HIGH** | Separate `status` (`AI_RETRYABLE`, `AI_EXHAUSTED`) and `failure_class` from `REJECTED_LOW_SCORE` / `REJECTED_AI_AD`, surfacing distinct badges and retry actions in the admin queue UI. |

---

## 3. Content Quality, Domain Adaptation & Rich Formatting Risks

| Risk ID | Risk Description | Severity | Enforced Mitigation in `OpenGenerate` |
|---|---|---|---|
| `RISK-CNT-01` | **Loss of Scientific/Topical Structure in Channel Posts:** Flattening comparative benchmarks, clinical/scientific findings, or statistical reports into generic unstructured paragraphs. | **HIGH** | Domain-Adaptive Scientific & Editorial Structuring classifies each article's discipline and enforces the matching layout (`tabular`, `itemized`, `sequential`, `statistical`, `analytical`, or `narrative`) in both prompt construction and output schema validation. |
| `RISK-CNT-02` | **Content Loss During Rich Message -> Legacy HTML Fallback:** When `sendRichMessage` is unavailable or rejected, a naive fallback strips tables, blockquotes, collapsible details, or links. | **HIGH** | Lossless AST downgrade renderer converts Rich tables into aligned monospaced/bulleted grids, collapsible sections into `<tg-spoiler>` or titled sections, and preserves 100% of links, quotes, and body text in both channel posts and bot deep-link continuations. |
| `RISK-CNT-03` | **Stale AI Cache After Prompt/Settings Edit:** Serving cached AI output generated under old channel prompts, categories, criteria weights, or output language. | **MEDIUM** | `ai_cache` key incorporates a SHA-256 fingerprint of `(canonical_content_hash + channel_editorial_settings_version + output_lang + scientific_format_policy)`. |

---

## 4. Security & Multi-Tenant Abuse Risks

| Risk ID | Risk Description | Severity | Enforced Mitigation in `OpenGenerate` |
|---|---|---|---|
| `RISK-SEC-01` | **Server-Side Request Forgery (SSRF) via Source URLs, Custom JSON APIs, or Media Links:** An attacker supplies `http://127.0.0.1/...`, `http://169.254.169.254/...`, or an open redirect to probe internal VPS services or cloud metadata. | **CRITICAL** | Custom SSRF-safe network transport resolves DNS before connection, blocks loopback/private/link-local/multicast IPs, revalidates every redirect hop, and strips `Authorization`/custom API headers across host changes. Local AI endpoints require explicit operator allowlisting. |
| `RISK-SEC-02` | **Prompt Injection via Scraped Web Content:** Malicious instructions embedded in an article body attempt to override system prompts, fake high evaluation scores, or exfiltrate configuration. | **HIGH** | Strict system/data separation using XML/JSON structural delimiters, post-generation schema & score bounds verification, and output sanitization before rendering. |
| `RISK-SEC-03` | **IDOR / Cross-Tenant Callback Manipulation:** A user crafts callback data (e.g., `a:ch:<other_channel_id>` or `a:srcd:<other_source_id>`) or invokes `s:*` superadmin routes directly. | **CRITICAL** | Mandatory server-side authorization middleware verifying `role == 'superadmin'` on all `s:*` routes and `channel.owner_id == actor_id` on every channel/source/article/model operation. |
| `RISK-SEC-04` | **Plaintext API Key Exposure in DB, Logs, or UI Errors:** Source custom API keys or personal/global AI provider tokens leaked in stack traces, diagnostic views, or unencrypted database backups. | **CRITICAL** | Envelope/Fernet encryption at rest (`custom_api_key_enc`, `api_key_enc`), structured log redaction filters for `Bearer`, `sk-`, bot tokens, and query secrets, and masked UI display. |
| `RISK-SEC-05` | **Telegram Callback Data Overflow (>64 Bytes):** Dynamic callback payloads exceeding Telegram's 64-byte hard limit fail silently or crash keyboard construction. | **MEDIUM** | Compact prefix routing (`u:`, `a:`, `s:`, `c:`) using integer IDs or short lookup tokens, enforced by automated route-coverage tests checking byte length <= 64. |
