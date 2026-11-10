"""Command line entrypoint: check-config | migrate | worker | backup | verify-backup | restore."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from opengenerate import __version__
from opengenerate.bootstrap.settings import ConfigError, Settings
from opengenerate.infrastructure import backup as bk
from opengenerate.infrastructure.backup import BackupError


def _settings() -> Settings:
    return Settings.load(os.environ)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opengenerate")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-config", help="validate configuration without starting anything")
    sub.add_parser("migrate", help="apply database migrations to head")
    sub.add_parser("worker", help="run the worker role")
    backup = sub.add_parser("backup", help="create a verified logical backup")
    backup.add_argument("--dir", type=Path, required=True)
    verify = sub.add_parser("verify-backup", help="verify checksum and readability of a backup")
    verify.add_argument("path", type=Path)
    restore = sub.add_parser("restore", help="restore a backup into an EMPTY database")
    restore.add_argument("path", type=Path)
    restore.add_argument("--target-url-env", required=True, help="env var holding the target DB URL")
    args = parser.parse_args(argv)

    try:
        if args.command == "check-config":
            _settings()
            print("configuration OK")
            return 0
        if args.command == "migrate":
            from opengenerate.migrations import current_revision, head_revision, upgrade

            url = _settings().database_url.get_secret_value()
            upgrade(url)
            print(f"schema at {current_revision(url)} (head {head_revision()})")
            return 0
        if args.command == "worker":
            from opengenerate.bootstrap.worker import worker_main

            asyncio.run(worker_main(_settings()))
            return 0
        if args.command == "backup":
            result = bk.create_backup(_settings().database_url.get_secret_value(), args.dir)
            print(f"backup verified: {result.path} sha256={result.manifest['sha256']}")
            return 0
        if args.command == "verify-backup":
            manifest = bk.verify_backup(args.path)
            print(f"backup OK: sha256={manifest['sha256']} revision={manifest['schema_revision']}")
            return 0
        if args.command == "restore":
            target = os.environ.get(args.target_url_env)
            if not target:
                print(f"environment variable {args.target_url_env} is not set", file=sys.stderr)
                return 2
            manifest = bk.restore_backup(args.path, target)
            print(f"restore verified: revision={manifest['schema_revision']}")
            return 0
    except (ConfigError, BackupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
