from __future__ import annotations

import pytest

from opengenerate.application.policy import require_active, require_owner, require_superadmin
from opengenerate.domain.errors import AccessDenied
from opengenerate.domain.roles import Actor, Role

USER = Actor(10, Role.USER)
ADMIN = Actor(20, Role.ADMIN)
SUPER = Actor(30, Role.SUPERADMIN)
BANNED_SUPER = Actor(31, Role.SUPERADMIN, is_banned=True)


def test_superadmin_required() -> None:
    require_superadmin(SUPER)
    for actor in (USER, ADMIN, BANNED_SUPER):
        with pytest.raises(AccessDenied):
            require_superadmin(actor)


def test_owner_check_is_strict_by_default() -> None:
    require_owner(ADMIN, 20)
    with pytest.raises(AccessDenied):
        require_owner(ADMIN, 21)
    with pytest.raises(AccessDenied):
        require_owner(SUPER, 21)  # superadmin has no implicit ownership


def test_owner_check_superadmin_override_is_explicit() -> None:
    require_owner(SUPER, 21, allow_superadmin=True)
    with pytest.raises(AccessDenied):
        require_owner(BANNED_SUPER, 31, allow_superadmin=True)


def test_missing_and_foreign_objects_are_indistinguishable() -> None:
    with pytest.raises(AccessDenied) as missing:
        require_owner(USER, None)
    with pytest.raises(AccessDenied) as foreign:
        require_owner(USER, 999)
    assert str(missing.value) == str(foreign.value)


def test_banned_users_are_rejected() -> None:
    with pytest.raises(AccessDenied):
        require_active(Actor(1, Role.USER, is_banned=True))
