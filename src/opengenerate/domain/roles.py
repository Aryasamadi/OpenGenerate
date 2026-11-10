"""Roles and the acting principal."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    USER = "user"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: int
    role: Role
    is_banned: bool = False

    @property
    def is_superadmin(self) -> bool:
        return self.role is Role.SUPERADMIN


def effective_role(user_id: int, stored_role: Role, bootstrap_superadmins: Collection[int]) -> Role:
    """Configured bootstrap superadmins always hold the superadmin role; others use the stored role."""
    return Role.SUPERADMIN if user_id in bootstrap_superadmins else stored_role
