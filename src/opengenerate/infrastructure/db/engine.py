"""Async SQLAlchemy engine wrapper with explicit timeouts and a transaction helper."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

_SCHEME = re.compile(r"^postgres(?:ql)?(?:\+\w+)?://")


def to_asyncpg_url(url: str) -> str:
    return _SCHEME.sub("postgresql+asyncpg://", url, count=1)


def to_psycopg_url(url: str) -> str:
    return _SCHEME.sub("postgresql+psycopg://", url, count=1)


def to_plain_dsn(url: str) -> str:
    return _SCHEME.sub("postgresql://", url, count=1)


class Database:
    def __init__(
        self,
        url: str,
        *,
        pool_size: int = 5,
        max_overflow: int = 5,
        statement_timeout_ms: int = 30_000,
        lock_timeout_ms: int = 5_000,
        application_name: str = "opengenerate",
    ) -> None:
        self.url = url
        self.engine: AsyncEngine = create_async_engine(
            to_asyncpg_url(url),
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            connect_args={
                "server_settings": {
                    "application_name": application_name,
                    "statement_timeout": str(statement_timeout_ms),
                    "lock_timeout": str(lock_timeout_ms),
                    "timezone": "UTC",
                }
            },
        )

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """One atomic unit of work; commits on success and rolls back on any exception."""
        async with self.engine.begin() as conn:
            yield conn

    async def ping(self) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()
