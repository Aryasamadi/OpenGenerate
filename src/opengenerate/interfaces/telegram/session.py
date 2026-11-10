"""Durable Guided-Wizard Session & Quote Draft State in PostgreSQL `kv_cache`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

SESSION_TTL = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class WizardSession:
    user_id: int
    action_id: str
    step: str
    parent_callback: str
    context: dict[str, Any]
    expires_at: datetime


def _session_key(user_id: int) -> str:
    return f"session:user:{user_id}"


def _quote_discount_key(user_id: int, plan_id: int) -> str:
    return f"quote_disc:user:{user_id}:plan:{plan_id}"


async def set_wizard_session(
    conn: AsyncConnection,
    *,
    user_id: int,
    action_id: str,
    step: str,
    parent_callback: str = "u:home",
    context: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> WizardSession:
    ts = now or datetime.now(UTC)
    exp = ts + SESSION_TTL
    ctx = dict(context or {})
    payload = {
        "action_id": action_id,
        "step": step,
        "parent_callback": parent_callback,
        "context": ctx,
    }
    await conn.execute(
        text(
            "INSERT INTO kv_cache(cache_key, value_json, expires_at, updated_at) "
            "VALUES (:k, CAST(:v AS jsonb), :exp, :now) "
            "ON CONFLICT (cache_key) DO UPDATE SET "
            "value_json = EXCLUDED.value_json, expires_at = EXCLUDED.expires_at, updated_at = :now"
        ),
        {
            "k": _session_key(user_id),
            "v": json.dumps(payload, ensure_ascii=False),
            "exp": exp,
            "now": ts,
        },
    )
    return WizardSession(
        user_id=user_id,
        action_id=action_id,
        step=step,
        parent_callback=parent_callback,
        context=ctx,
        expires_at=exp,
    )


async def get_wizard_session(
    conn: AsyncConnection,
    *,
    user_id: int,
    now: datetime | None = None,
) -> WizardSession | None:
    ts = now or datetime.now(UTC)
    row = (
        await conn.execute(
            text(
                "SELECT value_json, expires_at FROM kv_cache "
                "WHERE cache_key = :k AND (expires_at IS NULL OR expires_at > :now)"
            ),
            {"k": _session_key(user_id), "now": ts},
        )
    ).first()
    if row is None:
        return None
    m = row._mapping
    raw = m["value_json"]
    data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return WizardSession(
        user_id=user_id,
        action_id=str(data.get("action_id", "")),
        step=str(data.get("step", "")),
        parent_callback=str(data.get("parent_callback", "u:home")),
        context=dict(data.get("context") or {}),
        expires_at=m["expires_at"],
    )


async def clear_wizard_session(
    conn: AsyncConnection,
    *,
    user_id: int,
) -> WizardSession | None:
    cur = await get_wizard_session(conn, user_id=user_id)
    await conn.execute(
        text("DELETE FROM kv_cache WHERE cache_key = :k"),
        {"k": _session_key(user_id)},
    )
    return cur


async def set_quote_discount(
    conn: AsyncConnection,
    *,
    user_id: int,
    plan_id: int,
    discount_code: str,
    now: datetime | None = None,
) -> None:
    ts = now or datetime.now(UTC)
    await conn.execute(
        text(
            "INSERT INTO kv_cache(cache_key, value_json, expires_at, updated_at) "
            "VALUES (:k, CAST(:v AS jsonb), :exp, :now) "
            "ON CONFLICT (cache_key) DO UPDATE SET "
            "value_json = EXCLUDED.value_json, expires_at = EXCLUDED.expires_at, updated_at = :now"
        ),
        {
            "k": _quote_discount_key(user_id, plan_id),
            "v": json.dumps({"code": discount_code.strip().upper()}),
            "exp": ts + timedelta(hours=2),
            "now": ts,
        },
    )


async def get_quote_discount(
    conn: AsyncConnection,
    *,
    user_id: int,
    plan_id: int,
    now: datetime | None = None,
) -> str | None:
    ts = now or datetime.now(UTC)
    row = (
        await conn.execute(
            text(
                "SELECT value_json FROM kv_cache "
                "WHERE cache_key = :k AND (expires_at IS NULL OR expires_at > :now)"
            ),
            {"k": _quote_discount_key(user_id, plan_id), "now": ts},
        )
    ).first()
    if row is None:
        return None
    raw = row._mapping["value_json"]
    data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    code = data.get("code")
    return str(code) if code else None


async def clear_quote_discount(
    conn: AsyncConnection,
    *,
    user_id: int,
    plan_id: int,
) -> None:
    await conn.execute(
        text("DELETE FROM kv_cache WHERE cache_key = :k"),
        {"k": _quote_discount_key(user_id, plan_id)},
    )
