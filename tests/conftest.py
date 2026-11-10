from __future__ import annotations

import os
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).parent.parent))

from opengenerate.infrastructure.db.engine import Database, to_plain_dsn
from opengenerate.migrations import upgrade

TEMPLATE_DB = "og_template"


def _with_db(url: str, name: str) -> str:
    return make_url(to_plain_dsn(url)).set(database=name).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def admin_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Server-level URL: CI service container via OG_TEST_DATABASE_URL, else a throw-away local cluster."""
    configured = os.environ.get("OG_TEST_DATABASE_URL")
    if configured:
        yield configured
        return
    import pixeltable_pgserver as pgserver

    server = pgserver.get_server(tmp_path_factory.mktemp("pg") / "data", cleanup_mode="delete")
    try:
        yield server.get_uri()
    finally:
        server.cleanup()


@pytest.fixture(scope="session")
def template_url(admin_url: str) -> str:
    with psycopg.connect(to_plain_dsn(admin_url), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEMPLATE_DB}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{TEMPLATE_DB}"')
    url = _with_db(admin_url, TEMPLATE_DB)
    upgrade(url)
    return url


@pytest.fixture
async def db_url(admin_url: str, template_url: str) -> AsyncIterator[str]:
    name = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(to_plain_dsn(admin_url), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE_DB}"')
    try:
        yield _with_db(admin_url, name)
    finally:
        with psycopg.connect(to_plain_dsn(admin_url), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
async def db(db_url: str) -> AsyncIterator[Database]:
    database = Database(db_url, pool_size=10, max_overflow=10)
    try:
        yield database
    finally:
        await database.dispose()


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def valid_env(fernet_key: str) -> dict[str, str]:
    return {
        "OG_DATABASE_URL": "postgresql://u:p@localhost:5432/og",
        "OG_BOT_TOKEN": "123456789:AAH_fake_token_value_0123456789abcdefghij",
        "OG_SUPERADMIN_IDS": "111, 222",
        "OG_ENCRYPTION_KEYS": fernet_key,
        "OG_ENVIRONMENT": "test",
    }


async def make_user(db: Database, user_id: int, role: str = "user") -> None:
    from sqlalchemy import text

    async with db.transaction() as conn:
        await conn.execute(
            text("INSERT INTO users(user_id, role) VALUES (:u, :r) ON CONFLICT DO NOTHING"),
            {"u": user_id, "r": role},
        )
