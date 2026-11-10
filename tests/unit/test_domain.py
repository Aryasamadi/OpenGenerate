from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from opengenerate.domain.errors import ValidationFailed
from opengenerate.domain.money import Currency, apply_percent_discount, mul_bps
from opengenerate.domain.retry import backoff_ceiling, full_jitter_delay
from opengenerate.domain.roles import Actor, Role, effective_role


@pytest.mark.parametrize(
    ("amount", "bps", "expected"),
    [(1000, 2000, 200), (1, 5000, 1), (1, 4999, 0), (999, 5000, 500), (0, 5000, 0), (12345, 10000, 12345)],
)
def test_mul_bps_rounds_half_up(amount: int, bps: int, expected: int) -> None:
    assert mul_bps(amount, bps) == expected


@pytest.mark.parametrize(("amount", "bps"), [(-1, 100), (10, -1), (10, 10001)])
def test_mul_bps_rejects_invalid(amount: int, bps: int) -> None:
    with pytest.raises(ValidationFailed):
        mul_bps(amount, bps)


@given(st.integers(0, 10**12), st.integers(0, 100))
def test_discount_never_exceeds_amount_and_sums_back(amount: int, percent: int) -> None:
    discount, payable = apply_percent_discount(amount, percent)
    assert 0 <= discount <= amount
    assert discount + payable == amount
    assert payable >= 0


@given(st.integers(0, 10**9), st.integers(0, 100), st.integers(0, 100))
def test_discount_is_monotonic_in_percent(amount: int, p1: int, p2: int) -> None:
    lo, hi = sorted((p1, p2))
    assert apply_percent_discount(amount, lo)[0] <= apply_percent_discount(amount, hi)[0]


def test_currency_rules() -> None:
    assert Currency.TMN.spendable and Currency.USD.spendable
    assert not Currency.LEGACY.spendable
    assert Currency.USD.exponent == 2 and Currency.TMN.exponent == 0


def test_backoff_is_bounded_and_jittered() -> None:
    assert backoff_ceiling(1, 2, 300) == 2
    assert backoff_ceiling(4, 2, 300) == 16
    assert backoff_ceiling(30, 2, 300) == 300
    assert full_jitter_delay(3, 2, 300, rng=lambda: 0.0) == 0.0
    assert full_jitter_delay(3, 2, 300, rng=lambda: 1.0) == 8.0


def test_effective_role_bootstrap_superadmin_wins() -> None:
    assert effective_role(1, Role.USER, {1}) is Role.SUPERADMIN
    assert effective_role(2, Role.ADMIN, {1}) is Role.ADMIN
    assert Actor(1, Role.SUPERADMIN).is_superadmin
