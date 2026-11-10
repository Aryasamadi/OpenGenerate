"""Transactional outbox: durable, retried, dead-lettered delivery of important messages."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.application.ports import MessageSender
from opengenerate.domain.errors import PermanentError, TransientError, UnknownOutcomeError
from opengenerate.domain.retry import full_jitter_delay
from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.logging import get_logger, redact_text

log = get_logger("outbox")


async def enqueue(
    conn: AsyncConnection,
    *,
    idempotency_key: str,
    recipient_id: int,
    kind: str,
    payload: Mapping[str, Any],
    max_attempts: int = 8,
) -> bool:
    """Insert in the caller's transaction. Returns False if the key already existed."""
    row = (
        await conn.execute(
            text(
                "INSERT INTO outbox(idempotency_key, recipient_id, kind, payload, max_attempts) "
                "VALUES (:k, :r, :kind, CAST(:p AS jsonb), :m) "
                "ON CONFLICT (idempotency_key) DO NOTHING RETURNING id"
            ),
            {
                "k": idempotency_key,
                "r": recipient_id,
                "kind": kind,
                "p": json.dumps(dict(payload)),
                "m": max_attempts,
            },
        )
    ).first()
    return row is not None


@dataclass(frozen=True, slots=True)
class OutboxStats:
    pending: int
    sending: int
    dead_letter: int


async def stats(conn: AsyncConnection) -> OutboxStats:
    rows = await conn.execute(text("SELECT status, count(*) AS n FROM outbox GROUP BY status"))
    counts = {r.status: int(r.n) for r in rows}
    return OutboxStats(counts.get("pending", 0), counts.get("sending", 0), counts.get("dead_letter", 0))


class OutboxDispatcher:
    """At-least-once delivery: an ambiguous send is retried (duplicate beats loss for notices)."""

    def __init__(
        self,
        db: Database,
        sender: MessageSender,
        *,
        batch_size: int = 20,
        concurrency: int = 5,
        lease_seconds: float = 60.0,
        backoff_base: float = 5.0,
        backoff_cap: float = 900.0,
    ) -> None:
        self._db = db
        self._sender = sender
        self._batch = batch_size
        self._sem = asyncio.Semaphore(concurrency)
        self._lease = lease_seconds
        self._base = backoff_base
        self._cap = backoff_cap

    async def run_once(self) -> int:
        async with self._db.transaction() as conn:
            await conn.execute(
                text(
                    "UPDATE outbox SET status = 'pending' WHERE status = 'sending' AND lease_until < now()"
                )
            )
            rows = (
                await conn.execute(
                    text(
                        "WITH picked AS (SELECT id FROM outbox WHERE status = 'pending' "
                        "AND next_retry_at <= now() ORDER BY next_retry_at, id "
                        "FOR UPDATE SKIP LOCKED LIMIT :n) "
                        "UPDATE outbox o SET status = 'sending', attempts = o.attempts + 1, "
                        "lease_until = now() + make_interval(secs => CAST(:l AS double precision)) "
                        "FROM picked WHERE o.id = picked.id "
                        "RETURNING o.id, o.recipient_id, o.kind, o.payload, o.attempts, o.max_attempts"
                    ),
                    {"n": self._batch, "l": self._lease},
                )
            ).all()
        await asyncio.gather(*(self._deliver(r) for r in rows))
        return len(rows)

    async def _deliver(self, row: Any) -> None:
        payload = json.loads(row.payload) if isinstance(row.payload, str) else row.payload
        async with self._sem:
            error: str | None = None
            retry_after: float | None = None
            permanent = False
            try:
                await self._sender.send(row.recipient_id, row.kind, payload)
            except TransientError as exc:
                error, retry_after = f"transient: {exc}", exc.retry_after
            except UnknownOutcomeError as exc:
                error = f"unknown outcome: {exc}"
            except PermanentError as exc:
                error, permanent = f"permanent: {exc}", True
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        async with self._db.transaction() as conn:
            if error is None:
                await conn.execute(
                    text(
                        "UPDATE outbox SET status = 'sent', sent_at = now(), lease_until = NULL, "
                        "last_error = NULL WHERE id = :id"
                    ),
                    {"id": row.id},
                )
                return
            dead = permanent or row.attempts >= row.max_attempts
            delay = (
                retry_after
                if retry_after is not None
                else full_jitter_delay(row.attempts, self._base, self._cap)
            )
            await conn.execute(
                text(
                    "UPDATE outbox SET status = :s, last_error = :e, lease_until = NULL, "
                    "next_retry_at = now() + make_interval(secs => CAST(:d AS double precision)) "
                    "WHERE id = :id"
                ),
                {
                    "s": "dead_letter" if dead else "pending",
                    "e": redact_text(error)[:2000],
                    "d": delay,
                    "id": row.id,
                },
            )
            if dead:
                log.error("outbox_dead_letter", outbox_id=row.id, error=redact_text(error))
