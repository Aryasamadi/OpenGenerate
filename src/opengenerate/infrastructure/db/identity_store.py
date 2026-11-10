"""PostgreSQL persistence for Identity, Roles, Superadmin User Controls, Referrals & Mandatory Membership."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.application.policy import require_active, require_superadmin
from opengenerate.domain.errors import (
    Conflict,
    NotFound,
    TransientError,
    UnknownOutcomeError,
    ValidationFailed,
)
from opengenerate.domain.referrals import DEFAULT_REFERRAL_PERCENT, ReferralEconomics
from opengenerate.domain.roles import Actor, Role, effective_role
from opengenerate.infrastructure import outbox

LanguageCode = Literal["fa", "en"]
MEMBER_ACTIVE_STATUSES = frozenset({"creator", "administrator", "member"})


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: int
    username: str | None
    full_name: str | None
    role: Role
    lang: str | None
    plan_id: int | None
    plan_expires_at: datetime | None
    queued_plan_id: int | None
    is_banned: bool
    free_plan_used: bool


@dataclass(frozen=True, slots=True)
class StartResult:
    user: UserRecord
    is_new_user: bool
    needs_language_selection: bool
    referral_attributed: bool
    referrer_id: int | None


@dataclass(frozen=True, slots=True)
class ReferralDashboard:
    user_id: int
    deep_link_param: str
    invited_count: int
    rewarded_payments_count: int
    earned_tmn: int
    earned_usd: int
    economics: ReferralEconomics


@dataclass(frozen=True, slots=True)
class MandatoryChannelConfig:
    lang: LanguageCode
    enabled: bool
    channel_id: int | None
    channel_username: str | None

    @property
    def target_chat(self) -> int | str | None:
        if self.channel_id is not None:
            return self.channel_id
        if self.channel_username:
            u = self.channel_username.lstrip("@")
            return f"@{u}"
        return None


@dataclass(frozen=True, slots=True)
class MembershipGateResult:
    allowed: bool
    degraded_bypass: bool = False
    channel_id: int | None = None
    channel_username: str | None = None


def _row_to_user(row: Any, bootstrap_superadmins: Collection[int] = ()) -> UserRecord:
    m = row._mapping
    role = effective_role(int(m["user_id"]), Role(str(m["role"])), bootstrap_superadmins)
    return UserRecord(
        user_id=int(m["user_id"]),
        username=m["username"],
        full_name=m["full_name"],
        role=role,
        lang=m["lang"],
        plan_id=int(m["plan_id"]) if m["plan_id"] is not None else None,
        plan_expires_at=m["plan_expires_at"],
        queued_plan_id=int(m["queued_plan_id"]) if m["queued_plan_id"] is not None else None,
        is_banned=bool(m["is_banned"]),
        free_plan_used=bool(m["free_plan_used"]),
    )


async def register_or_update_user(
    conn: AsyncConnection,
    *,
    user_id: int,
    username: str | None = None,
    full_name: str | None = None,
    referral_param: str | None = None,
    bootstrap_superadmins: Collection[int] = (),
) -> StartResult:
    """Handle `/start` registration and first-touch referral attribution (`ref_<user_id>`)."""
    if user_id <= 0:
        raise ValidationFailed("user_id must be positive")
    initial_role = Role.SUPERADMIN.value if user_id in bootstrap_superadmins else Role.USER.value

    existing = (
        await conn.execute(text("SELECT * FROM users WHERE user_id = :u FOR UPDATE"), {"u": user_id})
    ).first()
    is_new = existing is None
    if existing is None:
        row = (
            await conn.execute(
                text(
                    "INSERT INTO users(user_id, username, full_name, role) "
                    "VALUES (:u, :un, :fn, :r) RETURNING *"
                ),
                {"u": user_id, "un": username, "fn": full_name, "r": initial_role},
            )
        ).one()
    else:
        new_role = (
            Role.SUPERADMIN.value
            if user_id in bootstrap_superadmins
            else str(existing._mapping["role"])
        )
        row = (
            await conn.execute(
                text(
                    "UPDATE users SET username = COALESCE(:un, username), "
                    "full_name = COALESCE(:fn, full_name), role = :r, updated_at = now() "
                    "WHERE user_id = :u RETURNING *"
                ),
                {"u": user_id, "un": username, "fn": full_name, "r": new_role},
            )
        ).one()

    user = _row_to_user(row, bootstrap_superadmins)
    attributed = False
    referrer_id: int | None = None

    if referral_param and referral_param.startswith("ref_"):
        raw_ref = referral_param[4:].strip()
        if raw_ref.isdigit():
            cand = int(raw_ref)
            if cand > 0 and cand != user_id:
                ref_row = (
                    await conn.execute(
                        text("SELECT user_id, is_banned FROM users WHERE user_id = :r"),
                        {"r": cand},
                    )
                ).first()
                if ref_row is not None and not bool(ref_row._mapping["is_banned"]):
                    ins = (
                        await conn.execute(
                            text(
                                "INSERT INTO referrals(referred_user_id, referrer_user_id) "
                                "VALUES (:u, :r) ON CONFLICT (referred_user_id) DO NOTHING "
                                "RETURNING referrer_user_id"
                            ),
                            {"u": user_id, "r": cand},
                        )
                    ).first()
                    if ins is not None:
                        attributed = True
                        referrer_id = cand

    return StartResult(
        user=user,
        is_new_user=is_new,
        needs_language_selection=user.lang is None,
        referral_attributed=attributed,
        referrer_id=referrer_id,
    )


async def set_user_language(
    conn: AsyncConnection,
    *,
    actor: Actor,
    lang: LanguageCode,
) -> UserRecord:
    require_active(actor)
    if lang not in ("fa", "en"):
        raise ValidationFailed("language must be 'fa' or 'en'")
    row = (
        await conn.execute(
            text("UPDATE users SET lang = :l, updated_at = now() WHERE user_id = :u RETURNING *"),
            {"l": lang, "u": actor.user_id},
        )
    ).first()
    if row is None:
        raise NotFound("user not found")
    return _row_to_user(row)


async def get_user(
    conn: AsyncConnection,
    user_id: int,
    bootstrap_superadmins: Collection[int] = (),
) -> UserRecord:
    row = (await conn.execute(text("SELECT * FROM users WHERE user_id = :u"), {"u": user_id})).first()
    if row is None:
        raise NotFound("user not found")
    return _row_to_user(row, bootstrap_superadmins)


async def list_users(
    conn: AsyncConnection,
    *,
    actor: Actor,
    admins_only: bool = False,
    page: int = 1,
    page_size: int = 15,
    bootstrap_superadmins: Collection[int] = (),
) -> tuple[list[UserRecord], int]:
    require_superadmin(actor)
    offset = max(0, (page - 1) * page_size)
    where = "WHERE role IN ('admin', 'superadmin') OR plan_id IS NOT NULL" if admins_only else ""
    total = int(
        (await conn.execute(text(f"SELECT count(*) FROM users {where}"))).scalar_one()
    )
    rows = await conn.execute(
        text(f"SELECT * FROM users {where} ORDER BY created_at DESC, user_id ASC LIMIT :lim OFFSET :off"),
        {"lim": page_size, "off": offset},
    )
    return [_row_to_user(r, bootstrap_superadmins) for r in rows], total


async def ban_or_unban_user(
    conn: AsyncConnection,
    *,
    actor: Actor,
    target_user_id: int,
    ban: bool,
    reason: str = "",
    bootstrap_superadmins: Collection[int] = (),
) -> UserRecord:
    require_superadmin(actor)
    if target_user_id == actor.user_id:
        raise Conflict("superadmin cannot ban or lock out self")
    target = await get_user(conn, target_user_id, bootstrap_superadmins)
    if target.role is Role.SUPERADMIN:
        raise Conflict("cannot ban or lock out another superadmin")

    row = (
        await conn.execute(
            text(
                "UPDATE users SET is_banned = :b, updated_at = now() WHERE user_id = :u RETURNING *"
            ),
            {"b": ban, "u": target_user_id},
        )
    ).one()
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id, before_hash, after_hash, reason) "
            "VALUES (:a, :act, 'user', :oid, :bh, :ah, :r)"
        ),
        {
            "a": actor.user_id,
            "act": "user_ban" if ban else "user_unban",
            "oid": str(target_user_id),
            "bh": f"is_banned={target.is_banned}",
            "ah": f"is_banned={ban}",
            "r": reason,
        },
    )
    return _row_to_user(row, bootstrap_superadmins)


async def reset_free_plan_eligibility(
    conn: AsyncConnection,
    *,
    actor: Actor,
    target_user_id: int,
) -> UserRecord:
    require_superadmin(actor)
    row = (
        await conn.execute(
            text(
                "UPDATE users SET free_plan_used = FALSE, updated_at = now() "
                "WHERE user_id = :u RETURNING *"
            ),
            {"u": target_user_id},
        )
    ).first()
    if row is None:
        raise NotFound("user not found")
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id) "
            "VALUES (:a, 'reset_free_plan_eligibility', 'user', :oid)"
        ),
        {"a": actor.user_id, "oid": str(target_user_id)},
    )
    return _row_to_user(row)


async def reset_daily_usage(
    conn: AsyncConnection,
    *,
    actor: Actor,
    target_user_id: int,
    reset_posts: bool = False,
    reset_tests: bool = False,
) -> None:
    require_superadmin(actor)
    if not reset_posts and not reset_tests:
        raise ValidationFailed("must specify at least one counter to reset")
    sets: list[str] = []
    if reset_posts:
        sets.append("posts_count = 0")
    if reset_tests:
        sets.append("tests_count = 0")
    await conn.execute(
        text(f"UPDATE usage SET {', '.join(sets)} WHERE user_id = :u AND usage_date = CURRENT_DATE"),
        {"u": target_user_id},
    )
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id, reason) "
            "VALUES (:a, 'reset_daily_usage', 'user', :oid, :r)"
        ),
        {
            "a": actor.user_id,
            "oid": str(target_user_id),
            "r": f"posts={reset_posts},tests={reset_tests}",
        },
    )


async def send_direct_message_to_user(
    conn: AsyncConnection,
    *,
    actor: Actor,
    target_user_id: int,
    message_text: str,
    idempotency_key: str,
) -> None:
    require_superadmin(actor)
    if not message_text.strip():
        raise ValidationFailed("message_text cannot be empty")
    await get_user(conn, target_user_id)
    await outbox.enqueue(
        conn,
        idempotency_key=idempotency_key,
        recipient_id=target_user_id,
        kind="text",
        payload={"text": message_text},
    )
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id) "
            "VALUES (:a, 'admin_direct_message', 'user', :oid)"
        ),
        {"a": actor.user_id, "oid": str(target_user_id)},
    )


# ---- Referral Economics & Dashboard ----------------------------------------------------------


async def get_referral_economics(conn: AsyncConnection) -> ReferralEconomics:
    row = (
        await conn.execute(text("SELECT value FROM settings WHERE key = 'referral_economics'"))
    ).first()
    if row is None:
        return ReferralEconomics()
    val = row._mapping["value"]
    data = json.loads(val) if isinstance(val, str) else val
    return ReferralEconomics(
        percent=int(data.get("percent", DEFAULT_REFERRAL_PERCENT)),
        cap_tmn=int(data.get("cap_tmn", 0)),
        cap_usd=int(data.get("cap_usd", 0)),
    )


async def set_referral_economics(
    conn: AsyncConnection,
    *,
    actor: Actor,
    economics: ReferralEconomics,
) -> ReferralEconomics:
    require_superadmin(actor)
    payload = json.dumps(
        {"percent": economics.percent, "cap_tmn": economics.cap_tmn, "cap_usd": economics.cap_usd}
    )
    await conn.execute(
        text(
            "INSERT INTO settings(key, value, updated_by, updated_at) "
            "VALUES ('referral_economics', CAST(:v AS jsonb), :u, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()"
        ),
        {"v": payload, "u": actor.user_id},
    )
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id, after_hash) "
            "VALUES (:a, 'update_referral_economics', 'settings', 'referral_economics', :ah)"
        ),
        {"a": actor.user_id, "ah": payload},
    )
    return economics


async def get_referral_dashboard(conn: AsyncConnection, *, actor: Actor) -> ReferralDashboard:
    require_active(actor)
    econ = await get_referral_economics(conn)
    row = (
        await conn.execute(
            text(
                "SELECT count(*) AS cnt, "
                "       COALESCE(SUM(rewarded_payments_count), 0) AS p_cnt, "
                "       COALESCE(SUM(total_reward_tmn), 0) AS tmn, "
                "       COALESCE(SUM(total_reward_usd), 0) AS usd "
                "FROM referrals WHERE referrer_user_id = :u"
            ),
            {"u": actor.user_id},
        )
    ).one()
    m = row._mapping
    return ReferralDashboard(
        user_id=actor.user_id,
        deep_link_param=f"ref_{actor.user_id}",
        invited_count=int(m["cnt"]),
        rewarded_payments_count=int(m["p_cnt"]),
        earned_tmn=int(m["tmn"]),
        earned_usd=int(m["usd"]),
        economics=econ,
    )


# ---- Mandatory Membership Configuration & Verification ---------------------------------------


async def get_mandatory_channel_config(
    conn: AsyncConnection, lang: LanguageCode
) -> MandatoryChannelConfig:
    key = f"mandatory_channel:{lang}"
    row = (await conn.execute(text("SELECT value FROM settings WHERE key = :k"), {"k": key})).first()
    if row is None:
        return MandatoryChannelConfig(lang=lang, enabled=False, channel_id=None, channel_username=None)
    val = row._mapping["value"]
    data = json.loads(val) if isinstance(val, str) else val
    return MandatoryChannelConfig(
        lang=lang,
        enabled=bool(data.get("enabled", False)),
        channel_id=int(data["channel_id"]) if data.get("channel_id") is not None else None,
        channel_username=data.get("channel_username"),
    )


async def set_mandatory_channel_config(
    conn: AsyncConnection,
    *,
    actor: Actor,
    config: MandatoryChannelConfig,
) -> MandatoryChannelConfig:
    require_superadmin(actor)
    if config.channel_id is not None and config.channel_id >= 0:
        raise ValidationFailed("Telegram channel_id must be negative")
    key = f"mandatory_channel:{config.lang}"
    payload = json.dumps(
        {
            "enabled": config.enabled,
            "channel_id": config.channel_id,
            "channel_username": config.channel_username,
        }
    )
    await conn.execute(
        text(
            "INSERT INTO settings(key, value, updated_by, updated_at) "
            "VALUES (:k, CAST(:v AS jsonb), :u, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()"
        ),
        {"k": key, "v": payload, "u": actor.user_id},
    )
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id, after_hash) "
            "VALUES (:a, 'set_mandatory_channel', 'settings', :k, :ah)"
        ),
        {"a": actor.user_id, "k": key, "ah": payload},
    )
    return config


async def check_mandatory_membership(
    conn: AsyncConnection,
    *,
    actor: Actor,
    lang: LanguageCode,
    get_chat_member: Any,
) -> MembershipGateResult:
    """Server-side mandatory channel gate with fail-safe handling of transient network errors."""
    require_active(actor)
    if actor.is_superadmin:
        return MembershipGateResult(allowed=True)
    cfg = await get_mandatory_channel_config(conn, lang)
    target = cfg.target_chat
    if not cfg.enabled or target is None:
        return MembershipGateResult(allowed=True)
    try:
        member: dict[str, Any] = await get_chat_member(target, actor.user_id)
    except (TransientError, UnknownOutcomeError):
        # Section 8.7: Fail safely when Telegram cannot verify membership; never permanently lock out
        # users because of a transient network error.
        return MembershipGateResult(
            allowed=True,
            degraded_bypass=True,
            channel_id=cfg.channel_id,
            channel_username=cfg.channel_username,
        )
    status = str(member.get("status", ""))
    is_member = status in MEMBER_ACTIVE_STATUSES or (
        status == "restricted" and bool(member.get("is_member", False))
    )
    if is_member:
        return MembershipGateResult(
            allowed=True, channel_id=cfg.channel_id, channel_username=cfg.channel_username
        )
    return MembershipGateResult(
        allowed=False, channel_id=cfg.channel_id, channel_username=cfg.channel_username
    )
