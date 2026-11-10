from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.conftest import make_user

from opengenerate.domain.errors import (
    CurrencyNotSpendable,
    IdempotencyConflict,
    InsufficientFunds,
    PermanentError,
    TransientError,
    UnknownOutcomeError,
)
from opengenerate.domain.money import Currency, LedgerReason
from opengenerate.infrastructure import jobs, outbox
from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.db.idempotency import run_once
from opengenerate.infrastructure.db.ledger import post_ledger_entry, reconcile_wallets
from opengenerate.infrastructure.leader import AdvisoryLockLeader
from opengenerate.infrastructure.ratelimit import RateLimiter, heartbeat_age_seconds, record_heartbeat
from opengenerate.migrations import current_revision, downgrade, head_revision, upgrade


async def test_all_25_domain_tables_and_migration_roundtrip(db_url: str) -> None:
    assert current_revision(db_url) == head_revision()
    downgrade(db_url, "base")
    assert current_revision(db_url) is None
    upgrade(db_url, "head")
    assert current_revision(db_url) == head_revision()

    database = Database(db_url)
    try:
        async with database.engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type IN ('BASE TABLE', 'VIEW')"
                )
            )
            present = {r[0] for r in rows}
    finally:
        await database.dispose()

    expected_25 = {
        "users", "plans", "discounts", "channels", "sources",
        "usage", "articles", "posted", "ch_tokens", "ai_models",
        "deeplinks", "kv_cache", "ai_cache", "ai_usage", "pub_journal",
        "settings", "support", "support_map", "plan_changes", "pay_requests",
        "notifications", "referrals", "wallet", "credit_log", "rate_limits",
    }
    assert expected_25.issubset(present)


async def test_ledger_post_idempotency_currency_isolation_and_db_triggers(db: Database) -> None:
    await make_user(db, 1001)

    async with db.transaction() as conn:
        r1 = await post_ledger_entry(
            conn,
            user_id=1001,
            currency=Currency.TMN,
            delta=500_000,
            reason=LedgerReason.PAYMENT_SURPLUS,
            idempotency_key="pay:1:surplus",
        )
        assert r1.balance_after == 500_000 and not r1.replayed

        # Replay with identical args is a no-op returning the original balance
        r2 = await post_ledger_entry(
            conn,
            user_id=1001,
            currency=Currency.TMN,
            delta=500_000,
            reason=LedgerReason.PAYMENT_SURPLUS,
            idempotency_key="pay:1:surplus",
        )
        assert r2.replayed and r2.balance_after == 500_000 and r2.entry_id == r1.entry_id

        # Separate USD wallet does not mix with TMN
        r_usd = await post_ledger_entry(
            conn,
            user_id=1001,
            currency=Currency.USD,
            delta=1500,
            reason=LedgerReason.REFERRAL_REWARD,
            idempotency_key="ref:1",
        )
        assert r_usd.balance_after == 1500

    # Reusing idempotency key with different delta raises IdempotencyConflict
    with pytest.raises(IdempotencyConflict):
        async with db.transaction() as conn:
            await post_ledger_entry(
                conn,
                user_id=1001,
                currency=Currency.TMN,
                delta=999_999,
                reason=LedgerReason.PAYMENT_SURPLUS,
                idempotency_key="pay:1:surplus",
            )

    # Overdraft rejected
    with pytest.raises(InsufficientFunds):
        async with db.transaction() as conn:
            await post_ledger_entry(
                conn,
                user_id=1001,
                currency=Currency.TMN,
                delta=-600_000,
                reason=LedgerReason.PAYMENT_DEBIT,
                idempotency_key="pay:2:debit",
            )

    # LEGACY currency can be migrated in, but never spent
    async with db.transaction() as conn:
        r_leg = await post_ledger_entry(
            conn,
            user_id=1001,
            currency=Currency.LEGACY,
            delta=250_000,
            reason=LedgerReason.MIGRATION,
            idempotency_key="mig:1001",
        )
        assert r_leg.balance_after == 250_000

    with pytest.raises(CurrencyNotSpendable):
        async with db.transaction() as conn:
            await post_ledger_entry(
                conn,
                user_id=1001,
                currency=Currency.LEGACY,
                delta=-10_000,
                reason=LedgerReason.PAYMENT_DEBIT,
                idempotency_key="leg:spend",
            )

    # Append-only trigger blocks UPDATE/DELETE on ledger_entries
    with pytest.raises(DBAPIError):
        async with db.transaction() as conn:
            await conn.execute(text("DELETE FROM ledger_entries WHERE user_id = 1001"))

    # Deferred constraint trigger blocks direct tampering of wallet_balances without matching ledger row
    with pytest.raises(DBAPIError):
        async with db.transaction() as conn:
            await conn.execute(
                text("UPDATE wallet_balances SET balance = 99999999 WHERE user_id = 1001 AND currency = 'TMN'")
            )

    async with db.engine.connect() as conn:
        assert await reconcile_wallets(conn) == []


async def test_concurrent_ledger_postings_preserve_exact_balance(db: Database) -> None:
    await make_user(db, 2002)

    async def credit(i: int) -> None:
        async with db.transaction() as conn:
            await post_ledger_entry(
                conn,
                user_id=2002,
                currency=Currency.TMN,
                delta=10_000,
                reason=LedgerReason.REFERRAL_REWARD,
                idempotency_key=f"conc:{i}",
            )

    await asyncio.gather(*(credit(i) for i in range(10)))
    async with db.engine.connect() as conn:
        bal = (
            await conn.execute(
                text("SELECT balance FROM wallet_balances WHERE user_id = 2002 AND currency = 'TMN'")
            )
        ).scalar_one()
        assert bal == 100_000
        assert await reconcile_wallets(conn) == []


async def test_run_once_idempotency_helper(db: Database) -> None:
    calls = 0

    async def side_effect() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"ok": True, "calls": calls}

    async with db.transaction() as conn:
        res1, rep1 = await run_once(conn, "payment_approve", "p-1", "hash-a", side_effect)
    async with db.transaction() as conn:
        res2, rep2 = await run_once(conn, "payment_approve", "p-1", "hash-a", side_effect)

    assert not rep1 and rep2
    assert res1 == res2 == {"ok": True, "calls": 1}
    assert calls == 1

    with pytest.raises(IdempotencyConflict):
        async with db.transaction() as conn:
            await run_once(conn, "payment_approve", "p-1", "hash-different", side_effect)


async def test_jobs_skip_locked_dedupe_and_error_classification(db: Database) -> None:
    async with db.transaction() as conn:
        j1 = await jobs.enqueue(conn, "work", {"n": 1}, dedupe_key="ch:1")
        j_dup = await jobs.enqueue(conn, "work", {"n": 1}, dedupe_key="ch:1")
        j2 = await jobs.enqueue(conn, "ambiguous", {"n": 2}, max_attempts=3)
    assert j1 is not None and j_dup is None and j2 is not None

    seen: list[int] = []

    async def handle_work(job: jobs.Job) -> None:
        seen.append(job.payload["n"])

    async def handle_ambiguous(_job: jobs.Job) -> None:
        raise UnknownOutcomeError("timeout after send")

    runner = jobs.JobRunner(
        db,
        {"work": handle_work, "ambiguous": handle_ambiguous},
        poll_interval=0.05,
    )
    task = asyncio.create_task(runner.run(grace_seconds=2.0))
    await asyncio.sleep(0.25)
    runner.stop()
    await task

    assert seen == [1]
    async with db.engine.connect() as conn:
        s1 = (await conn.execute(text("SELECT status FROM jobs WHERE id = :id"), {"id": j1})).scalar_one()
        s2, att2 = (
            await conn.execute(text("SELECT status, attempts FROM jobs WHERE id = :id"), {"id": j2})
        ).one()
    assert s1 == "done"
    # UnknownOutcomeError must fail immediately on attempt 1 without blind retry
    assert s2 == "failed" and att2 == 1


async def test_outbox_dispatcher_retries_transient_and_dead_letters_permanent(db: Database) -> None:
    async with db.transaction() as conn:
        await outbox.enqueue(
            conn, idempotency_key="ob:1", recipient_id=10, kind="text", payload={"text": "hi"}, max_attempts=2
        )
        await outbox.enqueue(
            conn, idempotency_key="ob:2", recipient_id=20, kind="text", payload={"text": "blocked"}, max_attempts=3
        )

    delivered: list[int] = []
    attempts_10 = 0

    class FakeSender:
        async def send(self, recipient_id: int, _kind: str, _payload: Any) -> None:
            nonlocal attempts_10
            if recipient_id == 10:
                attempts_10 += 1
                if attempts_10 == 1:
                    raise TransientError("429", retry_after=0.0)
                delivered.append(recipient_id)
                return
            raise PermanentError("bot was blocked by the user")

    disp = outbox.OutboxDispatcher(db, FakeSender(), backoff_base=0.01, backoff_cap=0.02)
    await disp.run_once()
    await asyncio.sleep(0.05)
    await disp.run_once()

    assert delivered == [10]
    async with db.engine.connect() as conn:
        st = await outbox.stats(conn)
    assert st.pending == 0 and st.dead_letter == 1


async def test_advisory_lock_leader_election_and_rate_limiter(db_url: str, db: Database) -> None:
    l1 = AdvisoryLockLeader(db_url, 987654)
    l2 = AdvisoryLockLeader(db_url, 987654)
    try:
        assert await l1.try_acquire() is True
        assert await l2.try_acquire() is False
        assert await l1.check() is True
        await l1.release()
        assert await l2.try_acquire() is True
    finally:
        await l1.release()
        await l2.release()

    limiter = RateLimiter(db)
    d1 = await limiter.hit("support:user:1", limit=2, window_seconds=60)
    d2 = await limiter.hit("support:user:1", limit=2, window_seconds=60)
    d3 = await limiter.hit("support:user:1", limit=2, window_seconds=60)
    assert d1.allowed and d2.allowed and not d3.allowed
    assert d3.retry_after > 0

    assert await heartbeat_age_seconds(db, "scheduler") is None
    await record_heartbeat(db, "scheduler", {"tick": 1})
    age = await heartbeat_age_seconds(db, "scheduler")
    assert age is not None and 0.0 <= age < 5.0
