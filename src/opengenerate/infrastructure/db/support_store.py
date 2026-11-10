"""PostgreSQL persistence for Durable Two-Way Customer Support (`support` & `support_map`)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.application.policy import require_active, require_superadmin
from opengenerate.domain.errors import NotFound, ValidationFailed
from opengenerate.domain.roles import Actor
from opengenerate.infrastructure import outbox

SupportMessageKind = Literal["text", "photo", "document"]


@dataclass(frozen=True, slots=True)
class SupportTicketRecord:
    ticket_id: int
    user_id: int
    status: Literal["open", "closed"]
    opened_at: datetime
    closed_at: datetime | None
    last_message_at: datetime


@dataclass(frozen=True, slots=True)
class SupportMapRecord:
    superadmin_id: int
    forwarded_msg_id: int
    ticket_id: int
    user_id: int
    user_msg_id: int | None


def _row_to_ticket(row: Any) -> SupportTicketRecord:
    m = row._mapping
    return SupportTicketRecord(
        ticket_id=int(m["ticket_id"]),
        user_id=int(m["user_id"]),
        status=m["status"],
        opened_at=m["opened_at"],
        closed_at=m["closed_at"],
        last_message_at=m["last_message_at"],
    )


async def get_or_open_support_ticket(
    conn: AsyncConnection,
    *,
    user_id: int,
) -> SupportTicketRecord:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM support WHERE user_id = :u AND status = 'open' "
                "ORDER BY ticket_id DESC LIMIT 1 FOR UPDATE"
            ),
            {"u": user_id},
        )
    ).first()
    if row is not None:
        updated = (
            await conn.execute(
                text(
                    "UPDATE support SET last_message_at = now() "
                    "WHERE ticket_id = :t RETURNING *"
                ),
                {"t": int(row._mapping["ticket_id"])},
            )
        ).one()
        return _row_to_ticket(updated)

    created = (
        await conn.execute(
            text("INSERT INTO support(user_id, status) VALUES (:u, 'open') RETURNING *"),
            {"u": user_id},
        )
    ).one()
    return _row_to_ticket(created)


async def send_user_support_message(
    conn: AsyncConnection,
    *,
    actor: Actor,
    kind: SupportMessageKind,
    idempotency_key: str,
    text_body: str | None = None,
    file_id: str | None = None,
    user_msg_id: int | None = None,
    superadmin_forwarded_ids: Mapping[int, int] | None = None,
    notify_superadmin_ids: Sequence[int] = (),
) -> SupportTicketRecord:
    """Open or update a support ticket, persist durable `support_map` routing, and enqueue superadmin notifications."""
    require_active(actor)
    if kind not in ("text", "photo", "document"):
        raise ValidationFailed("invalid support message kind")
    if kind == "text" and not (text_body and text_body.strip()):
        raise ValidationFailed("text_body is required for text support messages")
    if kind in ("photo", "document") and not file_id:
        raise ValidationFailed("file_id is required for media support messages")

    ticket = await get_or_open_support_ticket(conn, user_id=actor.user_id)

    if superadmin_forwarded_ids:
        for sa_id, fwd_msg_id in superadmin_forwarded_ids.items():
            await record_support_forward_mapping(
                conn,
                superadmin_id=sa_id,
                forwarded_msg_id=fwd_msg_id,
                ticket_id=ticket.ticket_id,
                user_id=actor.user_id,
                user_msg_id=user_msg_id,
            )

    for sa_id in notify_superadmin_ids:
        await outbox.enqueue(
            conn,
            idempotency_key=f"{idempotency_key}:sa:{sa_id}",
            recipient_id=sa_id,
            kind=f"support_{kind}",
            payload={
                "ticket_id": ticket.ticket_id,
                "user_id": actor.user_id,
                "user_msg_id": user_msg_id,
                "text": text_body,
                "file_id": file_id,
            },
        )
    return ticket


async def record_support_forward_mapping(
    conn: AsyncConnection,
    *,
    superadmin_id: int,
    forwarded_msg_id: int,
    ticket_id: int,
    user_id: int,
    user_msg_id: int | None = None,
) -> SupportMapRecord:
    """Persist `(superadmin_id, forwarded_msg_id) -> (ticket_id, user_id)` in PostgreSQL so replies survive restarts."""
    await conn.execute(
        text(
            "INSERT INTO support_map(superadmin_id, forwarded_msg_id, ticket_id, user_id, user_msg_id) "
            "VALUES (:sa, :fm, :t, :u, :um) "
            "ON CONFLICT (superadmin_id, forwarded_msg_id) DO UPDATE SET "
            "ticket_id = EXCLUDED.ticket_id, user_id = EXCLUDED.user_id, user_msg_id = EXCLUDED.user_msg_id"
        ),
        {
            "sa": superadmin_id,
            "fm": forwarded_msg_id,
            "t": ticket_id,
            "u": user_id,
            "um": user_msg_id,
        },
    )
    return SupportMapRecord(
        superadmin_id=superadmin_id,
        forwarded_msg_id=forwarded_msg_id,
        ticket_id=ticket_id,
        user_id=user_id,
        user_msg_id=user_msg_id,
    )


async def reply_to_support_message(
    conn: AsyncConnection,
    *,
    actor: Actor,
    replied_to_msg_id: int,
    kind: SupportMessageKind,
    idempotency_key: str,
    text_body: str | None = None,
    file_id: str | None = None,
) -> SupportMapRecord:
    """Route a superadmin's reply back to the customer via the durable `support_map` table."""
    require_superadmin(actor)
    if kind == "text" and not (text_body and text_body.strip()):
        raise ValidationFailed("text_body is required for text support reply")
    if kind in ("photo", "document") and not file_id:
        raise ValidationFailed("file_id is required for media support reply")

    row = (
        await conn.execute(
            text(
                "SELECT * FROM support_map "
                "WHERE superadmin_id = :sa AND forwarded_msg_id = :fm"
            ),
            {"sa": actor.user_id, "fm": replied_to_msg_id},
        )
    ).first()
    if row is None:
        raise NotFound("support mapping not found for replied message")
    m = row._mapping
    mapping = SupportMapRecord(
        superadmin_id=int(m["superadmin_id"]),
        forwarded_msg_id=int(m["forwarded_msg_id"]),
        ticket_id=int(m["ticket_id"]),
        user_id=int(m["user_id"]),
        user_msg_id=int(m["user_msg_id"]) if m["user_msg_id"] is not None else None,
    )

    await conn.execute(
        text("UPDATE support SET last_message_at = now() WHERE ticket_id = :t"),
        {"t": mapping.ticket_id},
    )
    await outbox.enqueue(
        conn,
        idempotency_key=idempotency_key,
        recipient_id=mapping.user_id,
        kind=f"support_reply_{kind}",
        payload={
            "ticket_id": mapping.ticket_id,
            "reply_to_user_msg_id": mapping.user_msg_id,
            "text": text_body,
            "file_id": file_id,
        },
    )
    await conn.execute(
        text(
            "INSERT INTO audit_log(actor_id, action, object_type, object_id) "
            "VALUES (:a, 'support_reply', 'support', :oid)"
        ),
        {"a": actor.user_id, "oid": str(mapping.ticket_id)},
    )
    return mapping


async def close_support_ticket(
    conn: AsyncConnection,
    *,
    actor: Actor,
    ticket_id: int | None = None,
) -> SupportTicketRecord | None:
    require_active(actor)
    if ticket_id is not None:
        row = (
            await conn.execute(
                text(
                    "UPDATE support SET status = 'closed', closed_at = now() "
                    "WHERE ticket_id = :t AND (user_id = :u OR :is_sa) RETURNING *"
                ),
                {"t": ticket_id, "u": actor.user_id, "is_sa": actor.is_superadmin},
            )
        ).first()
    else:
        row = (
            await conn.execute(
                text(
                    "UPDATE support SET status = 'closed', closed_at = now() "
                    "WHERE user_id = :u AND status = 'open' RETURNING *"
                ),
                {"u": actor.user_id},
            )
        ).first()
    return _row_to_ticket(row) if row is not None else None
