from __future__ import annotations

import asyncio
from typing import Any

import pytest

from opengenerate.domain.errors import Conflict, QuotaExceeded, TransientError
from opengenerate.domain.money import Currency, LedgerReason
from opengenerate.domain.referrals import ReferralEconomics
from opengenerate.domain.roles import Actor, Role
from opengenerate.domain.subscriptions import QuoteDriftError
from opengenerate.infrastructure.db.billing_store import (
    approve_payment,
    claim_free_plan,
    create_discount,
    create_plan,
    delete_or_deactivate_plan,
    get_daily_usage,
    get_discount,
    quote_plan_for_user,
    refund_payment,
    reject_payment,
    release_daily_quota,
    reserve_daily_quota,
    submit_payment_request,
    update_plan,
)
from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.db.identity_store import (
    MandatoryChannelConfig,
    ban_or_unban_user,
    check_mandatory_membership,
    get_referral_dashboard,
    get_user,
    register_or_update_user,
    reset_daily_usage,
    reset_free_plan_eligibility,
    set_mandatory_channel_config,
    set_referral_economics,
    set_user_language,
)
from opengenerate.infrastructure.db.ledger import get_balance, post_ledger_entry
from opengenerate.infrastructure.db.support_store import (
    close_support_ticket,
    reply_to_support_message,
    send_user_support_message,
)


@pytest.mark.asyncio
async def test_identity_registration_referral_first_touch_and_superadmin_lockout_guard(
    db: Database,
) -> None:
    sa_actor = Actor(user_id=100, role=Role.SUPERADMIN)
    async with db.transaction() as conn:
        await register_or_update_user(
            conn, user_id=100, username="boss", bootstrap_superadmins={100}
        )
        await register_or_update_user(conn, user_id=200, username="referrer")
        await register_or_update_user(conn, user_id=201, username="second_referrer")

        # Self-referral is ignored
        self_ref = await register_or_update_user(
            conn, user_id=300, username="alice", referral_param="ref_300"
        )
        assert not self_ref.referral_attributed

        # First valid referral is attributed
        first_ref = await register_or_update_user(
            conn, user_id=300, username="alice", referral_param="ref_200"
        )
        assert first_ref.referral_attributed
        assert first_ref.referrer_id == 200

        # Second referral attempt on same user is ignored (first-touch immutable)
        second_ref = await register_or_update_user(
            conn, user_id=300, username="alice", referral_param="ref_201"
        )
        assert not second_ref.referral_attributed

        # Superadmin cannot ban self or another superadmin
        with pytest.raises(Conflict):
            await ban_or_unban_user(conn, actor=sa_actor, target_user_id=100, ban=True)

        await register_or_update_user(
            conn, user_id=101, username="co_boss", bootstrap_superadmins={100, 101}
        )
        with pytest.raises(Conflict):
            await ban_or_unban_user(
                conn,
                actor=sa_actor,
                target_user_id=101,
                ban=True,
                bootstrap_superadmins={100, 101},
            )


@pytest.mark.asyncio
async def test_free_plan_claim_quotas_and_safe_plan_deactivation(db: Database) -> None:
    sa = Actor(user_id=100, role=Role.SUPERADMIN)
    u1 = Actor(user_id=501, role=Role.USER)

    async with db.transaction() as conn:
        await register_or_update_user(conn, user_id=100, bootstrap_superadmins={100})
        await register_or_update_user(conn, user_id=501)

        unreferenced_plan = await create_plan(
            conn,
            actor=sa,
            name_fa="آزمایشی",
            name_en="Temp",
            price_tmn=100_000,
            price_usd=500,
            duration_days=7,
            daily_post_limit=2,
            max_sources=2,
            max_channels=1,
        )
        free_plan = await create_plan(
            conn,
            actor=sa,
            name_fa="رایگان",
            name_en="Free Trial",
            price_tmn=0,
            price_usd=0,
            duration_days=3,
            daily_post_limit=1,
            max_sources=2,
            max_channels=1,
            daily_test_limit=1,
            is_free=True,
        )

        # Unreferenced plan is physically deleted
        assert (
            await delete_or_deactivate_plan(conn, actor=sa, plan_id=unreferenced_plan.plan_id)
            == "deleted"
        )

        # User claims free plan once; second claim fails unless superadmin resets eligibility
        claimed = await claim_free_plan(conn, actor=u1, idempotency_key="claim:501:1")
        assert claimed.plan_id == free_plan.plan_id
        with pytest.raises(Conflict):
            await claim_free_plan(conn, actor=u1, idempotency_key="claim:501:2")

        # Referenced plan is soft-deactivated instead of deleted
        assert (
            await delete_or_deactivate_plan(conn, actor=sa, plan_id=free_plan.plan_id)
            == "deactivated"
        )

        # Quota reservation and pre-send release
        assert await reserve_daily_quota(conn, user_id=501, counter="posts_count") == 1
        with pytest.raises(QuotaExceeded):
            await reserve_daily_quota(conn, user_id=501, counter="posts_count")
        await release_daily_quota(conn, user_id=501, counter="posts_count")
        assert (await get_daily_usage(conn, 501)).posts_count == 0

        # Superadmin resets free plan eligibility & counters
        await reserve_daily_quota(conn, user_id=501, counter="posts_count")
        await reset_daily_usage(conn, actor=sa, target_user_id=501, reset_posts=True)
        assert (await get_daily_usage(conn, 501)).posts_count == 0
        reset_u = await reset_free_plan_eligibility(conn, actor=sa, target_user_id=501)
        assert not reset_u.free_plan_used


@pytest.mark.asyncio
async def test_concurrent_payment_approval_discount_cap_and_drift_detection(db: Database) -> None:
    sa1 = Actor(user_id=100, role=Role.SUPERADMIN)
    sa2 = Actor(user_id=101, role=Role.SUPERADMIN)
    buyer1 = Actor(user_id=601, role=Role.USER)
    buyer2 = Actor(user_id=602, role=Role.USER)

    async with db.transaction() as conn:
        for uid in (100, 101):
            await register_or_update_user(conn, user_id=uid, bootstrap_superadmins={100, 101})
        for uid in (601, 602):
            await register_or_update_user(conn, user_id=uid)
            await set_user_language(
                conn, actor=Actor(user_id=uid, role=Role.USER), lang="fa"
            )

        plan = await create_plan(
            conn,
            actor=sa1,
            name_fa="حرفه‌ای",
            name_en="Pro",
            price_tmn=500_000,
            price_usd=2_500,
            duration_days=30,
            daily_post_limit=20,
            max_sources=10,
            max_channels=5,
        )
        # Discount with max_uses=1
        await create_discount(conn, actor=sa1, code="ONCE20", percent=20, max_uses=1)

        # Give buyer1 100,000 TMN spendable wallet balance AND 999,999 LEGACY balance (must be ignored!)
        await post_ledger_entry(
            conn,
            idempotency_key="seed:601:tmn",
            user_id=601,
            currency=Currency.TMN,
            delta=100_000,
            reason=LedgerReason.ADMIN_ADJUSTMENT,
            actor_id=100,
        )
        await post_ledger_entry(
            conn,
            idempotency_key="seed:601:legacy",
            user_id=601,
            currency=Currency.LEGACY,
            delta=999_999,
            reason=LedgerReason.MIGRATION,
            actor_id=100,
        )

        q1 = await quote_plan_for_user(
            conn, actor=buyer1, plan_id=plan.plan_id, discount_code="ONCE20"
        )
        # 500,000 - 20% (100,000) = 400,000 net; minus 100,000 TMN wallet = 300,000 payable
        assert q1.wallet_applied == 100_000
        assert q1.payable_amount == 300_000

        q2 = await quote_plan_for_user(
            conn, actor=buyer2, plan_id=plan.plan_id, discount_code="ONCE20"
        )

        p1 = await submit_payment_request(
            conn,
            actor=buyer1,
            quote=q1,
            receipt_type="text",
            receipt_text="TRX-601",
            idempotency_key="sub:601",
            notify_superadmin_ids=[100, 101],
        )
        p2 = await submit_payment_request(
            conn,
            actor=buyer2,
            quote=q2,
            receipt_type="text",
            receipt_text="TRX-602",
            idempotency_key="sub:602",
        )

    # Concurrent double approval of p1 by two superadmins: both succeed idempotently or one wins cleanly,
    # and wallet is debited ONLY ONCE!
    async def _approve(sa: Actor, pid: int) -> Any:
        async with db.transaction() as c:
            return await approve_payment(c, actor=sa, payment_id=pid)

    res1, res2 = await asyncio.gather(_approve(sa1, p1.payment_id), _approve(sa2, p1.payment_id))
    assert res1.status == "approved"
    assert res2.status == "approved"

    async with db.transaction() as conn:
        assert await get_balance(conn, 601, Currency.TMN) == 0
        assert await get_balance(conn, 601, Currency.LEGACY) == 999_999
        disc = await get_discount(conn, "ONCE20")
        assert disc.used_count == 1

        # Approving p2 now fails with QuoteDriftError because ONCE20 (max_uses=1) is exhausted!
        with pytest.raises(QuoteDriftError):
            await approve_payment(conn, actor=sa1, payment_id=p2.payment_id)


@pytest.mark.asyncio
async def test_price_drift_and_wallet_drift_rejected_on_approval(db: Database) -> None:
    sa = Actor(user_id=100, role=Role.SUPERADMIN)
    buyer = Actor(user_id=701, role=Role.USER)

    async with db.transaction() as conn:
        await register_or_update_user(conn, user_id=100, bootstrap_superadmins={100})
        await register_or_update_user(conn, user_id=701)
        await set_user_language(conn, actor=buyer, lang="en")  # USD currency

        plan = await create_plan(
            conn,
            actor=sa,
            name_fa="پایه",
            name_en="Basic",
            price_tmn=200_000,
            price_usd=1_000,
            duration_days=30,
            daily_post_limit=5,
            max_sources=3,
            max_channels=1,
        )
        q = await quote_plan_for_user(conn, actor=buyer, plan_id=plan.plan_id)
        assert q.currency is Currency.USD
        pay = await submit_payment_request(
            conn,
            actor=buyer,
            quote=q,
            receipt_type="photo",
            receipt_file_id="file_123",
            idempotency_key="sub:701:1",
        )

        # Admin changes plan USD price before approving -> QuoteDriftError
        await update_plan(conn, actor=sa, plan_id=plan.plan_id, price_usd=1_200)
        with pytest.raises(QuoteDriftError):
            await approve_payment(conn, actor=sa, payment_id=pay.payment_id)
        await reject_payment(conn, actor=sa, payment_id=pay.payment_id, reason="price updated")


@pytest.mark.asyncio
async def test_referral_reward_and_idempotent_refund_with_partial_clawback(db: Database) -> None:
    sa = Actor(user_id=100, role=Role.SUPERADMIN)
    referrer = Actor(user_id=801, role=Role.USER)
    buyer = Actor(user_id=802, role=Role.USER)

    async with db.transaction() as conn:
        await register_or_update_user(conn, user_id=100, bootstrap_superadmins={100})
        await register_or_update_user(conn, user_id=801)
        await register_or_update_user(conn, user_id=802, referral_param="ref_801")
        await set_user_language(conn, actor=buyer, lang="fa")

        await set_referral_economics(
            conn,
            actor=sa,
            economics=ReferralEconomics(percent=25, cap_tmn=100_000, cap_usd=500),
        )
        plan = await create_plan(
            conn,
            actor=sa,
            name_fa="ویژه",
            name_en="VIP",
            price_tmn=600_000,
            price_usd=3_000,
            duration_days=30,
            daily_post_limit=50,
            max_sources=20,
            max_channels=10,
        )
        q = await quote_plan_for_user(conn, actor=buyer, plan_id=plan.plan_id)
        pay = await submit_payment_request(
            conn,
            actor=buyer,
            quote=q,
            receipt_type="text",
            receipt_text="VIP-RECEIPT",
            idempotency_key="sub:802:vip",
        )
        await approve_payment(conn, actor=sa, payment_id=pay.payment_id)

        # 25% of 600,000 = 150,000, capped at 100,000 TMN
        assert await get_balance(conn, 801, Currency.TMN) == 100_000
        dash = await get_referral_dashboard(conn, actor=referrer)
        assert dash.invited_count == 1
        assert dash.rewarded_payments_count == 1
        assert dash.earned_tmn == 100_000

        # Referrer spends 60,000 TMN of their reward before buyer is refunded
        await post_ledger_entry(
            conn,
            idempotency_key="spend:801",
            user_id=801,
            currency=Currency.TMN,
            delta=-60_000,
            reason=LedgerReason.ADMIN_ADJUSTMENT,
            actor_id=100,
        )
        assert await get_balance(conn, 801, Currency.TMN) == 40_000

        # Refund buyer: buyer receives 600,000 TMN refund credit; referrer is clawed back 40,000 TMN (never negative!)
        refunded = await refund_payment(
            conn, actor=sa, payment_id=pay.payment_id, reason="customer request"
        )
        assert refunded.status == "refunded"
        assert await get_balance(conn, 802, Currency.TMN) == 600_000
        assert await get_balance(conn, 801, Currency.TMN) == 0
        assert (await get_user(conn, 802)).plan_id is None

        # Repeated refund is rejected
        with pytest.raises(Conflict):
            await refund_payment(conn, actor=sa, payment_id=pay.payment_id)


@pytest.mark.asyncio
async def test_mandatory_membership_verification_and_transient_failsafe(db: Database) -> None:
    sa = Actor(user_id=100, role=Role.SUPERADMIN)
    user = Actor(user_id=901, role=Role.USER)

    async with db.transaction() as conn:
        await register_or_update_user(conn, user_id=100, bootstrap_superadmins={100})
        await register_or_update_user(conn, user_id=901)
        await set_mandatory_channel_config(
            conn,
            actor=sa,
            config=MandatoryChannelConfig(
                lang="fa",
                enabled=True,
                channel_id=-1001234567890,
                channel_username="OpenGenerateNews",
            ),
        )

        async def _mock_left(_chat: int | str, _uid: int) -> dict[str, Any]:
            return {"status": "left"}

        async def _mock_member(_chat: int | str, _uid: int) -> dict[str, Any]:
            return {"status": "member"}

        async def _mock_outage(_chat: int | str, _uid: int) -> dict[str, Any]:
            raise TransientError("telegram timeout")

        res_left = await check_mandatory_membership(
            conn, actor=user, lang="fa", get_chat_member=_mock_left
        )
        assert not res_left.allowed

        res_ok = await check_mandatory_membership(
            conn, actor=user, lang="fa", get_chat_member=_mock_member
        )
        assert res_ok.allowed and not res_ok.degraded_bypass

        # Transient network error fails safely without locking out the user
        res_degraded = await check_mandatory_membership(
            conn, actor=user, lang="fa", get_chat_member=_mock_outage
        )
        assert res_degraded.allowed and res_degraded.degraded_bypass


@pytest.mark.asyncio
async def test_durable_two_way_support_routing_across_restarts(db: Database) -> None:
    sa = Actor(user_id=100, role=Role.SUPERADMIN)
    user = Actor(user_id=950, role=Role.USER)

    async with db.transaction() as conn:
        await register_or_update_user(conn, user_id=100, bootstrap_superadmins={100})
        await register_or_update_user(conn, user_id=950)

        ticket = await send_user_support_message(
            conn,
            actor=user,
            kind="text",
            text_body="سلام، نیاز به راهنمایی دارم",
            user_msg_id=42,
            superadmin_forwarded_ids={100: 777001},
            notify_superadmin_ids=[100],
            idempotency_key="sup:950:42",
        )
        assert ticket.status == "open"

    # Simulate new transaction / process restart: superadmin replies to forwarded_msg_id=777001
    async with db.transaction() as conn:
        mapping = await reply_to_support_message(
            conn,
            actor=sa,
            replied_to_msg_id=777001,
            kind="text",
            text_body="درود، مشکل شما بررسی شد.",
            idempotency_key="sup_reply:100:777001",
        )
        assert mapping.user_id == 950
        assert mapping.ticket_id == ticket.ticket_id

        closed = await close_support_ticket(conn, actor=user)
        assert closed is not None and closed.status == "closed"
