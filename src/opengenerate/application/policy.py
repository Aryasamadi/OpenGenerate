"""Authorization policies. Every use-case calls these itself; UI visibility is never a control."""

from __future__ import annotations

from opengenerate.domain.errors import AccessDenied
from opengenerate.domain.roles import Actor, Role


def require_active(actor: Actor) -> None:
    if actor.is_banned:
        raise AccessDenied("account is restricted")


def require_superadmin(actor: Actor) -> None:
    require_active(actor)
    if actor.role is not Role.SUPERADMIN:
        raise AccessDenied("superadmin required")


def require_owner(
    actor: Actor, owner_id: int | None, *, allow_superadmin: bool = False
) -> None:
    """Object-level check. Missing and foreign objects raise the identical error."""
    require_active(actor)
    if allow_superadmin and actor.role is Role.SUPERADMIN:
        return
    if owner_id is None or owner_id != actor.user_id:
        raise AccessDenied("not found or not permitted")
