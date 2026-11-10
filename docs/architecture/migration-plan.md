# Migration Plan (Stage B → executed in Phase 8)

Governed by [ADR-0013](decisions/0013-migration-strategy.md). Legacy names below are the **assumed** inventory from the Master Prompt; they must be confirmed by introspecting a real snapshot (OI-1) before any ETL run.

## 1. Legacy → target mapping (all 25 legacy tables)

| # | Legacy table | Target table(s) | Transform / rule | Disposition |
|---|---|---|---|---|
| 1 | `users` | `users` | ids preserved; role enum validated; `plan_id`/expiry carried; ban flag kept; `free_plan_used` kept | migrate |
| 2 | `plans` | `plans` | ids preserved; prices → minor units (`price_tmn` toman, `price_usd` cents); text-only/negotiated prices kept as text with `price_numeric=false` | migrate |
| 3 | `discounts` | `discounts`, `discount_redemptions` | `used_count` carried; redemptions backfilled only where payments reference the code | migrate |
| 4 | `channels` | `channels` | settings blob → versioned JSONB (`settings_version=1` upgrader); `original_owner` preserved as audit evidence | migrate |
| 5 | `sources` | `sources`, `source_fetch_state` | ETag/Last-Modified/cooldown to fetch-state; API key re-encrypted | migrate |
| 6 | `usage` | `usage_counters` | per-day counters kept for current period only (older → archived summary) | migrate (recent) |
| 7 | `articles` | `articles` | legacy status mapped to new state machine (table in §3); infrastructure failures **not** mapped to `rejected_*`; media/score/category kept; AST null (regenerated lazily) | migrate |
| 8 | `posted` | `published_items` | unique `(channel_id, article_hash)` re-validated; duplicates reported | migrate |
| 9 | `ch_tokens` | `channel_transfer_tokens` | legacy token format cannot be verified against the new HMAC scheme, so all outstanding tokens are expired at cutover and owners re-issue; consumed/expired rows kept as audit history | expire |
| 10 | `ai_models` | `ai_models` | ownership kept (global=NULL); keys decrypted with legacy key and re-encrypted; missing key → `needs_reentry`, model disabled | migrate |
| 11 | `deeplinks` | `deep_links` | keys preserved byte-for-byte so old links resolve; content re-linked to migrated articles | migrate |
| 12 | `kv_cache` | — | cache | discard |
| 13 | `ai_cache` | — | key scheme changes (fingerprint includes prompt version) | discard |
| 14 | `ai_usage` | `ai_usage` | totals kept for current period | migrate (recent) |
| 15 | `pub_journal` | `publication_journal` | only non-settled rows; `in_flight`/unknown → `delivery_unknown` (never auto-resend) | migrate |
| 16 | `settings` | `settings`, `text_overrides` | typed per key; text overrides re-validated for HTML; idempotency markers dropped | migrate |
| 17 | `support` | `support_sessions` | open sessions only | migrate |
| 18 | `support_map` | `support_routes` | kept so replies to old forwarded messages still route | migrate |
| 19 | `plan_changes` | `plan_changes` | kept as immutable history; idempotency keys synthesised `legacy:{id}` | migrate |
| 20 | `pay_requests` | `payments` | state mapping (§3); receipts keep Telegram `file_id`; `processing` rows need **operator review**, never auto-applied | migrate |
| 21 | `notifications` | `outbox` | `pending` kept; `sent` summarised; dead-letters kept for review | migrate |
| 22 | `referrals` | `referrals` | PK on referred user; reward totals recomputed **only to compare**, source totals kept | migrate |
| 23 | `wallet` | `wallet_balances` | per currency; unknown → `LEGACY` | migrate |
| 24 | `credit_log` | `ledger_entries` | 1:1 with `legacy:{id}` idempotency keys, `balance_after` verified; gaps reported | migrate |
| 25 | `rate_limits` | `rate_limits` | not migrated (windows are ephemeral) | discard |

## 2. Execution stages
1. **Inspect** — introspect snapshot schema, row counts, nulls/ranges, orphan FKs; diff against §1; write `inspect-report.md`.
2. **Plan** — resolve diffs, freeze mapping version.
3. **Rehearse** — full ETL into a disposable PostgreSQL (the validated `pgserver` fixture locally; service container in CI); run reconciliation; repeat until clean. Rehearsal also runs against **synthetic prior-schema snapshots** committed under `tests/fixtures/legacy_schemas/` (every supported prior schema version).
4. **Backup & freeze** — operator freezes legacy writes; verified backup of the legacy file (copy + checksum).
5. **Run** into the empty production PostgreSQL (refuses a non-empty target without `--allow-nonempty`, which is never used in production).
6. **Reconcile** — gates in §4 must all pass.
7. **Shadow start** — new stack with outbound sends disabled; operator checks health, a sample of channels/payments/links.
8. **Cutover** — enable sends; legacy left read-only for the agreed retention window.
9. **Rollback** — stop new stack, re-enable legacy; no data in legacy was modified; new DB kept for forensics.

## 3. State mappings (to be finalised after inspect)
- Payment: `pending→pending`, `processing→manual_review` (flagged), `approved→approved`, `rejected→rejected`, `refunded→refunded`; unknown values block the run.
- Article: legacy ready→`ready`; rejected→`rejected_*` by stored reason class; AI failure/retry/exhausted→`ai_retryable|ai_exhausted`; published→`published`; unknown→listed and held in `needs_review`.

## 4. Reconciliation gates (all must pass; a failure blocks cutover)
| Gate | Check |
|---|---|
| G1 | row counts per migrated table = source (± documented discards) |
| G2 | per-table checksum over stable columns |
| G3 | all FKs valid, all unique/idempotency constraints hold |
| G4 | `wallet_balances = SUM(ledger_entries)` per (user, currency); totals equal source wallet totals per currency |
| G5 | no negative balances; no spendable `LEGACY` |
| G6 | one owner per channel; one referral per referred user; one published row per (channel, article hash) |
| G7 | every pending/approved payment's plan still exists or is deactivated, not deleted |
| G8 | 100 % of legacy deep-link keys resolve; sampled article render check |
| G9 | encrypted fields decrypt with the new key ring (sample 100 %) or flagged `needs_reentry` |
| G10 | sampled field-level diff (configurable % + all payments, all wallets) |

## 5. Safety rules
Never run against production without: verified backup path, operator confirmation token, passing rehearsal on the same snapshot, shadow-mode plan. Never delete or modify the legacy data. All outputs go to `migration-report.md` with commit SHA and tool version.
