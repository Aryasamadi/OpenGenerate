"""Pure subscription, proration, tier comparison, and quote calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from opengenerate.domain.errors import Conflict, ValidationFailed
from opengenerate.domain.money import Currency, apply_percent_discount

ChangeKind = Literal["purchase", "renewal", "upgrade"]
SECONDS_PER_DAY = 86_400


class NegotiatedPlanError(Conflict):
    """Plan uses a text-only / negotiated tariff and cannot be auto-quoted numerically."""

    code = "negotiated_plan"


class DowngradeNotAllowedError(Conflict):
    """Paid downgrade while a higher-tier plan is active is forbidden."""

    code = "downgrade_not_allowed"


class QuoteDriftError(Conflict):
    """Live price, discount availability, or wallet balance changed after the quote was created."""

    code = "quote_drift"


@dataclass(frozen=True, slots=True)
class PlanSpec:
    plan_id: int
    duration_days: int
    daily_post_limit: int
    max_sources: int
    max_channels: int
    daily_test_limit: int
    price_tmn: int | None
    price_usd: int | None
    is_free: bool
    is_active: bool

    def tariff_for(self, currency: Currency) -> int | None:
        if currency is Currency.TMN:
            return self.price_tmn
        if currency is Currency.USD:
            return self.price_usd
        return None


@dataclass(frozen=True, slots=True)
class ActiveSubscription:
    plan: PlanSpec
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PlanQuote:
    plan_id: int
    currency: Currency
    change_kind: ChangeKind
    base_price: int
    proration_credit: int
    price_after_proration: int
    discount_code: str | None
    discount_percent: int
    discount_amount: int
    net_price: int
    wallet_applied: int
    payable_amount: int
    new_expires_at: datetime


def currency_for_language(lang: str | None) -> Currency:
    """Persian ('fa') uses Toman (TMN); English ('en') uses USD cents."""
    return Currency.TMN if (lang or "fa") == "fa" else Currency.USD


def compute_proration_credit(
    current: ActiveSubscription,
    currency: Currency,
    now: datetime,
) -> int:
    """Compute remaining-time value of `current` in minor units of `currency`."""
    if current.plan.is_free or current.expires_at <= now:
        return 0
    current_price = current.plan.tariff_for(currency)
    if not current_price or current_price <= 0:
        return 0
    total_seconds = current.plan.duration_days * SECONDS_PER_DAY
    if total_seconds <= 0:
        return 0
    remaining_seconds = max(0, int((current.expires_at - now).total_seconds()))
    if remaining_seconds >= total_seconds:
        return current_price
    return (current_price * remaining_seconds) // total_seconds


def is_eligible_upgrade(current: PlanSpec, target: PlanSpec, currency: Currency) -> bool:
    """Return True if `target` is a strictly higher tier than `current` in `currency`."""
    cur_price = current.tariff_for(currency)
    tgt_price = target.tariff_for(currency)
    if cur_price is None or tgt_price is None:
        return False
    if tgt_price > cur_price:
        return True
    if tgt_price < cur_price:
        return False
    # Equal price: only allow if strictly higher operational quotas
    return (
        target.daily_post_limit > current.daily_post_limit
        or target.max_channels > current.max_channels
        or target.max_sources > current.max_sources
    )


def build_quote(
    *,
    target: PlanSpec,
    currency: Currency,
    now: datetime,
    current: ActiveSubscription | None = None,
    discount_code: str | None = None,
    discount_percent: int = 0,
    spendable_wallet_balance: int = 0,
) -> PlanQuote:
    if not currency.spendable:
        raise ValidationFailed("cannot quote in non-spendable currency")
    if spendable_wallet_balance < 0:
        raise ValidationFailed("spendable wallet balance cannot be negative")
    base_price = target.tariff_for(currency)
    if base_price is None:
        raise NegotiatedPlanError("plan has no numeric tariff for the selected currency")

    proration_credit = 0
    if current is None or current.expires_at <= now or current.plan.is_free:
        change_kind: ChangeKind = "purchase"
        new_expires_at = now + timedelta(days=target.duration_days)
    elif current.plan.plan_id == target.plan_id:
        change_kind = "renewal"
        new_expires_at = current.expires_at + timedelta(days=target.duration_days)
    else:
        if not is_eligible_upgrade(current.plan, target, currency):
            raise DowngradeNotAllowedError("cannot downgrade while a higher plan is active")
        change_kind = "upgrade"
        proration_credit = min(base_price, compute_proration_credit(current, currency, now))
        new_expires_at = now + timedelta(days=target.duration_days)

    price_after_proration = max(0, base_price - proration_credit)
    discount_amount, net_price = apply_percent_discount(price_after_proration, discount_percent)
    wallet_applied = min(spendable_wallet_balance, net_price)
    payable_amount = net_price - wallet_applied

    return PlanQuote(
        plan_id=target.plan_id,
        currency=currency,
        change_kind=change_kind,
        base_price=base_price,
        proration_credit=proration_credit,
        price_after_proration=price_after_proration,
        discount_code=discount_code if discount_percent > 0 else None,
        discount_percent=discount_percent,
        discount_amount=discount_amount,
        net_price=net_price,
        wallet_applied=wallet_applied,
        payable_amount=payable_amount,
        new_expires_at=new_expires_at,
    )
