"""PostgreSQL persistence for Channels, 24 Editorial Settings, and Ownership Transfer Tokens (`ch_tokens`)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.application.policy import require_active, require_owner
from opengenerate.domain.channels import (
    DEFAULT_TOKEN_TTL_HOURS,
    ChannelSettings,
    generate_transfer_token,
    hash_transfer_token,
    validate_token_ttl_hours,
)
from opengenerate.domain.errors import AccessDenied, Conflict, NotFound, QuotaExceeded, ValidationFailed
from opengenerate.domain.roles import Actor, Role
from opengenerate.infrastructure.db.billing_store import get_plan
from opengenerate.infrastructure.db.identity_store import get_user

BotAdminVerifier = Callable[[int], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ChannelRecord:
    channel_id: int
    username: str | None
    title: str
    owner_id: int
    original_owner_id: int
    enabled: bool
    settings: ChannelSettings
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TransferTokenRecord:
    token_id: int
    channel_id: int
    token_hash: str
    issued_by: int
    expires_at: datetime
    attempt_count: int
    max_attempts: int
    consumed_at: datetime | None
    consumed_by: int | None
    created_at: datetime


def _row_to_channel(row: Any) -> ChannelRecord:
    m = row._mapping
    raw_settings = m["settings_json"]
    settings_dict = json.loads(raw_settings) if isinstance(raw_settings, str) else (raw_settings or {})
    return ChannelRecord(
        channel_id=int(m["channel_id"]),
        username=m["username"],
        title=str(m["title"]),
        owner_id=int(m["owner_id"]),
        original_owner_id=int(m["original_owner_id"]),
        enabled=bool(m["enabled"]),
        settings=ChannelSettings.from_dict(settings_dict),
        last_run_at=m["last_run_at"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


def _row_to_token(row: Any) -> TransferTokenRecord:
    m = row._mapping
    return TransferTokenRecord(
        token_id=int(m["token_id"]),
        channel_id=int(m["channel_id"]),
        token_hash=str(m["token_hash"]),
        issued_by=int(m["issued_by"]),
        expires_at=m["expires_at"],
        attempt_count=int(m["attempt_count"]),
        max_attempts=int(m["max_attempts"]),
        consumed_at=m["consumed_at"],
        consumed_by=int(m["consumed_by"]) if m["consumed_by"] is not None else None,
        created_at=m["created_at"],
    )


async def _enforce_max_channels_quota(
    conn: AsyncConnection,
    *,
    user_id: int,
    now: datetime,
    bootstrap_superadmins: Collection[int] = (),
) -> None:
    user = await get_user(conn, user_id, bootstrap_superadmins)
    if user.is_banned:
        raise AccessDenied("user is banned")
    if user.role is Role.SUPERADMIN:
        return
    if user.plan_id is None or user.plan_expires_at is None or user.plan_expires_at <= now:
        raise QuotaExceeded("active_subscription_required")
    plan = await get_plan(conn, user.plan_id)
    current_count = int(
        (
            await conn.execute(
                text("SELECT count(*) FROM channels WHERE owner_id = :u"),
                {"u": user_id},
            )
        ).scalar_one()
    )
    if current_count >= plan.max_channels:
        raise QuotaExceeded("max_channels_quota_exceeded")


async def verify_bot_channel_permissions(
    channel_id: int,
    get_bot_member: BotAdminVerifier | None,
) -> None:
    """Verify that the bot is an administrator with post permissions in `channel_id`."""
    if get_bot_member is None:
        return
    member = await get_bot_member(channel_id)
    status = str(member.get("status", ""))
    if status == "creator":
        return
    if status != "administrator":
        raise ValidationFailed("bot_is_not_channel_administrator")
    if member.get("can_post_messages") is False:
        raise ValidationFailed("bot_lacks_can_post_messages_permission")


async def register_channel(
    conn: AsyncConnection,
    *,
    actor: Actor,
    channel_id: int,
    title: str,
    username: str | None = None,
    settings: ChannelSettings | None = None,
    get_bot_member: BotAdminVerifier | None = None,
    now: datetime | None = None,
    bootstrap_superadmins: Collection[int] = (),
) -> ChannelRecord:
    """Register a Telegram channel under `actor.user_id` after verifying quota and bot admin rights."""
    require_active(actor)
    if channel_id >= 0:
        raise ValidationFailed("Telegram channel_id must be negative")
    ts = now or datetime.now(UTC)

    existing = (
        await conn.execute(
            text("SELECT channel_id FROM channels WHERE channel_id = :cid"),
            {"cid": channel_id},
        )
    ).first()
    if existing is not None:
        raise Conflict("channel_already_registered")

    await _enforce_max_channels_quota(
        conn, user_id=actor.user_id, now=ts, bootstrap_superadmins=bootstrap_superadmins
    )
    await verify_bot_channel_permissions(channel_id, get_bot_member)

    cfg = settings or ChannelSettings()
    clean_username = username.lstrip("@") if username else None
    row = (
        await conn.execute(
            text(
                "INSERT INTO channels("
                "channel_id, username, title, owner_id, original_owner_id, enabled, settings_json) "
                "VALUES (:cid, :un, :t, :o, :oo, FALSE, CAST(:s AS jsonb)) RETURNING *"
            ),
            {
                "cid": channel_id,
                "un": clean_username,
                "t": title.strip() or str(channel_id),
                "o": actor.user_id,
                "oo": actor.user_id,
                "s": json.dumps(cfg.to_dict()),
            },
        )
    ).one()
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id) "
            "VALUES (:a, 'register_channel', 'channel', :oid)"
        ),
        {"a": actor.user_id, "oid": str(channel_id)},
    )
    return _row_to_channel(row)


async def get_channel(
    conn: AsyncConnection,
    *,
    actor: Actor,
    channel_id: int,
) -> ChannelRecord:
    require_active(actor)
    row = (
        await conn.execute(
            text("SELECT * FROM channels WHERE channel_id = :cid"),
            {"cid": channel_id},
        )
    ).first()
    if row is None:
        raise NotFound("channel not found")
    ch = _row_to_channel(row)
    require_owner(actor, ch.owner_id, allow_superadmin=True)
    return ch


async def list_channels(
    conn: AsyncConnection,
    *,
    actor: Actor,
    all_for_superadmin: bool = False,
) -> list[ChannelRecord]:
    require_active(actor)
    if actor.is_superadmin and all_for_superadmin:
        rows = await conn.execute(
            text("SELECT * FROM channels ORDER BY created_at DESC, channel_id ASC")
        )
    else:
        rows = await conn.execute(
            text(
                "SELECT * FROM channels WHERE owner_id = :u ORDER BY created_at DESC, channel_id ASC"
            ),
            {"u": actor.user_id},
        )
    return [_row_to_channel(r) for r in rows]


async def set_channel_enabled(
    conn: AsyncConnection,
    *,
    actor: Actor,
    channel_id: int,
    enabled: bool,
) -> ChannelRecord:
    ch = await get_channel(conn, actor=actor, channel_id=channel_id)
    row = (
        await conn.execute(
            text(
                "UPDATE channels SET enabled = :en, updated_at = now() "
                "WHERE channel_id = :cid RETURNING *"
            ),
            {"en": enabled, "cid": ch.channel_id},
        )
    ).one()
    return _row_to_channel(row)


async def update_channel_settings(
    conn: AsyncConnection,
    *,
    actor: Actor,
    channel_id: int,
    settings: ChannelSettings,
) -> ChannelRecord:
    ch = await get_channel(conn, actor=actor, channel_id=channel_id)
    payload = json.dumps(settings.to_dict())
    row = (
        await conn.execute(
            text(
                "UPDATE channels SET settings_json = CAST(:s AS jsonb), updated_at = now() "
                "WHERE channel_id = :cid RETURNING *"
            ),
            {"s": payload, "cid": ch.channel_id},
        )
    ).one()
    return _row_to_channel(row)


async def reset_channel_settings(
    conn: AsyncConnection,
    *,
    actor: Actor,
    channel_id: int,
) -> ChannelRecord:
    """Reset all 24 channel settings to defaults while preserving registered sources."""
    return await update_channel_settings(
        conn, actor=actor, channel_id=channel_id, settings=ChannelSettings()
    )


async def delete_channel(
    conn: AsyncConnection,
    *,
    actor: Actor,
    channel_id: int,
) -> None:
    ch = await get_channel(conn, actor=actor, channel_id=channel_id)
    await conn.execute(
        text("DELETE FROM channels WHERE channel_id = :cid"),
        {"cid": ch.channel_id},
    )
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id) "
            "VALUES (:a, 'delete_channel', 'channel', :oid)"
        ),
        {"a": actor.user_id, "oid": str(ch.channel_id)},
    )


# ---- Channel Ownership Transfer Tokens (`ch_tokens`) -----------------------------------------


async def issue_transfer_token(
    conn: AsyncConnection,
    *,
    actor: Actor,
    channel_id: int,
    ttl_hours: int = DEFAULT_TOKEN_TTL_HOURS,
    max_attempts: int = 5,
    now: datetime | None = None,
) -> tuple[str, TransferTokenRecord]:
    """Issue a one-time transfer token; returns `(raw_token, TransferTokenRecord)` storing only `token_hash`."""
    ch = await get_channel(conn, actor=actor, channel_id=channel_id)
    validate_token_ttl_hours(ttl_hours)
    if max_attempts < 1:
        raise ValidationFailed("max_attempts must be >= 1")
    ts = now or datetime.now(UTC)
    raw_token, token_hash = generate_transfer_token()
    expires_at = ts + timedelta(hours=ttl_hours)

    row = (
        await conn.execute(
            text(
                "INSERT INTO ch_tokens(channel_id, token_hash, issued_by, expires_at, max_attempts) "
                "VALUES (:cid, :th, :u, :exp, :max) RETURNING *"
            ),
            {
                "cid": ch.channel_id,
                "th": token_hash,
                "u": actor.user_id,
                "exp": expires_at,
                "max": max_attempts,
            },
        )
    ).one()
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id) "
            "VALUES (:a, 'issue_transfer_token', 'channel', :oid)"
        ),
        {"a": actor.user_id, "oid": str(ch.channel_id)},
    )
    return raw_token, _row_to_token(row)


async def list_active_transfer_tokens(
    conn: AsyncConnection,
    *,
    actor: Actor,
    channel_id: int,
    now: datetime | None = None,
) -> list[TransferTokenRecord]:
    ch = await get_channel(conn, actor=actor, channel_id=channel_id)
    ts = now or datetime.now(UTC)
    rows = await conn.execute(
        text(
            "SELECT * FROM ch_tokens "
            "WHERE channel_id = :cid AND consumed_at IS NULL AND expires_at > :now "
            "AND attempt_count < max_attempts ORDER BY created_at DESC"
        ),
        {"cid": ch.channel_id, "now": ts},
    )
    return [_row_to_token(r) for r in rows]


async def revoke_transfer_token(
    conn: AsyncConnection,
    *,
    actor: Actor,
    channel_id: int,
    token_id: int,
) -> None:
    ch = await get_channel(conn, actor=actor, channel_id=channel_id)
    res = await conn.execute(
        text("DELETE FROM ch_tokens WHERE token_id = :tid AND channel_id = :cid"),
        {"tid": token_id, "cid": ch.channel_id},
    )
    if (res.rowcount or 0) == 0:
        raise NotFound("transfer token not found")
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id) "
            "VALUES (:a, 'revoke_transfer_token', 'channel', :oid)"
        ),
        {"a": actor.user_id, "oid": f"{ch.channel_id}:{token_id}"},
    )


async def redeem_transfer_token(
    conn: AsyncConnection,
    *,
    actor: Actor,
    raw_token: str,
    now: datetime | None = None,
    bootstrap_superadmins: Collection[int] = (),
) -> ChannelRecord:
    """Atomically redeem a channel transfer token, transferring ownership and invalidating sibling tokens."""
    require_active(actor)
    ts = now or datetime.now(UTC)
    token_hash = hash_transfer_token(raw_token)

    tok_row = (
        await conn.execute(
            text("SELECT * FROM ch_tokens WHERE token_hash = :th FOR UPDATE"),
            {"th": token_hash},
        )
    ).first()
    if tok_row is None:
        raise NotFound("invalid_or_expired_transfer_token")
    tok = _row_to_token(tok_row)

    if tok.consumed_at is not None:
        raise Conflict("transfer_token_already_consumed")
    if tok.attempt_count >= tok.max_attempts:
        raise Conflict("transfer_token_attempts_exhausted")
    if tok.expires_at <= ts:
        raise Conflict("transfer_token_expired")

    ch_row = (
        await conn.execute(
            text("SELECT * FROM channels WHERE channel_id = :cid FOR UPDATE"),
            {"cid": tok.channel_id},
        )
    ).one()
    ch = _row_to_channel(ch_row)
    if ch.owner_id == actor.user_id:
        raise Conflict("cannot_redeem_own_channel_token")

    # Enforce target user's max_channels quota before transferring ownership
    await _enforce_max_channels_quota(
        conn, user_id=actor.user_id, now=ts, bootstrap_superadmins=bootstrap_superadmins
    )

    # Mark this token consumed and invalidate all other unconsumed tokens for this channel
    await conn.execute(
        text(
            "UPDATE ch_tokens SET consumed_at = :now, consumed_by = :u "
            "WHERE token_id = :tid"
        ),
        {"now": ts, "u": actor.user_id, "tid": tok.token_id},
    )
    await conn.execute(
        text(
            "DELETE FROM ch_tokens "
            "WHERE channel_id = :cid AND token_id <> :tid AND consumed_at IS NULL"
        ),
        {"cid": ch.channel_id, "tid": tok.token_id},
    )

    # Transfer channel ownership while preserving original_owner_id and reassigning source admin_id
    updated_ch_row = (
        await conn.execute(
            text(
                "UPDATE channels SET owner_id = :new_o, updated_at = now() "
                "WHERE channel_id = :cid RETURNING *"
            ),
            {"new_o": actor.user_id, "cid": ch.channel_id},
        )
    ).one()
    await conn.execute(
        text("UPDATE sources SET admin_id = :new_o, updated_at = now() WHERE channel_id = :cid"),
        {"new_o": actor.user_id, "cid": ch.channel_id},
    )
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id, before_hash, after_hash) "
            "VALUES (:a, 'redeem_transfer_token', 'channel', :oid, :bh, :ah)"
        ),
        {
            "a": actor.user_id,
            "oid": str(ch.channel_id),
            "bh": f"owner_id={ch.owner_id}",
            "ah": f"owner_id={actor.user_id}",
        },
    )
    return _row_to_channel(updated_ch_row)
