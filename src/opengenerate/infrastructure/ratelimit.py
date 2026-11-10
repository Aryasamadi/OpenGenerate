"""Persisted fixed-window rate limiting and process heartbeats."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from opengenerate.infrastructure.db.engine import Database


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    count: int
    retry_after: float


class RateLimiter:
    """Atomic upsert counter. Runs in its own transaction so denied/failed attempts still count."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def hit(self, bucket_key: str, *, limit: int, window_seconds: float) -> RateDecision:
        async with self._db.transaction() as conn:
            row = (
                await conn.execute(
                    text(
                        "INSERT INTO rate_limits(bucket_key, window_start, count) VALUES (:k, now(), 1) "
                        "ON CONFLICT (bucket_key) DO UPDATE SET "
                        " count = CASE WHEN rate_limits.window_start <= now() - make_interval(secs => CAST(:w AS double precision)) "
                        "              THEN 1 ELSE rate_limits.count + 1 END, "
                        " window_start = CASE WHEN rate_limits.window_start <= now() - make_interval(secs => CAST(:w AS double precision)) "
                        "              THEN now() ELSE rate_limits.window_start END "
                        "RETURNING count, EXTRACT(EPOCH FROM (window_start + make_interval(secs => CAST(:w AS double precision)) - now())) AS retry_after"
                    ),
                    {"k": bucket_key, "w": window_seconds},
                )
            ).one()
        count_val = int(row._mapping["count"])
        retry_val = float(row._mapping["retry_after"])
        allowed = count_val <= limit
        return RateDecision(allowed, count_val, 0.0 if allowed else max(retry_val, 0.0))


async def record_heartbeat(db: Database, name: str, info: Mapping[str, Any] | None = None) -> None:
    async with db.transaction() as conn:
        await conn.execute(
            text(
                "INSERT INTO system_heartbeats(name, beat_at, info) VALUES (:n, now(), CAST(:i AS jsonb)) "
                "ON CONFLICT (name) DO UPDATE SET beat_at = now(), info = EXCLUDED.info"
            ),
            {"n": name, "i": json.dumps(dict(info or {}))},
        )


async def heartbeat_age_seconds(db: Database, name: str) -> float | None:
    """Seconds since the last heartbeat, or None when it was never recorded (unknown)."""
    async with db.engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT EXTRACT(EPOCH FROM (now() - beat_at)) AS age FROM system_heartbeats WHERE name = :n"),
                {"n": name},
            )
        ).first()
    return None if row is None else float(row.age)
