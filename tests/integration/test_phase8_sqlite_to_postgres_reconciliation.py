"""Integration tests for Phase 8: 25-Table SQLite -> PostgreSQL ETL & 10-Gate Reconciliation."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from tests.unit.test_phase8_migration_inspector_and_packaging import (
    create_synthetic_legacy_sqlite,
)

from opengenerate.domain.errors import Conflict
from opengenerate.domain.money import Currency
from opengenerate.infrastructure.crypto import FieldCipher
from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.db.ledger import get_balance
from opengenerate.infrastructure.db.publish_store import resolve_deeplink_continuation
from opengenerate.infrastructure.legacy_migrator import migrate_sqlite_to_postgres


@pytest.mark.asyncio
async def test_full_25_table_sqlite_to_postgres_etl_and_10_reconciliation_gates(
    db: Database,
    fernet_key: str,
    tmp_path: Path,
) -> None:
    sqlite_file = tmp_path / "legacy_snapshot.db"
    create_synthetic_legacy_sqlite(sqlite_file)
    keyring = FieldCipher([fernet_key])

    async with db.transaction() as conn:
        res = await migrate_sqlite_to_postgres(
            sqlite_file,
            conn,
            target_keyring=keyring,
            allow_nonempty=False,
        )

    assert res.reconciliation is not None
    assert res.reconciliation.all_passed is True
    assert len(res.reconciliation.gates) == 10
    for gate in res.reconciliation.gates:
        assert gate.passed is True, f"Gate {gate.gate_id} failed: {gate.detail}"

    md_report = res.render_markdown_report()
    assert "Overall Reconciliation Status:** `PASSED`" in md_report
    assert "Dropped unbalanced HTML override in settings key 'i18n:welcome:fa'" in md_report

    # Verify post-migration invariants inside PostgreSQL
    async with db.transaction() as conn:
        # 1. Legacy deep-link resolves byte-for-byte (`dl_legacyKey500`)
        cont = await resolve_deeplink_continuation(conn, key="dl_legacyKey500")
        assert cont.article_id == 500
        assert "متن کامل در ربات" in cont.html_chunks[0]

        # 2. Legacy in-flight pub_journal row was quarantined as `delivery_unknown`
        pj_phase = (
            await conn.execute(
                text("SELECT phase FROM pub_journal WHERE idempotency_key = 'legacy_pub_501'")
            )
        ).scalar_one()
        assert pj_phase == "delivery_unknown"

        # 3. Untagged legacy wallet balance isolated in `LEGACY` (non-spendable in TMN/USD)
        assert await get_balance(conn, 202, Currency.TMN) == 0
        assert await get_balance(conn, 202, Currency.USD) == 0
        assert await get_balance(conn, 202, Currency.LEGACY) == 120000

        # 4. Plaintext API keys in legacy SQLite are now encrypted at rest (`v1:...`)
        src_key_enc = str(
            (
                await conn.execute(
                    text("SELECT custom_api_key_enc FROM sources WHERE source_id = 10")
                )
            ).scalar_one()
        )
        assert src_key_enc.startswith("v1:")
        assert keyring.decrypt(src_key_enc) == "legacy_plain_secret_key_1"

        ai_key_enc = str(
            (
                await conn.execute(
                    text("SELECT api_key_enc FROM ai_models WHERE model_id = 1")
                )
            ).scalar_one()
        )
        assert ai_key_enc.startswith("v1:")
        assert keyring.decrypt(ai_key_enc) == "sk-legacy-openai-key"

        # 5. Re-running without allow_nonempty=True refuses to overwrite non-empty target DB
        with pytest.raises(Conflict):
            await migrate_sqlite_to_postgres(
                sqlite_file,
                conn,
                target_keyring=keyring,
                allow_nonempty=False,
            )
