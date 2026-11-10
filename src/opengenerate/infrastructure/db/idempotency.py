"""Generic exactly-once effect wrapper keyed by (scope, key)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.domain.errors import IdempotencyConflict


async def run_once(
    conn: AsyncConnection,
    scope: str,
    key: str,
    request_hash: str,
    effect: Callable[[], Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], bool]:
    """Run `effect` at most once per (scope, key); return (response, replayed).

    The idempotency row and the effect commit or roll back together. A concurrent caller with the
    same key blocks on the unique index until the first transaction finishes, then replays.
    """
    inserted = (
        await conn.execute(
            text(
                "INSERT INTO idempotency_keys(scope, key, request_hash) VALUES (:s, :k, :h) "
                "ON CONFLICT (scope, key) DO NOTHING RETURNING 1"
            ),
            {"s": scope, "k": key, "h": request_hash},
        )
    ).first()
    if inserted is not None:
        response = await effect()
        await conn.execute(
            text(
                "UPDATE idempotency_keys SET response = CAST(:r AS jsonb) WHERE scope = :s AND key = :k"
            ),
            {"r": json.dumps(response), "s": scope, "k": key},
        )
        return response, False

    row = (
        await conn.execute(
            text("SELECT request_hash, response FROM idempotency_keys WHERE scope = :s AND key = :k"),
            {"s": scope, "k": key},
        )
    ).one()
    if row.request_hash != request_hash:
        raise IdempotencyConflict("idempotency key reused with different parameters")
    stored = row.response
    return (json.loads(stored) if isinstance(stored, str) else stored), True
