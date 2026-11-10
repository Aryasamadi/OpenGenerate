"""Single-scheduler authority via a PostgreSQL session-level advisory lock."""

from __future__ import annotations

import asyncpg

from opengenerate.infrastructure.db.engine import to_plain_dsn


class AdvisoryLockLeader:
    """Holds `pg_try_advisory_lock(key)` on a dedicated connection.

    The lock is released by the server if the connection dies, so `check()` detecting a dead
    connection means leadership is lost and scheduling must stop immediately.
    """

    def __init__(self, url: str, key: int) -> None:
        self._dsn = to_plain_dsn(url)
        self._key = key
        self._conn: asyncpg.Connection | None = None
        self.is_leader = False

    async def try_acquire(self) -> bool:
        if self.is_leader:
            return True
        conn = await asyncpg.connect(self._dsn, server_settings={"application_name": "og-leader"})
        got = bool(await conn.fetchval("SELECT pg_try_advisory_lock($1)", self._key))
        if got:
            self._conn, self.is_leader = conn, True
        else:
            await conn.close()
        return got

    async def check(self) -> bool:
        """Verify the lock-holding session is still alive."""
        if not self.is_leader or self._conn is None:
            return False
        try:
            await self._conn.fetchval("SELECT 1", timeout=5)
        except (asyncpg.PostgresError, OSError, TimeoutError, asyncpg.InterfaceError):
            self.is_leader = False
            self._conn.terminate()
            self._conn = None
        return self.is_leader

    async def release(self) -> None:
        conn, self._conn, self.is_leader = self._conn, None, False
        if conn is not None:
            try:
                await conn.fetchval("SELECT pg_advisory_unlock($1)", self._key)
            finally:
                await conn.close()
