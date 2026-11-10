"""Durable job queue on PostgreSQL (`FOR UPDATE SKIP LOCKED`, leases, jittered backoff)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.domain.errors import PermanentError, TransientError, UnknownOutcomeError
from opengenerate.domain.retry import full_jitter_delay
from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.logging import get_logger, redact_text

log = get_logger("jobs")


class LeaseLost(Exception):
    """The job's lease expired and another worker may own it."""


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    correlation_id: str | None


def _payload(value: Any) -> dict[str, Any]:
    loaded = json.loads(value) if isinstance(value, str) else value
    return dict(loaded or {})


async def enqueue(
    conn: AsyncConnection,
    kind: str,
    payload: Mapping[str, Any] | None = None,
    *,
    delay_seconds: float = 0.0,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
    correlation_id: str | None = None,
) -> int | None:
    """Insert a job; returns its id, or None if an active job with `dedupe_key` already exists."""
    row = (
        await conn.execute(
            text(
                "INSERT INTO jobs(kind, payload, run_at, dedupe_key, max_attempts, correlation_id) "
                "VALUES (:kind, CAST(:p AS jsonb), now() + make_interval(secs => CAST(:d AS double precision)), "
                ":dk, :m, :cid) ON CONFLICT DO NOTHING RETURNING id"
            ),
            {
                "kind": kind,
                "p": json.dumps(payload or {}),
                "d": delay_seconds,
                "dk": dedupe_key,
                "m": max_attempts,
                "cid": correlation_id,
            },
        )
    ).first()
    return None if row is None else int(row.id)


async def claim(
    conn: AsyncConnection, kinds: list[str], *, limit: int, lease_seconds: float, worker_id: str
) -> list[Job]:
    rows = await conn.execute(
        text(
            "WITH picked AS ("
            "  SELECT id FROM jobs WHERE status = 'pending' AND run_at <= now() "
            "  AND kind = ANY(CAST(:kinds AS text[])) ORDER BY run_at, id "
            "  FOR UPDATE SKIP LOCKED LIMIT :n) "
            "UPDATE jobs j SET status = 'running', attempts = j.attempts + 1, "
            "  lease_until = now() + make_interval(secs => CAST(:lease AS double precision)), "
            "  locked_by = :w, updated_at = now() "
            "FROM picked WHERE j.id = picked.id "
            "RETURNING j.id, j.kind, j.payload, j.attempts, j.max_attempts, j.correlation_id"
        ),
        {"kinds": kinds, "n": limit, "lease": lease_seconds, "w": worker_id},
    )
    return [
        Job(r.id, r.kind, _payload(r.payload), r.attempts, r.max_attempts, r.correlation_id)
        for r in rows
    ]


async def extend_lease(
    conn: AsyncConnection, job_id: int, worker_id: str, lease_seconds: float
) -> None:
    result = await conn.execute(
        text(
            "UPDATE jobs SET lease_until = now() + make_interval(secs => CAST(:l AS double precision)) "
            "WHERE id = :id AND status = 'running' AND locked_by = :w"
        ),
        {"l": lease_seconds, "id": job_id, "w": worker_id},
    )
    if result.rowcount != 1:
        raise LeaseLost(f"job {job_id}")


async def complete(conn: AsyncConnection, job_id: int, worker_id: str) -> None:
    result = await conn.execute(
        text(
            "UPDATE jobs SET status = 'done', lease_until = NULL, last_error = NULL, updated_at = now() "
            "WHERE id = :id AND status = 'running' AND locked_by = :w"
        ),
        {"id": job_id, "w": worker_id},
    )
    if result.rowcount != 1:
        raise LeaseLost(f"job {job_id}")


async def fail(
    conn: AsyncConnection,
    job: Job,
    worker_id: str,
    error: str,
    *,
    retry: bool,
    delay_seconds: float = 0.0,
) -> str:
    """Record a failure. Returns the resulting status ('pending' for retry, else 'failed')."""
    give_up = (not retry) or job.attempts >= job.max_attempts
    status = "failed" if give_up else "pending"
    result = await conn.execute(
        text(
            "UPDATE jobs SET status = :s, lease_until = NULL, locked_by = NULL, last_error = :e, "
            "run_at = now() + make_interval(secs => CAST(:d AS double precision)), updated_at = now() "
            "WHERE id = :id AND status = 'running' AND locked_by = :w"
        ),
        {"s": status, "e": redact_text(error)[:2000], "d": delay_seconds, "id": job.id, "w": worker_id},
    )
    if result.rowcount != 1:
        raise LeaseLost(f"job {job.id}")
    return status


async def release(conn: AsyncConnection, job_id: int, worker_id: str) -> None:
    """Return an unfinished job to the queue without consuming an attempt (graceful shutdown)."""
    await conn.execute(
        text(
            "UPDATE jobs SET status = 'pending', attempts = GREATEST(attempts - 1, 0), "
            "lease_until = NULL, locked_by = NULL, updated_at = now() "
            "WHERE id = :id AND status = 'running' AND locked_by = :w"
        ),
        {"id": job_id, "w": worker_id},
    )


async def reclaim_expired(conn: AsyncConnection) -> int:
    """Requeue (or fail, if out of attempts) jobs whose worker vanished."""
    result = await conn.execute(
        text(
            "UPDATE jobs SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'pending' END, "
            "lease_until = NULL, locked_by = NULL, last_error = 'lease expired', updated_at = now() "
            "WHERE status = 'running' AND lease_until < now()"
        )
    )
    return int(result.rowcount)


Handler = Callable[[Job], Awaitable[None]]


class JobRunner:
    def __init__(
        self,
        db: Database,
        handlers: Mapping[str, Handler],
        *,
        worker_id: str | None = None,
        concurrency: int = 4,
        lease_seconds: float = 60.0,
        poll_interval: float = 0.5,
        backoff_base: float = 2.0,
        backoff_cap: float = 300.0,
    ) -> None:
        self._db = db
        self._handlers = dict(handlers)
        self.worker_id = worker_id or f"w-{uuid.uuid4().hex[:8]}"
        self._concurrency = concurrency
        self._lease = lease_seconds
        self._poll = poll_interval
        self._base = backoff_base
        self._cap = backoff_cap
        self._stopping = asyncio.Event()
        self._inflight: dict[int, asyncio.Task[None]] = {}
        self._jobs: dict[int, Job] = {}

    @property
    def inflight(self) -> int:
        return len(self._inflight)

    def stop(self) -> None:
        self._stopping.set()

    async def run(self, grace_seconds: float = 30.0) -> None:
        kinds = list(self._handlers)
        while not self._stopping.is_set():
            free = self._concurrency - len(self._inflight)
            if free > 0 and kinds:
                async with self._db.transaction() as conn:
                    await reclaim_expired(conn)
                    claimed = await claim(
                        conn, kinds, limit=free, lease_seconds=self._lease, worker_id=self.worker_id
                    )
                for job in claimed:
                    self._jobs[job.id] = job
                    task = asyncio.create_task(self._execute(job), name=f"job-{job.id}")
                    self._inflight[job.id] = task

                    def _on_done(_t: asyncio.Task[None], jid: int = job.id) -> None:
                        self._inflight.pop(jid, None)

                    task.add_done_callback(_on_done)
                if claimed:
                    continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=self._poll)
        await self._drain(grace_seconds)

    async def _drain(self, grace_seconds: float) -> None:
        tasks = list(self._inflight.values())
        if not tasks:
            return
        _done, pending = await asyncio.wait(tasks, timeout=grace_seconds)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def _heartbeat(self, job_id: int) -> None:
        while True:
            await asyncio.sleep(self._lease / 3)
            async with self._db.transaction() as conn:
                await extend_lease(conn, job_id, self.worker_id, self._lease)

    async def _execute(self, job: Job) -> None:
        handler = self._handlers[job.kind]
        beat = asyncio.create_task(self._heartbeat(job.id))
        try:
            await handler(job)
            outcome: tuple[bool, float, str] | None = None
        except asyncio.CancelledError:
            beat.cancel()
            async with self._db.transaction() as conn:
                await release(conn, job.id, self.worker_id)
            raise
        except TransientError as exc:
            delay = exc.retry_after if exc.retry_after is not None else self._delay(job)
            outcome = (True, delay, f"transient: {exc}")
        except UnknownOutcomeError as exc:
            outcome = (False, 0.0, f"unknown outcome (not retried): {exc}")
        except PermanentError as exc:
            outcome = (False, 0.0, f"permanent: {exc}")
        except Exception as exc:  # unexpected handler bug: bounded retry
            outcome = (True, self._delay(job), f"{type(exc).__name__}: {exc}")
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
        try:
            async with self._db.transaction() as conn:
                if outcome is None:
                    await complete(conn, job.id, self.worker_id)
                else:
                    retry, delay, message = outcome
                    status = await fail(conn, job, self.worker_id, message, retry=retry, delay_seconds=delay)
                    log.warning("job_failed", job_id=job.id, kind=job.kind, status=status, error=message)
        except LeaseLost:
            log.error("job_lease_lost", job_id=job.id, kind=job.kind)

    def _delay(self, job: Job) -> float:
        return full_jitter_delay(job.attempts, self._base, self._cap)
