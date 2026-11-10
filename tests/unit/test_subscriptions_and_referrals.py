from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from opengenerate.domain.errors import ValidationFailed
from opengenerate.domain.money import Currency
from opengenerate.domain.referrals import (
    ReferralEconomics,
    compute_referral_clawback,
    compute_referral_reward,
)
from opengenerate.domain.subscriptions import (
    ActiveSubscription,
    DowngradeNotAllowedError,
    NegotiatedPlanError,
    PlanSpec,
    build_quote,
    compute_proration_credit,
    currency_for_language,
    is_eligible_upgrade,
)


def _plan(
    plan_id: int = 1,
    *,
    duration_days: int = 30,
    price_tmn: int | None = 300_000,
    price_usd: int | None = 1_500,
    daily_post_limit: int = 10,
    max_sources: int = 5,
    max_channels: int = 2,
    is_free: bool = False,
) -> PlanSpec:
    return PlanSpec(
        plan_id=plan_id,
        duration_days=duration_days,
        daily_post_limit=daily_post_limit,
        max_sources=max_sources,
        max_channels=max_channels,
        daily_test_limit=3,
        price_tmn=price_tmn,
        price_usd=price_usd,
        is_free=is_free,
        is_active=True,
    )


def test_currency_for_language() -> None:
    assert currency_for_language("fa") is Currency.TMN
    assert currency_for_language(None) is Currency.TMN
    assert currency_for_language("en") is Currency.USD


def test_proration_credit_and_upgrade_quote() -> None:
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    basic = _plan(1, duration_days=30, price_tmn=300_000, price_usd=1_500)
    pro = _plan(2, duration_days=30, price_tmn=900_000, price_usd=4_500, daily_post_limit=30)

    # Half of the 30-day plan remains -> 150,000 TMN credit
    active = ActiveSubscription(plan=basic, expires_at=now + timedelta(days=15))
    assert compute_proration_credit(active, Currency.TMN, now) == 150_000

    quote = build_quote(
        target=pro,
        currency=Currency.TMN,
        now=now,
        current=active,
        discount_code="OFF10",
        discount_percent=10,
        spendable_wallet_balance=200_000,
    )
    assert quote.change_kind == "upgrade"
    assert quote.base_price == 900_000
    assert quote.proration_credit == 150_000
    assert quote.price_after_proration == 750_000
    assert quote.discount_amount == 75_000
    assert quote.net_price == 675_000
    assert quote.wallet_applied == 200_000
    assert quote.payable_amount == 475_000


def test_downgrade_rejected_and_same_plan_renews() -> None:
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    basic = _plan(1, price_tmn=300_000)
    pro = _plan(2, price_tmn=900_000)
    active_pro = ActiveSubscription(plan=pro, expires_at=now + timedelta(days=10))

    assert not is_eligible_upgrade(pro, basic, Currency.TMN)
    with pytest.raises(DowngradeNotAllowedError):
        build_quote(target=basic, currency=Currency.TMN, now=now, current=active_pro)

    renewal = build_quote(target=pro, currency=Currency.TMN, now=now, current=active_pro)
    assert renewal.change_kind == "renewal"
    assert renewal.proration_credit == 0
    assert renewal.new_expires_at == active_pro.expires_at + timedelta(days=30)


def test_negotiated_plan_and_non_spendable_currency_rejected() -> None:
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    custom = _plan(9, price_tmn=None, price_usd=None)
    with pytest.raises(NegotiatedPlanError):
        build_quote(target=custom, currency=Currency.TMN, now=now)
    with pytest.raises(ValidationFailed):
        build_quote(target=_plan(1), currency=Currency.LEGACY, now=now)


def test_referral_economics_bounds_and_clawback() -> None:
    with pytest.raises(ValidationFailed):
        ReferralEconomics(percent=51)
    with pytest.raises(ValidationFailed):
        ReferralEconomics(percent=-1)

    econ = ReferralEconomics(percent=20, cap_tmn=100_000, cap_usd=500)
    # 20% of 300,000 = 60,000 (under 100,000 cap)
    assert (
        compute_referral_reward(
            eligible_paid_amount=300_000,
            currency=Currency.TMN,
            economics=econ,
            already_earned_in_currency=0,
        )
        == 60_000
    )
    # 20% of 800,000 = 160,000 -> capped at 100,000
    assert (
        compute_referral_reward(
            eligible_paid_amount=800_000,
            currency=Currency.TMN,
            economics=econ,
            already_earned_in_currency=0,
        )
        == 100_000
    )

    # Partial referrer balance clawback never drives balance negative
    clawback, shortfall = compute_referral_clawback(
        reward_granted=100_000, referrer_wallet_balance=35_000
    )
    assert clawback == 35_000
    assert shortfall == 65_000
