"""Versioned schema management (Alembic, programmatic)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from opengenerate.infrastructure.db.engine import to_psycopg_url

_HERE = Path(__file__).parent


def _config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_HERE))
    cfg.set_main_option("sqlalchemy.url", to_psycopg_url(url).replace("%", "%%"))
    return cfg


def head_revision() -> str:
    head = ScriptDirectory(str(_HERE)).get_current_head()
    assert head is not None
    return head


def current_revision(url: str) -> str | None:
    engine = create_engine(to_psycopg_url(url))
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(_config(url), revision)


def downgrade(url: str, revision: str) -> None:
    command.downgrade(_config(url), revision)
