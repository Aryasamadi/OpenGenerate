"""Health checks with explicit three-state semantics: ok | degraded | unknown.

`unknown` means telemetry is missing; it must never be presented as a healthy zero.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text

from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.db.ledger import reconcile_wallets
from opengenerate.infrastructure.outbox import stats as outbox_stats
from opengenerate.infrastructure.ratelimit import heartbeat_age_seconds

Status = Literal["ok", "degraded", "unknown"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: Status
    detail: str = ""


Check = Callable[[], Awaitable[CheckResult]]


def overall(results: dict[str, CheckResult]) -> Status:
    statuses = {r.status for r in results.values()}
    if "degraded" in statuses:
        return "degraded"
    if "unknown" in statuses:
        return "unknown"
    return "ok"


def db_check(db: Database) -> Check:
    async def run() -> CheckResult:
        try:
            await db.ping()
        except Exception as exc:
            return CheckResult("degraded", f"database unreachable ({type(exc).__name__})")
        return CheckResult("ok")

    return run


def migration_check(db: Database, expected_head: str) -> Check:
    async def run() -> CheckResult:
        try:
            async with db.engine.connect() as conn:
                row = (await conn.execute(text("SELECT version_num FROM alembic_version"))).first()
        except Exception:
            return CheckResult("unknown", "schema version unavailable")
        if row is None or row.version_num != expected_head:
            return CheckResult("degraded", f"schema at {row.version_num if row else 'none'}, expected {expected_head}")
        return CheckResult("ok", expected_head)

    return run


def heartbeat_check(db: Database, name: str, max_age: float) -> Check:
    async def run() -> CheckResult:
        try:
            age = await heartbeat_age_seconds(db, name)
        except Exception:
            return CheckResult("unknown", "heartbeat unavailable")
        if age is None:
            return CheckResult("unknown", "no heartbeat recorded")
        if age > max_age:
            return CheckResult("degraded", f"last heartbeat {age:.0f}s ago (limit {max_age:.0f}s)")
        return CheckResult("ok", f"{age:.1f}s ago")

    return run


def outbox_check(db: Database, dead_letter_limit: int = 0) -> Check:
    async def run() -> CheckResult:
        try:
            async with db.engine.connect() as conn:
                s = await outbox_stats(conn)
        except Exception:
            return CheckResult("unknown", "outbox stats unavailable")
        if s.dead_letter > dead_letter_limit:
            return CheckResult("degraded", f"{s.dead_letter} dead-lettered notifications")
        return CheckResult("ok", f"pending={s.pending} sending={s.sending} dead_letter={s.dead_letter}")

    return run


def ledger_check(db: Database) -> Check:
    async def run() -> CheckResult:
        try:
            async with db.engine.connect() as conn:
                mismatches = await reconcile_wallets(conn)
        except Exception:
            return CheckResult("unknown", "ledger reconciliation unavailable")
        if mismatches:
            return CheckResult("degraded", f"{len(mismatches)} wallet(s) differ from ledger")
        return CheckResult("ok")

    return run
