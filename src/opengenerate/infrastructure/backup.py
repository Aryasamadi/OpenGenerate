"""Verified logical backups and restore rehearsal (pg_dump/pg_restore).

Passwords are passed through PGPASSWORD, never argv. A backup is only considered valid once its
checksum matches and `pg_restore --list` can read it; a restore is only considered valid once
row counts and the schema revision match the manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy.engine import make_url

from opengenerate.infrastructure.db.engine import to_plain_dsn


class BackupError(Exception):
    pass


def find_pg_binary(name: str) -> str:
    candidates: list[Path] = []
    if os.environ.get("OG_PG_BIN_DIR"):
        candidates.append(Path(os.environ["OG_PG_BIN_DIR"]))
    on_path = shutil.which(name)
    if on_path:
        return on_path
    try:  # developer machines without a system PostgreSQL
        import pixeltable_pgserver

        candidates.append(Path(pixeltable_pgserver.__file__).parent / "pginstall" / "bin")
    except ImportError:
        pass
    for directory in candidates:
        for suffix in ("", ".exe"):
            path = directory / f"{name}{suffix}"
            if path.exists():
                return str(path)
    raise BackupError(f"{name} not found (install postgresql-client or set OG_PG_BIN_DIR)")


def _conn_args(url: str) -> tuple[list[str], dict[str, str]]:
    u = make_url(to_plain_dsn(url))
    args = ["-h", u.host or "localhost", "-p", str(u.port or 5432), "-U", u.username or "postgres"]
    env = {**os.environ}
    if u.password:
        env["PGPASSWORD"] = u.password
    return args, env


def _dbname(url: str) -> str:
    name = make_url(to_plain_dsn(url)).database
    if not name:
        raise BackupError("database name missing from URL")
    return name


def table_counts(url: str) -> dict[str, int]:
    with psycopg.connect(to_plain_dsn(url)) as conn:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"
            ).fetchall()
        ]
        return {n: int(conn.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0]) for n in names}  # type: ignore[index]


def schema_revision(url: str) -> str | None:
    with psycopg.connect(to_plain_dsn(url)) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    return None if row is None else str(row[0])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise BackupError(f"{Path(cmd[0]).name} failed: {result.stderr.strip()[:500]}")
    return result


@dataclass(frozen=True)
class BackupResult:
    path: Path
    manifest_path: Path
    manifest: dict[str, Any]


def create_backup(url: str, directory: Path) -> BackupResult:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{_dbname(url)}-{stamp}.dump"
    conn_args, env = _conn_args(url)
    _run(
        [find_pg_binary("pg_dump"), "-Fc", "-Z", "6", "--no-owner", "--no-privileges",
         *conn_args, "-d", _dbname(url), "-f", str(path)],
        env,
    )
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "database": _dbname(url),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "schema_revision": schema_revision(url),
        "table_counts": table_counts(url),
    }
    manifest_path = path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    verify_backup(path)
    return BackupResult(path, manifest_path, manifest)


def verify_backup(path: Path) -> dict[str, Any]:
    manifest_path = path.with_suffix(".manifest.json")
    if not path.exists() or not manifest_path.exists():
        raise BackupError("backup or manifest missing")
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    if _sha256(path) != manifest["sha256"]:
        raise BackupError("checksum mismatch")
    listing = _run([find_pg_binary("pg_restore"), "--list", str(path)], {**os.environ})
    if "TABLE DATA" not in listing.stdout:
        raise BackupError("backup contains no table data")
    return manifest


def restore_backup(path: Path, target_url: str, *, require_empty: bool = True) -> dict[str, Any]:
    """Restore into an existing, empty database and verify counts + schema revision."""
    manifest = verify_backup(path)
    if require_empty and table_counts(target_url):
        raise BackupError("target database is not empty; refusing to restore over existing data")
    conn_args, env = _conn_args(target_url)
    _run(
        [find_pg_binary("pg_restore"), "--no-owner", "--no-privileges", "--exit-on-error",
         *conn_args, "-d", _dbname(target_url), str(path)],
        env,
    )
    restored = table_counts(target_url)
    if restored != manifest["table_counts"]:
        raise BackupError("restored row counts differ from manifest")
    if schema_revision(target_url) != manifest["schema_revision"]:
        raise BackupError("restored schema revision differs from manifest")
    return manifest
