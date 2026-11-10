"""Retry/backoff arithmetic (pure)."""

from __future__ import annotations

import random
from collections.abc import Callable

_default_rng = random.SystemRandom().random


def backoff_ceiling(attempt: int, base: float, cap: float) -> float:
    """Exponential ceiling for the given 1-based attempt number."""
    if attempt < 1:
        attempt = 1
    return float(min(cap, base * float(2 ** (attempt - 1))))


def full_jitter_delay(
    attempt: int, base: float, cap: float, rng: Callable[[], float] = _default_rng
) -> float:
    """Full-jitter exponential backoff: uniform in [0, ceiling]."""
    return rng() * backoff_ceiling(attempt, base, cap)
