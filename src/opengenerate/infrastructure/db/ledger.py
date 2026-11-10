"""Append-only ledger posting with row-locked wallets and idempotency keys."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.domain.errors import (
    CurrencyNotSpendable,
    IdempotencyConflict,
    InsufficientFunds,
    ValidationFailed,
)
from opengenerate.domain.money import LEGACY_ONLY_REASONS, Currency, LedgerReason


@dataclass(frozen=True, slots=True)
class LedgerResult:
    entry_id: int
    balance_after: int
    replayed: bool


async def post_ledger_entry(
    conn: AsyncConnection,
    *,
    user_id: int,
    currency: Currency,
    delta: int,
    reason: LedgerReason,
    idempotency_key: str,
    reference: str | None = None,
    actor_id: int | None = None,
) -> LedgerResult:
    """Apply `delta` to a user's wallet and append the matching ledger row, atomically.

    Must run inside the caller's transaction. Replaying the same key with identical parameters
    returns the original result; different parameters raise `IdempotencyConflict`.
    """
    if delta == 0:
        raise ValidationFailed("ledger delta must be non-zero")
    if not idempotency_key:
        raise ValidationFailed("idempotency key is required")
    if currency is Currency.LEGACY and reason not in LEGACY_ONLY_REASONS:
        raise CurrencyNotSpendable("legacy balances are frozen")

    await conn.execute(
        text(
            "INSERT INTO wallet_balances(user_id, currency, balance) VALUES (:u, :c, 0) "
            "ON CONFLICT (user_id, currency) DO NOTHING"
        ),
        {"u": user_id, "c": currency.value},
    )
    # The row lock serialises concurrent postings for this wallet; later statements then see
    # everything committed by earlier holders (READ COMMITTED).
    balance = (
        await conn.execute(
            text(
                "SELECT balance FROM wallet_balances WHERE user_id = :u AND currency = :c FOR UPDATE"
            ),
            {"u": user_id, "c": currency.value},
        )
    ).scalar_one()

    existing = (
        await conn.execute(
            text(
                "SELECT entry_id, user_id, currency, delta, reason, balance_after "
                "FROM ledger_entries WHERE idempotency_key = :k"
            ),
            {"k": idempotency_key},
        )
    ).first()
    if existing is not None:
        if (existing.user_id, existing.currency, existing.delta, existing.reason) != (
            user_id,
            currency.value,
            delta,
            reason.value,
        ):
            raise IdempotencyConflict("idempotency key reused with different parameters")
        return LedgerResult(existing.entry_id, existing.balance_after, replayed=True)

    new_balance = balance + delta
    if new_balance < 0:
        raise InsufficientFunds("wallet balance would become negative")

    await conn.execute(
        text(
            "UPDATE wallet_balances SET balance = :b, updated_at = now() "
            "WHERE user_id = :u AND currency = :c"
        ),
        {"b": new_balance, "u": user_id, "c": currency.value},
    )
    try:
        entry_id = (
            await conn.execute(
                text(
                    "INSERT INTO ledger_entries(idempotency_key, user_id, currency, delta, "
                    "balance_after, reason, reference, actor_id) "
                    "VALUES (:k, :u, :c, :d, :ba, :r, :ref, :actor) RETURNING entry_id"
                ),
                {
                    "k": idempotency_key,
                    "u": user_id,
                    "c": currency.value,
                    "d": delta,
                    "ba": new_balance,
                    "r": reason.value,
                    "ref": reference,
                    "actor": actor_id,
                },
            )
        ).scalar_one()
    except IntegrityError as exc:  # same key used for a different wallet concurrently
        raise IdempotencyConflict("idempotency key already used") from exc
    return LedgerResult(entry_id, new_balance, replayed=False)


async def get_balance(conn: AsyncConnection, user_id: int, currency: Currency) -> int:
    """Return the current balance of `(user_id, currency)` in minor units (0 if no wallet row exists)."""
    row = (
        await conn.execute(
            text("SELECT balance FROM wallet_balances WHERE user_id = :u AND currency = :c"),
            {"u": user_id, "c": currency.value},
        )
    ).first()
    if row is None:
        return 0
    return int(row._mapping["balance"])


@dataclass(frozen=True, slots=True)
class WalletMismatch:
    user_id: int
    currency: str
    balance: int
    ledger_sum: int


async def reconcile_wallets(conn: AsyncConnection) -> list[WalletMismatch]:
    """Return every (user, currency) whose wallet balance differs from the ledger sum."""
    rows = await conn.execute(
        text(
            "SELECT COALESCE(w.user_id, l.user_id) AS user_id, "
            "       COALESCE(w.currency, l.currency) AS currency, "
            "       COALESCE(w.balance, 0) AS balance, COALESCE(l.s, 0) AS ledger_sum "
            "FROM wallet_balances w "
            "FULL OUTER JOIN (SELECT user_id, currency, SUM(delta)::bigint AS s "
            "                 FROM ledger_entries GROUP BY user_id, currency) l "
            "  ON l.user_id = w.user_id AND l.currency = w.currency "
            "WHERE COALESCE(w.balance, 0) <> COALESCE(l.s, 0)"
        )
    )
    return [WalletMismatch(r.user_id, r.currency, r.balance, r.ledger_sum) for r in rows]
