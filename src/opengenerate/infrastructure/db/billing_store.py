"""PostgreSQL persistence for Plans, Subscriptions, Quotas, Discounts, Quotes, Payments, Wallets & Refunds."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.application.policy import require_active, require_superadmin
from opengenerate.domain.errors import Conflict, NotFound, QuotaExceeded, ValidationFailed
from opengenerate.domain.money import Currency, LedgerReason
from opengenerate.domain.referrals import compute_referral_clawback, compute_referral_reward
from opengenerate.domain.roles import Actor, Role
from opengenerate.domain.subscriptions import (
    ActiveSubscription,
    PlanQuote,
    PlanSpec,
    QuoteDriftError,
    build_quote,
    currency_for_language,
    is_eligible_upgrade,
)
from opengenerate.infrastructure import outbox
from opengenerate.infrastructure.db.identity_store import get_referral_economics, get_user
from opengenerate.infrastructure.db.ledger import get_balance, post_ledger_entry

ReceiptType = Literal["text", "photo", "document", "wallet_full"]
PaymentStatus = Literal["pending", "processing", "approved", "rejected", "refunded"]


@dataclass(frozen=True, slots=True)
class PlanRecord:
    plan_id: int
    name_fa: str
    name_en: str
    desc_fa: str
    desc_en: str
    price_tmn: int | None
    price_usd: int | None
    price_text_fa: str | None
    price_text_en: str | None
    duration_days: int
    daily_post_limit: int
    max_sources: int
    max_channels: int
    daily_test_limit: int
    is_free: bool
    is_active: bool
    sort_order: int

    def to_spec(self) -> PlanSpec:
        return PlanSpec(
            plan_id=self.plan_id,
            duration_days=self.duration_days,
            daily_post_limit=self.daily_post_limit,
            max_sources=self.max_sources,
            max_channels=self.max_channels,
            daily_test_limit=self.daily_test_limit,
            price_tmn=self.price_tmn,
            price_usd=self.price_usd,
            is_free=self.is_free,
            is_active=self.is_active,
        )


@dataclass(frozen=True, slots=True)
class DiscountRecord:
    code: str
    percent: int
    expires_at: datetime | None
    max_uses: int
    used_count: int
    is_active: bool

    def is_usable_at(self, now: datetime) -> bool:
        if not self.is_active:
            return False
        if self.expires_at is not None and self.expires_at <= now:
            return False
        if self.max_uses > 0 and self.used_count >= self.max_uses:
            return False
        return True


@dataclass(frozen=True, slots=True)
class PaymentRequestRecord:
    payment_id: int
    idempotency_key: str
    user_id: int
    plan_id: int
    currency: Currency
    quoted_base_price: int
    discount_code: str | None
    discount_amount: int
    wallet_reserved: int
    payable_amount: int
    receipt_type: ReceiptType
    receipt_file_id: str | None
    receipt_text: str | None
    status: PaymentStatus
    reviewer_id: int | None
    reviewed_at: datetime | None
    applied_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DailyUsageRecord:
    user_id: int
    posts_count: int
    tests_count: int
    ai_calls_count: int
    ai_tokens_count: int


def _row_to_plan(row: Any) -> PlanRecord:
    m = row._mapping
    return PlanRecord(
        plan_id=int(m["plan_id"]),
        name_fa=str(m["name_fa"]),
        name_en=str(m["name_en"]),
        desc_fa=str(m["desc_fa"]),
        desc_en=str(m["desc_en"]),
        price_tmn=int(m["price_tmn"]) if m["price_tmn"] is not None else None,
        price_usd=int(m["price_usd"]) if m["price_usd"] is not None else None,
        price_text_fa=m["price_text_fa"],
        price_text_en=m["price_text_en"],
        duration_days=int(m["duration_days"]),
        daily_post_limit=int(m["daily_post_limit"]),
        max_sources=int(m["max_sources"]),
        max_channels=int(m["max_channels"]),
        daily_test_limit=int(m["daily_test_limit"]),
        is_free=bool(m["is_free"]),
        is_active=bool(m["is_active"]),
        sort_order=int(m["sort_order"]),
    )


def _row_to_discount(row: Any) -> DiscountRecord:
    m = row._mapping
    return DiscountRecord(
        code=str(m["code"]),
        percent=int(m["percent"]),
        expires_at=m["expires_at"],
        max_uses=int(m["max_uses"]),
        used_count=int(m["used_count"]),
        is_active=bool(m["is_active"]),
    )


def _row_to_payment(row: Any) -> PaymentRequestRecord:
    m = row._mapping
    return PaymentRequestRecord(
        payment_id=int(m["payment_id"]),
        idempotency_key=str(m["idempotency_key"]),
        user_id=int(m["user_id"]),
        plan_id=int(m["plan_id"]),
        currency=Currency(str(m["currency"])),
        quoted_base_price=int(m["quoted_base_price"]),
        discount_code=m["discount_code"],
        discount_amount=int(m["discount_amount"]),
        wallet_reserved=int(m["wallet_reserved"]),
        payable_amount=int(m["payable_amount"]),
        receipt_type=m["receipt_type"],
        receipt_file_id=m["receipt_file_id"],
        receipt_text=m["receipt_text"],
        status=m["status"],
        reviewer_id=int(m["reviewer_id"]) if m["reviewer_id"] is not None else None,
        reviewed_at=m["reviewed_at"],
        applied_at=m["applied_at"],
        created_at=m["created_at"],
    )


# ---- Plan Management -------------------------------------------------------------------------


async def create_plan(
    conn: AsyncConnection,
    *,
    actor: Actor,
    name_fa: str,
    name_en: str,
    desc_fa: str = "",
    desc_en: str = "",
    price_tmn: int | None = None,
    price_usd: int | None = None,
    price_text_fa: str | None = None,
    price_text_en: str | None = None,
    duration_days: int,
    daily_post_limit: int,
    max_sources: int,
    max_channels: int,
    daily_test_limit: int = 3,
    is_free: bool = False,
    is_active: bool = True,
    sort_order: int = 0,
) -> PlanRecord:
    require_superadmin(actor)
    if not name_fa.strip() or not name_en.strip():
        raise ValidationFailed("bilingual plan names are required")
    if duration_days <= 0:
        raise ValidationFailed("duration_days must be positive")
    if min(daily_post_limit, max_sources, max_channels, daily_test_limit) < 0:
        raise ValidationFailed("plan quotas cannot be negative")
    if (price_tmn is not None and price_tmn < 0) or (price_usd is not None and price_usd < 0):
        raise ValidationFailed("plan prices cannot be negative")

    if is_free:
        await conn.execute(text("UPDATE plans SET is_free = FALSE WHERE is_free = TRUE"))

    row = (
        await conn.execute(
            text(
                "INSERT INTO plans("
                "name_fa, name_en, desc_fa, desc_en, price_tmn, price_usd, price_text_fa, price_text_en, "
                "duration_days, daily_post_limit, max_sources, max_channels, daily_test_limit, "
                "is_free, is_active, sort_order) "
                "VALUES (:nfa, :nen, :dfa, :den, :ptmn, :pusd, :tfa, :ten, :dur, :dpl, :ms, :mc, :dtl, "
                ":free, :act, :ord) RETURNING *"
            ),
            {
                "nfa": name_fa.strip(),
                "nen": name_en.strip(),
                "dfa": desc_fa,
                "den": desc_en,
                "ptmn": price_tmn,
                "pusd": price_usd,
                "tfa": price_text_fa,
                "ten": price_text_en,
                "dur": duration_days,
                "dpl": daily_post_limit,
                "ms": max_sources,
                "mc": max_channels,
                "dtl": daily_test_limit,
                "free": is_free,
                "act": is_active,
                "ord": sort_order,
            },
        )
    ).one()
    plan = _row_to_plan(row)
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id) "
            "VALUES (:a, 'create_plan', 'plan', :oid)"
        ),
        {"a": actor.user_id, "oid": str(plan.plan_id)},
    )
    return plan


async def get_plan(conn: AsyncConnection, plan_id: int) -> PlanRecord:
    row = (await conn.execute(text("SELECT * FROM plans WHERE plan_id = :p"), {"p": plan_id})).first()
    if row is None:
        raise NotFound("plan not found")
    return _row_to_plan(row)


async def list_plans(conn: AsyncConnection, *, active_only: bool = True) -> list[PlanRecord]:
    where = "WHERE is_active = TRUE" if active_only else ""
    rows = await conn.execute(
        text(f"SELECT * FROM plans {where} ORDER BY sort_order ASC, plan_id ASC")
    )
    return [_row_to_plan(r) for r in rows]


async def update_plan(
    conn: AsyncConnection,
    *,
    actor: Actor,
    plan_id: int,
    name_fa: str | None = None,
    name_en: str | None = None,
    desc_fa: str | None = None,
    desc_en: str | None = None,
    price_tmn: int | None = -1,
    price_usd: int | None = -1,
    price_text_fa: str | None = None,
    price_text_en: str | None = None,
    duration_days: int | None = None,
    daily_post_limit: int | None = None,
    max_sources: int | None = None,
    max_channels: int | None = None,
    daily_test_limit: int | None = None,
) -> PlanRecord:
    require_superadmin(actor)
    cur = await get_plan(conn, plan_id)
    new_tmn = cur.price_tmn if price_tmn == -1 else price_tmn
    new_usd = cur.price_usd if price_usd == -1 else price_usd
    row = (
        await conn.execute(
            text(
                "UPDATE plans SET "
                "name_fa = :nfa, name_en = :nen, desc_fa = :dfa, desc_en = :den, "
                "price_tmn = :ptmn, price_usd = :pusd, price_text_fa = :tfa, price_text_en = :ten, "
                "duration_days = :dur, daily_post_limit = :dpl, max_sources = :ms, "
                "max_channels = :mc, daily_test_limit = :dtl, updated_at = now() "
                "WHERE plan_id = :p RETURNING *"
            ),
            {
                "p": plan_id,
                "nfa": name_fa if name_fa is not None else cur.name_fa,
                "nen": name_en if name_en is not None else cur.name_en,
                "dfa": desc_fa if desc_fa is not None else cur.desc_fa,
                "den": desc_en if desc_en is not None else cur.desc_en,
                "ptmn": new_tmn,
                "pusd": new_usd,
                "tfa": price_text_fa if price_text_fa is not None else cur.price_text_fa,
                "ten": price_text_en if price_text_en is not None else cur.price_text_en,
                "dur": duration_days if duration_days is not None else cur.duration_days,
                "dpl": daily_post_limit if daily_post_limit is not None else cur.daily_post_limit,
                "ms": max_sources if max_sources is not None else cur.max_sources,
                "mc": max_channels if max_channels is not None else cur.max_channels,
                "dtl": daily_test_limit if daily_test_limit is not None else cur.daily_test_limit,
            },
        )
    ).one()
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id) "
            "VALUES (:a, 'update_plan', 'plan', :oid)"
        ),
        {"a": actor.user_id, "oid": str(plan_id)},
    )
    return _row_to_plan(row)


async def set_free_plan(
    conn: AsyncConnection,
    *,
    actor: Actor,
    plan_id: int,
) -> PlanRecord:
    require_superadmin(actor)
    await get_plan(conn, plan_id)
    await conn.execute(text("UPDATE plans SET is_free = FALSE WHERE is_free = TRUE"))
    row = (
        await conn.execute(
            text(
                "UPDATE plans SET is_free = TRUE, is_active = TRUE, updated_at = now() "
                "WHERE plan_id = :p RETURNING *"
            ),
            {"p": plan_id},
        )
    ).one()
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id) "
            "VALUES (:a, 'set_free_plan', 'plan', :oid)"
        ),
        {"a": actor.user_id, "oid": str(plan_id)},
    )
    return _row_to_plan(row)


async def delete_or_deactivate_plan(
    conn: AsyncConnection,
    *,
    actor: Actor,
    plan_id: int,
) -> Literal["deleted", "deactivated"]:
    """Safely delete an unreferenced plan or soft-deactivate it if referenced by users/payments/history."""
    require_superadmin(actor)
    await get_plan(conn, plan_id)
    refs = int(
        (
            await conn.execute(
                text(
                    "SELECT "
                    "  (SELECT count(*) FROM users WHERE plan_id = :p OR queued_plan_id = :p) + "
                    "  (SELECT count(*) FROM pay_requests WHERE plan_id = :p) + "
                    "  (SELECT count(*) FROM plan_changes WHERE old_plan_id = :p OR new_plan_id = :p)"
                ),
                {"p": plan_id},
            )
        ).scalar_one()
    )
    if refs > 0:
        await conn.execute(
            text("UPDATE plans SET is_active = FALSE, updated_at = now() WHERE plan_id = :p"),
            {"p": plan_id},
        )
        outcome: Literal["deleted", "deactivated"] = "deactivated"
    else:
        await conn.execute(text("DELETE FROM plans WHERE plan_id = :p"), {"p": plan_id})
        outcome = "deleted"
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id, reason) "
            "VALUES (:a, 'remove_plan', 'plan', :oid, :r)"
        ),
        {"a": actor.user_id, "oid": str(plan_id), "r": outcome},
    )
    return outcome


# ---- Subscriptions & Free Plan Claim ---------------------------------------------------------


async def claim_free_plan(
    conn: AsyncConnection,
    *,
    actor: Actor,
    idempotency_key: str,
    now: datetime | None = None,
) -> PlanRecord:
    """Atomically claim the free plan once per user lifetime."""
    require_active(actor)
    ts = now or datetime.now(UTC)

    existing_change = (
        await conn.execute(
            text("SELECT new_plan_id FROM plan_changes WHERE idempotency_key = :k"),
            {"k": idempotency_key},
        )
    ).first()
    if existing_change is not None:
        return await get_plan(conn, int(existing_change._mapping["new_plan_id"]))

    u_row = (
        await conn.execute(text("SELECT * FROM users WHERE user_id = :u FOR UPDATE"), {"u": actor.user_id})
    ).first()
    if u_row is None:
        raise NotFound("user not found")
    um = u_row._mapping
    if bool(um["free_plan_used"]):
        raise Conflict("free_plan_already_used")

    if um["plan_id"] is not None and um["plan_expires_at"] is not None and um["plan_expires_at"] > ts:
        cur_plan = await get_plan(conn, int(um["plan_id"]))
        if not cur_plan.is_free:
            raise Conflict("active_paid_plan_exists")

    free_row = (
        await conn.execute(
            text("SELECT * FROM plans WHERE is_free = TRUE AND is_active = TRUE ORDER BY plan_id ASC LIMIT 1")
        )
    ).first()
    if free_row is None:
        raise NotFound("no active free plan configured")
    free_plan = _row_to_plan(free_row)
    expires_at = ts + timedelta(days=free_plan.duration_days)
    new_role = Role.SUPERADMIN.value if str(um["role"]) == Role.SUPERADMIN.value else Role.ADMIN.value

    await conn.execute(
        text(
            "UPDATE users SET plan_id = :p, plan_expires_at = :exp, free_plan_used = TRUE, "
            "role = :r, updated_at = now() WHERE user_id = :u"
        ),
        {"p": free_plan.plan_id, "exp": expires_at, "r": new_role, "u": actor.user_id},
    )
    await conn.execute(
        text(
            "INSERT INTO plan_changes(idempotency_key, user_id, old_plan_id, new_plan_id, change_type, actor_id) "
            "VALUES (:k, :u, :old, :new, 'free_claim', :a)"
        ),
        {
            "k": idempotency_key,
            "u": actor.user_id,
            "old": um["plan_id"],
            "new": free_plan.plan_id,
            "a": actor.user_id,
        },
    )
    return free_plan


async def assign_or_revoke_user_plan(
    conn: AsyncConnection,
    *,
    actor: Actor,
    target_user_id: int,
    plan_id: int | None,
    idempotency_key: str,
    now: datetime | None = None,
) -> None:
    require_superadmin(actor)
    ts = now or datetime.now(UTC)
    u_row = (
        await conn.execute(text("SELECT * FROM users WHERE user_id = :u FOR UPDATE"), {"u": target_user_id})
    ).first()
    if u_row is None:
        raise NotFound("user not found")
    um = u_row._mapping
    old_plan_id = int(um["plan_id"]) if um["plan_id"] is not None else None

    if plan_id is None:
        new_role = Role.SUPERADMIN.value if str(um["role"]) == Role.SUPERADMIN.value else Role.USER.value
        await conn.execute(
            text(
                "UPDATE users SET plan_id = NULL, plan_expires_at = NULL, queued_plan_id = NULL, "
                "role = :r, updated_at = now() WHERE user_id = :u"
            ),
            {"r": new_role, "u": target_user_id},
        )
        change_type = "revoke"
    else:
        plan = await get_plan(conn, plan_id)
        expires_at = ts + timedelta(days=plan.duration_days)
        new_role = Role.SUPERADMIN.value if str(um["role"]) == Role.SUPERADMIN.value else Role.ADMIN.value
        await conn.execute(
            text(
                "UPDATE users SET plan_id = :p, plan_expires_at = :exp, role = :r, updated_at = now() "
                "WHERE user_id = :u"
            ),
            {"p": plan.plan_id, "exp": expires_at, "r": new_role, "u": target_user_id},
        )
        change_type = "admin_assign"

    await conn.execute(
        text(
            "INSERT INTO plan_changes(idempotency_key, user_id, old_plan_id, new_plan_id, change_type, actor_id) "
            "VALUES (:k, :u, :old, :new, :ct, :a) ON CONFLICT (idempotency_key) DO NOTHING"
        ),
        {
            "k": idempotency_key,
            "u": target_user_id,
            "old": old_plan_id,
            "new": plan_id,
            "ct": change_type,
            "a": actor.user_id,
        },
    )
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id, after_hash) "
            "VALUES (:a, :act, 'user', :oid, :ah)"
        ),
        {
            "a": actor.user_id,
            "act": change_type,
            "oid": str(target_user_id),
            "ah": f"plan_id={plan_id}",
        },
    )


async def queue_next_plan(
    conn: AsyncConnection,
    *,
    actor: Actor,
    next_plan_id: int,
) -> None:
    require_active(actor)
    plan = await get_plan(conn, next_plan_id)
    if not plan.is_active:
        raise Conflict("plan is not active")
    await conn.execute(
        text("UPDATE users SET queued_plan_id = :p, updated_at = now() WHERE user_id = :u"),
        {"p": next_plan_id, "u": actor.user_id},
    )


async def advance_expired_subscriptions(
    conn: AsyncConnection,
    *,
    now: datetime | None = None,
) -> int:
    """Activate queued plans or expire lapsed subscriptions."""
    ts = now or datetime.now(UTC)
    rows = (
        await conn.execute(
            text(
                "SELECT user_id, role, plan_id, queued_plan_id FROM users "
                "WHERE plan_id IS NOT NULL AND plan_expires_at IS NOT NULL AND plan_expires_at <= :now "
                "FOR UPDATE SKIP LOCKED"
            ),
            {"now": ts},
        )
    ).all()
    count = 0
    for row in rows:
        m = row._mapping
        uid = int(m["user_id"])
        queued = int(m["queued_plan_id"]) if m["queued_plan_id"] is not None else None
        if queued is not None:
            q_plan = await get_plan(conn, queued)
            if q_plan.is_active:
                exp = ts + timedelta(days=q_plan.duration_days)
                await conn.execute(
                    text(
                        "UPDATE users SET plan_id = :p, plan_expires_at = :exp, "
                        "queued_plan_id = NULL, updated_at = now() WHERE user_id = :u"
                    ),
                    {"p": q_plan.plan_id, "exp": exp, "u": uid},
                )
                count += 1
                continue
        new_role = Role.SUPERADMIN.value if str(m["role"]) == Role.SUPERADMIN.value else Role.USER.value
        await conn.execute(
            text(
                "UPDATE users SET plan_id = NULL, plan_expires_at = NULL, queued_plan_id = NULL, "
                "role = :r, updated_at = now() WHERE user_id = :u"
            ),
            {"r": new_role, "u": uid},
        )
        count += 1
    return count


# ---- Quotas & Daily Usage --------------------------------------------------------------------


async def get_daily_usage(conn: AsyncConnection, user_id: int) -> DailyUsageRecord:
    row = (
        await conn.execute(
            text("SELECT * FROM usage WHERE user_id = :u AND usage_date = CURRENT_DATE"),
            {"u": user_id},
        )
    ).first()
    if row is None:
        return DailyUsageRecord(
            user_id=user_id, posts_count=0, tests_count=0, ai_calls_count=0, ai_tokens_count=0
        )
    m = row._mapping
    return DailyUsageRecord(
        user_id=user_id,
        posts_count=int(m["posts_count"]),
        tests_count=int(m["tests_count"]),
        ai_calls_count=int(m["ai_calls_count"]),
        ai_tokens_count=int(m["ai_tokens_count"]),
    )


async def reserve_daily_quota(
    conn: AsyncConnection,
    *,
    user_id: int,
    counter: Literal["posts_count", "tests_count"],
    now: datetime | None = None,
    bootstrap_superadmins: Sequence[int] = (),
) -> int:
    """Atomically check active subscription limit and increment daily `posts_count` or `tests_count`."""
    ts = now or datetime.now(UTC)
    user = await get_user(conn, user_id, bootstrap_superadmins)
    if user.is_banned:
        raise QuotaExceeded("user is banned")
    if user.role is Role.SUPERADMIN:
        row = (
            await conn.execute(
                text(
                    f"INSERT INTO usage(user_id, usage_date, {counter}) VALUES (:u, CURRENT_DATE, 1) "
                    f"ON CONFLICT (user_id, usage_date) DO UPDATE SET {counter} = usage.{counter} + 1 "
                    f"RETURNING {counter}"
                ),
                {"u": user_id},
            )
        ).one()
        return int(row._mapping[counter])

    if user.plan_id is None or user.plan_expires_at is None or user.plan_expires_at <= ts:
        raise QuotaExceeded("subscription_expired_or_missing")
    plan = await get_plan(conn, user.plan_id)
    limit = plan.daily_post_limit if counter == "posts_count" else plan.daily_test_limit

    await conn.execute(
        text(
            "INSERT INTO usage(user_id, usage_date) VALUES (:u, CURRENT_DATE) "
            "ON CONFLICT (user_id, usage_date) DO NOTHING"
        ),
        {"u": user_id},
    )
    updated = (
        await conn.execute(
            text(
                f"UPDATE usage SET {counter} = {counter} + 1 "
                f"WHERE user_id = :u AND usage_date = CURRENT_DATE AND {counter} < :lim "
                f"RETURNING {counter}"
            ),
            {"u": user_id, "lim": limit},
        )
    ).first()
    if updated is None:
        raise QuotaExceeded(f"daily_{counter}_limit_exceeded")
    return int(updated._mapping[counter])


async def release_daily_quota(
    conn: AsyncConnection,
    *,
    user_id: int,
    counter: Literal["posts_count", "tests_count"],
) -> None:
    """Release a reserved daily quota slot when a pre-send failure occurs."""
    await conn.execute(
        text(
            f"UPDATE usage SET {counter} = GREATEST(0, {counter} - 1) "
            f"WHERE user_id = :u AND usage_date = CURRENT_DATE"
        ),
        {"u": user_id},
    )


# ---- Discounts -------------------------------------------------------------------------------


async def create_discount(
    conn: AsyncConnection,
    *,
    actor: Actor,
    code: str,
    percent: int,
    expires_at: datetime | None = None,
    max_uses: int = 0,
) -> DiscountRecord:
    require_superadmin(actor)
    norm_code = code.strip().upper()
    if not (3 <= len(norm_code) <= 32):
        raise ValidationFailed("discount code must be 3..32 characters")
    if not (1 <= percent <= 100):
        raise ValidationFailed("discount percent must be within 1..100")
    if max_uses < 0:
        raise ValidationFailed("max_uses cannot be negative")

    row = (
        await conn.execute(
            text(
                "INSERT INTO discounts(code, percent, expires_at, max_uses, is_active, created_by) "
                "VALUES (:c, :p, :exp, :max, TRUE, :u) RETURNING *"
            ),
            {
                "c": norm_code,
                "p": percent,
                "exp": expires_at,
                "max": max_uses,
                "u": actor.user_id,
            },
        )
    ).one()
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id) "
            "VALUES (:a, 'create_discount', 'discount', :oid)"
        ),
        {"a": actor.user_id, "oid": norm_code},
    )
    return _row_to_discount(row)


async def get_discount(conn: AsyncConnection, code: str) -> DiscountRecord:
    norm = code.strip().upper()
    row = (await conn.execute(text("SELECT * FROM discounts WHERE code = :c"), {"c": norm})).first()
    if row is None:
        raise NotFound("discount not found")
    return _row_to_discount(row)


async def list_discounts(conn: AsyncConnection, *, actor: Actor) -> list[DiscountRecord]:
    require_superadmin(actor)
    rows = await conn.execute(text("SELECT * FROM discounts ORDER BY created_at DESC, code ASC"))
    return [_row_to_discount(r) for r in rows]


async def toggle_discount(
    conn: AsyncConnection,
    *,
    actor: Actor,
    code: str,
    is_active: bool,
) -> DiscountRecord:
    require_superadmin(actor)
    norm = code.strip().upper()
    row = (
        await conn.execute(
            text("UPDATE discounts SET is_active = :act WHERE code = :c RETURNING *"),
            {"act": is_active, "c": norm},
        )
    ).first()
    if row is None:
        raise NotFound("discount not found")
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id, after_hash) "
            "VALUES (:a, 'toggle_discount', 'discount', :oid, :ah)"
        ),
        {"a": actor.user_id, "oid": norm, "ah": f"is_active={is_active}"},
    )
    return _row_to_discount(row)


async def delete_discount(
    conn: AsyncConnection,
    *,
    actor: Actor,
    code: str,
) -> Literal["deleted", "deactivated"]:
    require_superadmin(actor)
    norm = code.strip().upper()
    await get_discount(conn, norm)
    refs = int(
        (
            await conn.execute(
                text("SELECT count(*) FROM pay_requests WHERE discount_code = :c"),
                {"c": norm},
            )
        ).scalar_one()
    )
    if refs > 0:
        await conn.execute(
            text("UPDATE discounts SET is_active = FALSE WHERE code = :c"),
            {"c": norm},
        )
        return "deactivated"
    await conn.execute(text("DELETE FROM discounts WHERE code = :c"), {"c": norm})
    return "deleted"


# ---- Quoting, Payment Submission, Approval, Rejection & Refunds ------------------------------


async def _load_active_subscription(
    conn: AsyncConnection, user_id: int, now: datetime
) -> ActiveSubscription | None:
    u_row = (
        await conn.execute(
            text("SELECT plan_id, plan_expires_at FROM users WHERE user_id = :u"), {"u": user_id}
        )
    ).first()
    if u_row is None:
        return None
    m = u_row._mapping
    if m["plan_id"] is None or m["plan_expires_at"] is None or m["plan_expires_at"] <= now:
        return None
    plan = await get_plan(conn, int(m["plan_id"]))
    return ActiveSubscription(plan=plan.to_spec(), expires_at=m["plan_expires_at"])


async def quote_plan_for_user(
    conn: AsyncConnection,
    *,
    actor: Actor,
    plan_id: int,
    discount_code: str | None = None,
    use_wallet: bool = True,
    now: datetime | None = None,
) -> PlanQuote:
    require_active(actor)
    ts = now or datetime.now(UTC)
    user = await get_user(conn, actor.user_id)
    plan = await get_plan(conn, plan_id)
    if not plan.is_active or plan.is_free:
        raise Conflict("plan is not available for purchase")

    currency = currency_for_language(user.lang)
    current_sub = await _load_active_subscription(conn, actor.user_id, ts)

    disc_pct = 0
    norm_code: str | None = None
    if discount_code:
        disc = await get_discount(conn, discount_code)
        if not disc.is_usable_at(ts):
            raise Conflict("discount_expired_or_exhausted")
        disc_pct = disc.percent
        norm_code = disc.code

    wallet_bal = await get_balance(conn, actor.user_id, currency) if use_wallet else 0
    return build_quote(
        target=plan.to_spec(),
        currency=currency,
        now=ts,
        current=current_sub,
        discount_code=norm_code,
        discount_percent=disc_pct,
        spendable_wallet_balance=wallet_bal,
    )


async def submit_payment_request(
    conn: AsyncConnection,
    *,
    actor: Actor,
    quote: PlanQuote,
    receipt_type: ReceiptType,
    idempotency_key: str,
    receipt_file_id: str | None = None,
    receipt_text: str | None = None,
    notify_superadmin_ids: Sequence[int] = (),
) -> PaymentRequestRecord:
    """Create a pending payment request (maximum one open request per user) and enqueue superadmin notifications."""
    require_active(actor)
    if receipt_type not in ("text", "photo", "document", "wallet_full"):
        raise ValidationFailed("invalid receipt_type")
    if receipt_type == "text" and not (receipt_text and receipt_text.strip()):
        raise ValidationFailed("receipt_text is required for text receipts")
    if receipt_type in ("photo", "document") and not receipt_file_id:
        raise ValidationFailed("receipt_file_id is required for media receipts")

    existing_idem = (
        await conn.execute(
            text("SELECT * FROM pay_requests WHERE idempotency_key = :k"),
            {"k": idempotency_key},
        )
    ).first()
    if existing_idem is not None:
        return _row_to_payment(existing_idem)

    open_req = (
        await conn.execute(
            text(
                "SELECT payment_id FROM pay_requests "
                "WHERE user_id = :u AND status IN ('pending', 'processing')"
            ),
            {"u": actor.user_id},
        )
    ).first()
    if open_req is not None:
        raise Conflict("open_payment_request_already_exists")

    # Effective price after proration is stored as quoted_base_price so approval can detect tariff drift
    row = (
        await conn.execute(
            text(
                "INSERT INTO pay_requests("
                "idempotency_key, user_id, plan_id, currency, quoted_base_price, "
                "discount_code, discount_amount, wallet_reserved, payable_amount, "
                "receipt_type, receipt_file_id, receipt_text, status) "
                "VALUES (:k, :u, :p, :cur, :qbp, :dc, :da, :wr, :pa, :rt, :rf, :rtxt, 'pending') "
                "RETURNING *"
            ),
            {
                "k": idempotency_key,
                "u": actor.user_id,
                "p": quote.plan_id,
                "cur": quote.currency.value,
                "qbp": quote.base_price,
                "dc": quote.discount_code,
                "da": quote.discount_amount,
                "wr": quote.wallet_applied,
                "pa": quote.payable_amount,
                "rt": receipt_type,
                "rf": receipt_file_id,
                "rtxt": receipt_text,
            },
        )
    ).one()
    payment = _row_to_payment(row)

    for sa_id in notify_superadmin_ids:
        await outbox.enqueue(
            conn,
            idempotency_key=f"pay_submitted:{payment.payment_id}:sa:{sa_id}",
            recipient_id=sa_id,
            kind="payment_review",
            payload={
                "payment_id": payment.payment_id,
                "user_id": payment.user_id,
                "plan_id": payment.plan_id,
                "currency": payment.currency.value,
                "payable_amount": payment.payable_amount,
                "wallet_reserved": payment.wallet_reserved,
            },
        )
    return payment


async def approve_payment(
    conn: AsyncConnection,
    *,
    actor: Actor,
    payment_id: int,
    received_amount: int | None = None,
    now: datetime | None = None,
) -> PaymentRequestRecord:
    """Atomically approve a payment request: revalidate quote, consume discount, debit wallet, activate plan & reward referrer."""
    require_superadmin(actor)
    ts = now or datetime.now(UTC)

    # Check if already approved (idempotent replay)
    cur_row = (
        await conn.execute(
            text("SELECT * FROM pay_requests WHERE payment_id = :pid FOR UPDATE"),
            {"pid": payment_id},
        )
    ).first()
    if cur_row is None:
        raise NotFound("payment request not found")
    cur_pay = _row_to_payment(cur_row)
    if cur_pay.status == "approved":
        return cur_pay
    if cur_pay.status != "pending":
        raise Conflict(f"cannot approve payment in status {cur_pay.status}")

    claimed = (
        await conn.execute(
            text(
                "UPDATE pay_requests SET status = 'processing', reviewer_id = :sa, "
                "lease_until = :lease WHERE payment_id = :pid AND status = 'pending' RETURNING *"
            ),
            {"sa": actor.user_id, "lease": ts + timedelta(minutes=5), "pid": payment_id},
        )
    ).first()
    if claimed is None:
        raise Conflict("payment_already_claimed")
    pay = _row_to_payment(claimed)

    # Lock customer row
    u_row = (
        await conn.execute(
            text("SELECT * FROM users WHERE user_id = :u FOR UPDATE"),
            {"u": pay.user_id},
        )
    ).one()
    um = u_row._mapping

    # Revalidate live plan & tariff
    plan = await get_plan(conn, pay.plan_id)
    if not plan.is_active:
        raise QuoteDriftError("plan_no_longer_active")
    live_tariff = plan.to_spec().tariff_for(pay.currency)
    if live_tariff is None or live_tariff != pay.quoted_base_price:
        raise QuoteDriftError("plan_price_drifted")

    # Revalidate live upgrade/downgrade eligibility & compute new expiry
    current_sub = await _load_active_subscription(conn, pay.user_id, ts)
    if current_sub is None or current_sub.expires_at <= ts or current_sub.plan.is_free:
        change_type = "purchase"
        new_expires_at = ts + timedelta(days=plan.duration_days)
    elif current_sub.plan.plan_id == plan.plan_id:
        change_type = "renewal"
        new_expires_at = current_sub.expires_at + timedelta(days=plan.duration_days)
    else:
        if not is_eligible_upgrade(current_sub.plan, plan.to_spec(), pay.currency):
            raise QuoteDriftError("subscription_state_drifted_downgrade_forbidden")
        change_type = "upgrade"
        new_expires_at = ts + timedelta(days=plan.duration_days)

    # Atomically consume discount code if used
    if pay.discount_code is not None:
        d_row = (
            await conn.execute(
                text(
                    "UPDATE discounts SET used_count = used_count + 1 "
                    "WHERE code = :c AND is_active = TRUE "
                    "AND (expires_at IS NULL OR expires_at > :now) "
                    "AND (max_uses = 0 OR used_count < max_uses) "
                    "RETURNING percent"
                ),
                {"c": pay.discount_code, "now": ts},
            )
        ).first()
        if d_row is None:
            raise QuoteDriftError("discount_exhausted_or_expired")

    # Revalidate and debit wallet_reserved if > 0
    if pay.wallet_reserved > 0:
        live_bal = await get_balance(conn, pay.user_id, pay.currency)
        if live_bal < pay.wallet_reserved:
            raise QuoteDriftError("wallet_balance_drifted")
        await post_ledger_entry(
            conn,
            idempotency_key=f"pay:{pay.payment_id}:debit",
            user_id=pay.user_id,
            currency=pay.currency,
            delta=-pay.wallet_reserved,
            reason=LedgerReason.PAYMENT_DEBIT,
            reference=f"payment:{pay.payment_id}",
            actor_id=actor.user_id,
        )

    # Handle receipt amount verification / surplus credit
    actual_received = pay.payable_amount if received_amount is None else received_amount
    if actual_received < pay.payable_amount:
        raise QuoteDriftError("underpaid_receipt_amount")
    surplus = actual_received - pay.payable_amount
    if surplus > 0:
        await post_ledger_entry(
            conn,
            idempotency_key=f"pay:{pay.payment_id}:surplus",
            user_id=pay.user_id,
            currency=pay.currency,
            delta=surplus,
            reason=LedgerReason.PAYMENT_SURPLUS,
            reference=f"payment:{pay.payment_id}",
            actor_id=actor.user_id,
        )

    # Activate subscription & promote user -> admin
    new_role = Role.SUPERADMIN.value if str(um["role"]) == Role.SUPERADMIN.value else Role.ADMIN.value
    old_plan_id = int(um["plan_id"]) if um["plan_id"] is not None else None
    await conn.execute(
        text(
            "UPDATE users SET plan_id = :p, plan_expires_at = :exp, role = :r, updated_at = now() "
            "WHERE user_id = :u"
        ),
        {"p": plan.plan_id, "exp": new_expires_at, "r": new_role, "u": pay.user_id},
    )
    await conn.execute(
        text(
            "INSERT INTO plan_changes("
            "idempotency_key, user_id, old_plan_id, new_plan_id, change_type, "
            "wallet_debited, currency, payment_ref, actor_id) "
            "VALUES (:k, :u, :old, :new, :ct, :wd, :cur, :pref, :a)"
        ),
        {
            "k": f"pay:{pay.payment_id}:plan",
            "u": pay.user_id,
            "old": old_plan_id,
            "new": plan.plan_id,
            "ct": change_type,
            "wd": pay.wallet_reserved,
            "cur": pay.currency.value,
            "pref": str(pay.payment_id),
            "a": actor.user_id,
        },
    )

    # Grant referral reward if user was referred
    ref_row = (
        await conn.execute(
            text("SELECT * FROM referrals WHERE referred_user_id = :u FOR UPDATE"),
            {"u": pay.user_id},
        )
    ).first()
    if ref_row is not None:
        rm = ref_row._mapping
        referrer_id = int(rm["referrer_user_id"])
        econ = await get_referral_economics(conn)
        already_earned = (
            int(rm["total_reward_tmn"])
            if pay.currency is Currency.TMN
            else int(rm["total_reward_usd"])
        )
        net_settled = pay.payable_amount + pay.wallet_reserved
        reward = compute_referral_reward(
            eligible_paid_amount=net_settled,
            currency=pay.currency,
            economics=econ,
            already_earned_in_currency=already_earned,
        )
        if reward > 0:
            await post_ledger_entry(
                conn,
                idempotency_key=f"referral:pay:{pay.payment_id}",
                user_id=referrer_id,
                currency=pay.currency,
                delta=reward,
                reason=LedgerReason.REFERRAL_REWARD,
                reference=f"payment:{pay.payment_id}",
                actor_id=actor.user_id,
            )
            col = "total_reward_tmn" if pay.currency is Currency.TMN else "total_reward_usd"
            await conn.execute(
                text(
                    f"UPDATE referrals SET {col} = {col} + :rw, "
                    "rewarded_payments_count = rewarded_payments_count + 1 "
                    "WHERE referred_user_id = :u"
                ),
                {"rw": reward, "u": pay.user_id},
            )
            await outbox.enqueue(
                conn,
                idempotency_key=f"referral_reward_notify:pay:{pay.payment_id}",
                recipient_id=referrer_id,
                kind="referral_reward",
                payload={
                    "payment_id": pay.payment_id,
                    "referred_user_id": pay.user_id,
                    "reward": reward,
                    "currency": pay.currency.value,
                },
            )

    final_row = (
        await conn.execute(
            text(
                "UPDATE pay_requests SET status = 'approved', reviewer_id = :sa, "
                "reviewed_at = :now, applied_at = :now, lease_until = NULL "
                "WHERE payment_id = :pid RETURNING *"
            ),
            {"sa": actor.user_id, "now": ts, "pid": pay.payment_id},
        )
    ).one()
    await outbox.enqueue(
        conn,
        idempotency_key=f"pay_approved:{pay.payment_id}",
        recipient_id=pay.user_id,
        kind="payment_approved",
        payload={"payment_id": pay.payment_id, "plan_id": pay.plan_id},
    )
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id) "
            "VALUES (:a, 'approve_payment', 'payment', :oid)"
        ),
        {"a": actor.user_id, "oid": str(pay.payment_id)},
    )
    return _row_to_payment(final_row)


async def reject_payment(
    conn: AsyncConnection,
    *,
    actor: Actor,
    payment_id: int,
    reason: str = "",
    now: datetime | None = None,
) -> PaymentRequestRecord:
    require_superadmin(actor)
    ts = now or datetime.now(UTC)
    row = (
        await conn.execute(
            text(
                "UPDATE pay_requests SET status = 'rejected', reviewer_id = :sa, "
                "reviewed_at = :now, lease_until = NULL "
                "WHERE payment_id = :pid AND status IN ('pending', 'processing') RETURNING *"
            ),
            {"sa": actor.user_id, "now": ts, "pid": payment_id},
        )
    ).first()
    if row is None:
        raise Conflict("payment cannot be rejected in its current state")
    pay = _row_to_payment(row)
    await outbox.enqueue(
        conn,
        idempotency_key=f"pay_rejected:{pay.payment_id}",
        recipient_id=pay.user_id,
        kind="payment_rejected",
        payload={"payment_id": pay.payment_id, "reason": reason},
    )
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id, reason) "
            "VALUES (:a, 'reject_payment', 'payment', :oid, :r)"
        ),
        {"a": actor.user_id, "oid": str(pay.payment_id), "r": reason},
    )
    return pay


async def refund_payment(
    conn: AsyncConnection,
    *,
    actor: Actor,
    payment_id: int,
    reason: str = "",
    now: datetime | None = None,
) -> PaymentRequestRecord:
    """Audited idempotent refund workflow with referral reward clawback."""
    require_superadmin(actor)
    ts = now or datetime.now(UTC)

    cur_row = (
        await conn.execute(
            text("SELECT * FROM pay_requests WHERE payment_id = :pid FOR UPDATE"),
            {"pid": payment_id},
        )
    ).first()
    if cur_row is None:
        raise NotFound("payment request not found")
    cur_pay = _row_to_payment(cur_row)
    if cur_pay.status == "refunded":
        raise Conflict("already_refunded")
    if cur_pay.status != "approved":
        raise Conflict("only approved payments can be refunded")

    # Credit back the settled amount to the customer's wallet in the payment currency
    refund_amount = cur_pay.payable_amount + cur_pay.wallet_reserved
    if refund_amount > 0:
        await post_ledger_entry(
            conn,
            idempotency_key=f"refund:pay:{payment_id}:credit",
            user_id=cur_pay.user_id,
            currency=cur_pay.currency,
            delta=refund_amount,
            reason=LedgerReason.REFUND_CREDIT,
            reference=f"payment:{payment_id}",
            actor_id=actor.user_id,
        )

    # Revert customer's plan using prior plan from plan_changes
    pc_row = (
        await conn.execute(
            text("SELECT old_plan_id FROM plan_changes WHERE idempotency_key = :k"),
            {"k": f"pay:{payment_id}:plan"},
        )
    ).first()
    prev_plan_id = (
        int(pc_row._mapping["old_plan_id"])
        if pc_row is not None and pc_row._mapping["old_plan_id"] is not None
        else None
    )
    u_row = (
        await conn.execute(
            text("SELECT role FROM users WHERE user_id = :u FOR UPDATE"),
            {"u": cur_pay.user_id},
        )
    ).one()
    user_role = str(u_row._mapping["role"])
    if prev_plan_id is None:
        reverted_role = Role.SUPERADMIN.value if user_role == Role.SUPERADMIN.value else Role.USER.value
        await conn.execute(
            text(
                "UPDATE users SET plan_id = NULL, plan_expires_at = NULL, role = :r, updated_at = now() "
                "WHERE user_id = :u"
            ),
            {"r": reverted_role, "u": cur_pay.user_id},
        )
    else:
        await conn.execute(
            text("UPDATE users SET plan_id = :p, updated_at = now() WHERE user_id = :u"),
            {"p": prev_plan_id, "u": cur_pay.user_id},
        )

    await conn.execute(
        text(
            "INSERT INTO plan_changes("
            "idempotency_key, user_id, old_plan_id, new_plan_id, change_type, currency, payment_ref, actor_id) "
            "VALUES (:k, :u, :old, :new, 'refund', :cur, :pref, :a)"
        ),
        {
            "k": f"refund:pay:{payment_id}:plan",
            "u": cur_pay.user_id,
            "old": cur_pay.plan_id,
            "new": prev_plan_id,
            "cur": cur_pay.currency.value,
            "pref": str(payment_id),
            "a": actor.user_id,
        },
    )

    # Referral reward clawback (capped by referrer's available balance so balance never goes negative)
    ref_entry = (
        await conn.execute(
            text("SELECT user_id, delta FROM ledger_entries WHERE idempotency_key = :k"),
            {"k": f"referral:pay:{payment_id}"},
        )
    ).first()
    shortfall = 0
    if ref_entry is not None:
        referrer_id = int(ref_entry._mapping["user_id"])
        original_reward = int(ref_entry._mapping["delta"])
        ref_bal = await get_balance(conn, referrer_id, cur_pay.currency)
        clawback, shortfall = compute_referral_clawback(
            reward_granted=original_reward,
            referrer_wallet_balance=ref_bal,
        )
        if clawback > 0:
            await post_ledger_entry(
                conn,
                idempotency_key=f"refund:pay:{payment_id}:clawback",
                user_id=referrer_id,
                currency=cur_pay.currency,
                delta=-clawback,
                reason=LedgerReason.REFUND_CLAWBACK,
                reference=f"payment:{payment_id}",
                actor_id=actor.user_id,
            )
            col = "total_reward_tmn" if cur_pay.currency is Currency.TMN else "total_reward_usd"
            await conn.execute(
                text(
                    f"UPDATE referrals SET {col} = GREATEST(0, {col} - :cb) "
                    "WHERE referred_user_id = :u"
                ),
                {"cb": clawback, "u": cur_pay.user_id},
            )

    final_row = (
        await conn.execute(
            text(
                "UPDATE pay_requests SET status = 'refunded', reviewed_at = :now "
                "WHERE payment_id = :pid RETURNING *"
            ),
            {"now": ts, "pid": payment_id},
        )
    ).one()
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id, reason) "
            "VALUES (:a, 'refund_payment', 'payment', :oid, :r)"
        ),
        {
            "a": actor.user_id,
            "oid": str(payment_id),
            "r": f"{reason};clawback_shortfall={shortfall}".strip(";"),
        },
    )
    return _row_to_payment(final_row)


async def recover_stuck_payments(
    conn: AsyncConnection,
    *,
    now: datetime | None = None,
) -> int:
    """Revert expired `processing` payment leases to `pending` when no ledger/plan mutations were committed."""
    ts = now or datetime.now(UTC)
    res = await conn.execute(
        text(
            "UPDATE pay_requests SET status = 'pending', lease_until = NULL "
            "WHERE status = 'processing' AND lease_until IS NOT NULL AND lease_until <= :now"
        ),
        {"now": ts},
    )
    return int(res.rowcount or 0)
