# ADR-0013 — Migration strategy (legacy SQLite → PostgreSQL)

**Status:** Accepted (execution is Phase 8; **no production data is touched in Phases 2–7**)

## Context
Operator instruction: the legacy repository is not a code source (known defects). The Master Prompt still requires safe migration of real customer/channel/payment/wallet/referral/deep-link data. The legacy schema contract is therefore taken from the Master Prompt table inventory (25 tables) and must be **confirmed against a real snapshot** — we have none yet (open item OI-1).

## Decision
1. **Source of truth for the legacy shape:** a *sanitised or full copy* of the legacy SQLite file provided by the operator (read-only; never the live file). Phase 8 begins with an automated schema introspection that diffs the real snapshot against the assumed inventory; differences are documented and the mapping adjusted before any ETL is run.
2. **Tooling:** `opengenerate-migrate-legacy` CLI: `inspect → plan → rehearse → run → reconcile → report`. `rehearse` runs the whole ETL into a throw-away PostgreSQL; `run` refuses to start without `--i-have-a-verified-backup <path>` and an explicit operator confirmation token.
3. **Order & mapping** (details in `migration-plan.md`): identity → plans → discounts → channels → sources → usage → articles/posted → ch_tokens(expired only) → ai_models (re-encrypt) → deeplinks → pay_requests → plan_changes → wallet/ledger → referrals → support_map → settings → rate_limits(discarded) → caches(discarded).
4. **IDs preserved** (Telegram ids, plan ids, payment ids, deep-link keys) so existing bot deep links keep working.
5. **Money:** ledger rows are migrated 1:1; balances are **never recomputed from guesses**. Amounts converted to minor units with an explicit table (toman×1, USD×100 if legacy stored decimals, else documented rule). Balances with unknown currency go to `LEGACY` with a `migration` ledger entry. Reconciliation asserts `wallet == SUM(ledger)` per (user, currency) and totals equal to the source; any mismatch blocks cutover.
6. **Secrets:** legacy ciphertext is decrypted with the legacy key (supplied once via env, never stored) and re-encrypted with the new key ring; if the legacy key is unavailable those keys are flagged `needs_reentry` and the model/source is disabled, never silently dropped.
7. **Gates:** row counts per table, per-table checksums on stable columns, FK/unique validation, business invariants (single owner, unique referral, no negative balances, one published row per channel/article), sampled field-level diff, deep-link resolution test.
8. **Cutover & rollback:** freeze legacy writes (operator action) → final snapshot → ETL → reconcile → start new stack in **shadow mode** (`SEND_ENABLED=false`: reads/processing but no outbound sends) → operator approves → enable sends. Rollback = stop new stack, re-enable legacy; legacy data is never modified.
9. **Retire columns by evidence:** each legacy column is classified live/legacy/migration-only/reserved in the mapping; dropped columns are listed in the migration report.

## Alternatives rejected
In-place conversion of the legacy file; big-bang cutover without shadow mode; recomputing balances.

## Consequences
Phase 8 cannot complete without a legacy snapshot; this is tracked as an operator dependency, not assumed away.
