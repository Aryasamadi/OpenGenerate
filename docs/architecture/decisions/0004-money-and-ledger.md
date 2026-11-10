# ADR-0004 — Money, wallet and ledger model

**Status:** Accepted

## Context
Two currencies (Persian tariffs in toman, English tariffs in USD), frozen `legacy` balances of unknown currency, referral rewards with per-currency caps, discounts with use caps, refunds. Correctness is non-negotiable.

## Decision
1. **Integer minor units only**, `BIGINT`: `TMN` in toman (exponent 0), `USD` in cents (exponent 2). No floats/decimals in persistence or domain arithmetic. Percentages use integer basis points; rounding mode is explicit (`ROUND_HALF_UP` on the minor unit) and unit-tested.
2. `Currency` is a closed enum `{TMN, USD, LEGACY}`. `LEGACY` is read-only: the domain `Wallet.spend()` rejects it; a DB `CHECK` plus a trigger block ledger inserts with `reason` ≠ `migration`/`legacy_reconcile` for `LEGACY`.
3. **Append-only `ledger_entries`** (delta, currency, reason, idempotency key, reference). `wallet_balances` is a materialised balance updated **in the same transaction**; invariant `wallet.balance = SUM(ledger.delta)` is enforced by a deferred constraint trigger and verified by a reconciliation job + test. `CHECK (balance >= 0)`.
4. **Idempotency keys** (unique) on: ledger entry, plan change, referral reward (`referral:{payment_id}`), refund (`refund:{payment_id}`), discount consumption (`discount:{payment_id}`), notification.
5. **Payment state machine** `pending → processing → approved → (refunded)` and `pending → rejected`; transitions are guarded `UPDATE … WHERE status = :expected` inside one transaction (`READ COMMITTED` + row locks; `SERIALIZABLE` retry only where cross-row predicates require it). Stuck `processing` is recovered by a lease timestamp, never by re-applying money.
6. **Discount consumption**: single conditional `UPDATE … SET used = used + 1 WHERE (max_uses = 0 OR used < max_uses) AND active AND now() < expires_at RETURNING`; a zero-row result aborts the approval.
7. **Referral reward** computed from the approved payment's *numeric paid amount*, capped per `(referrer, currency)` using remaining-cap computed under a row lock on the referral aggregate; partial application when the cap is hit; reward ledger row keyed by payment id.
8. **Refund policy (documented, needs operator confirmation in Phase 4 — see open item OI-3):** a refund credits the *paid amount* to the customer's wallet in the original currency (ledger `refund_credit`); entitlement granted by that payment is revoked back to the prior plan state (or marked expired if it was the only entitlement); the referral reward for that payment is clawed back from the referrer's wallet only up to its current non-negative balance, remainder recorded as `reward_clawback_shortfall` for audit; a payment can be refunded once (idempotency key); partial refunds are **not** supported in v1.

## Alternatives rejected
`NUMERIC` decimals (invites mixed-unit bugs for a zero-decimal currency); balance-only wallet without ledger (violates audit/reconciliation); application-level locking only (not durable under multi-process).

## Consequences
Every money use case is a single DB transaction with an explicit test for double-click, crash-at-each-step, stale quote, cap exhaustion.

## Revisit trigger
Additional currencies or partial refunds.
