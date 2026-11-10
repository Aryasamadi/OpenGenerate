#!/usr/bin/env python3
"""CLI tool to inspect, migrate, and reconcile a legacy SQLite database into PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from opengenerate.infrastructure.crypto import FieldCipher
from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.legacy_migrator import (
    inspect_legacy_sqlite,
    migrate_sqlite_to_postgres,
)
from opengenerate.migrations import upgrade


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="OpenGenerate Legacy SQLite -> PostgreSQL ETL & 10-Gate Reconciliation Tool"
    )
    p.add_argument("--sqlite-path", required=True, help="Path to legacy SQLite .db snapshot")
    p.add_argument("--pg-dsn", help="Target PostgreSQL DSN (required unless --inspect-only)")
    p.add_argument(
        "--encryption-key",
        help="Target Fernet encryption key (required unless --inspect-only)",
    )
    p.add_argument(
        "--legacy-encryption-key",
        default=None,
        help="Optional legacy Fernet key if legacy SQLite already encrypted API keys",
    )
    p.add_argument(
        "--inspect-only",
        action="store_true",
        help="Only inspect the SQLite snapshot and print JSON report without writing to PostgreSQL",
    )
    p.add_argument(
        "--allow-nonempty",
        action="store_true",
        help="Allow running against a non-empty target PostgreSQL database (for rehearsal only)",
    )
    p.add_argument(
        "--report-out",
        default="migration-report.md",
        help="Output path for the Markdown reconciliation report",
    )
    return p


async def _run_migration_async(
    sqlite_path_str: str,
    pg_dsn: str,
    encryption_key: str,
    legacy_encryption_key: str | None,
    allow_nonempty: bool,
) -> tuple[str, bool]:
    upgrade(pg_dsn)
    target_keyring = FieldCipher([k.strip() for k in encryption_key.split(",") if k.strip()])
    legacy_keyring = (
        FieldCipher([k.strip() for k in legacy_encryption_key.split(",") if k.strip()])
        if legacy_encryption_key
        else None
    )

    db = Database(pg_dsn)
    try:
        async with db.transaction() as conn:
            res = await migrate_sqlite_to_postgres(
                sqlite_path_str,
                conn,
                target_keyring=target_keyring,
                legacy_keyring=legacy_keyring,
                allow_nonempty=allow_nonempty,
            )
        md = res.render_markdown_report()
        ok = bool(res.reconciliation and res.reconciliation.all_passed)
        return md, ok
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    sqlite_path = Path(args.sqlite_path).resolve()
    if not sqlite_path.is_file():
        sys.stderr.write(f"ERROR: SQLite file not found: {sqlite_path}\n")
        return 2

    if args.inspect_only:
        rep = inspect_legacy_sqlite(sqlite_path)
        sys.stdout.write(
            json.dumps(
                {
                    "sqlite_path": rep.sqlite_path,
                    "file_sha256": rep.file_sha256,
                    "table_counts": rep.table_counts,
                    "missing_tables": rep.missing_tables,
                    "in_flight_publications": rep.in_flight_publications,
                    "processing_payments": rep.processing_payments,
                    "untagged_wallet_rows": rep.untagged_wallet_rows,
                    "anomalies": rep.anomalies,
                },
                indent=2,
            )
            + "\n"
        )
        return 0

    if not args.pg_dsn or not args.encryption_key:
        sys.stderr.write("ERROR: --pg-dsn and --encryption-key are required for migration.\n")
        return 2

    md, ok = asyncio.run(
        _run_migration_async(
            str(sqlite_path),
            str(args.pg_dsn),
            str(args.encryption_key),
            args.legacy_encryption_key,
            bool(args.allow_nonempty),
        )
    )
    Path(args.report_out).write_text(md, encoding="utf-8")
    sys.stdout.write(md)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
