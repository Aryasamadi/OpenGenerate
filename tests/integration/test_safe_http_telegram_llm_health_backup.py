from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from tests.conftest import make_user
from tests.support.http_server import FakeServer, Req, Resp

from opengenerate.application.ports import LlmRequest
from opengenerate.domain.errors import PermanentError, TransientError, UnknownOutcomeError
from opengenerate.infrastructure import backup, health
from opengenerate.infrastructure.db.engine import Database, to_plain_dsn
from opengenerate.infrastructure.http_safe import (
    EgressPolicy,
    ResponseTooLarge,
    SafeHttpClient,
    SSRFBlocked,
    UnexpectedContentType,
)
from opengenerate.infrastructure.llm.adapters import HttpLlmProvider
from opengenerate.infrastructure.ratelimit import record_heartbeat
from opengenerate.infrastructure.telegram.raw_api import RawBotApi, RecipientUnreachable
from opengenerate.interfaces.ops import build_ops_app
from opengenerate.migrations import head_revision


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1/secret",
        "http://[::1]/secret",
        "http://10.0.0.1/internal",
        "http://172.16.5.4/internal",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:127.0.0.1]/mapped",
        "http://[64:ff9b::7f00:1]/nat64",
        "file:///etc/passwd",
        "http://user:pass@example.com/feed",
        "https://example.com:8443/nonstandard",
    ],
)
async def test_safe_http_blocks_private_loopback_metadata_and_bad_schemes(bad_url: str) -> None:
    async with SafeHttpClient() as client:
        with pytest.raises(SSRFBlocked):
            await client.get(bad_url)


async def test_safe_http_blocks_redirect_to_private_ip_and_strips_auth_on_origin_change() -> None:
    async with FakeServer(lambda _r: Resp(200, {"Content-Type": "application/json"}, b'{"ok":true}')) as dest:
        def redirect_handler(req: Req) -> Resp:
            if req.path == "/to-metadata":
                return Resp(302, {"Location": "http://169.254.169.254/latest"})
            return Resp(302, {"Location": f"{dest.base}/landed"})

        async with FakeServer(redirect_handler) as origin:
            # Allow origin only -> redirect to unallowlisted dest must be blocked by SSRF guard
            policy_origin_only = EgressPolicy.from_allowlist([origin.allow])
            async with SafeHttpClient(policy_origin_only) as client:
                with pytest.raises(SSRFBlocked):
                    await client.get(f"{origin.base}/to-metadata")
                with pytest.raises(SSRFBlocked):
                    await client.get(f"{origin.base}/to-dest")

            # Allow both origin and dest -> redirect succeeds, but Authorization header is stripped!
            policy_both = EgressPolicy.from_allowlist([origin.allow, dest.allow])
            async with SafeHttpClient(policy_both) as client:
                resp = await client.get(
                    f"{origin.base}/to-dest", headers={"Authorization": "Bearer secret-tok"}
                )
                assert resp.status_code == 200
                assert "authorization" in origin.requests[-1].headers
                assert "authorization" not in dest.requests[-1].headers


async def test_safe_http_enforces_size_and_content_type_caps() -> None:
    async with FakeServer(
        lambda _r: Resp(200, {"Content-Type": "text/html; charset=utf-8"}, b"x" * 5000)
    ) as srv:
        policy = EgressPolicy.from_allowlist([srv.allow])
        async with SafeHttpClient(policy, max_response_bytes=1024) as client:
            with pytest.raises(ResponseTooLarge):
                await client.get(f"{srv.base}/big")
            with pytest.raises(UnexpectedContentType):
                await client.get(
                    f"{srv.base}/type", allowed_content_types=["application/json"], max_bytes=10_000
                )


async def test_raw_bot_api_error_taxonomy() -> None:
    mode = "ok"

    def handler(_req: Req) -> Resp:
        if mode == "ok":
            return Resp(200, {"Content-Type": "application/json"}, b'{"ok":true,"result":{"message_id":42}}')
        if mode == "429":
            return Resp(
                429,
                {"Content-Type": "application/json"},
                b'{"ok":false,"error_code":429,"description":"Too Many Requests","parameters":{"retry_after":7}}',
            )
        if mode == "blocked":
            return Resp(
                403,
                {"Content-Type": "application/json"},
                b'{"ok":false,"error_code":403,"description":"Forbidden: bot was blocked by the user"}',
            )
        return Resp(502, {"Content-Type": "application/json"}, b'{"ok":false,"error_code":502,"description":"Bad Gateway"}')

    async with FakeServer(handler) as srv:
        api = RawBotApi(SecretStr("1234567:AAH_token_value_0123456789abcdefghij"), base_url=srv.base)
        try:
            res = await api.send_message(100, "hello")
            assert res["message_id"] == 42

            mode = "429"
            with pytest.raises(TransientError) as exc_429:
                await api.send_message(100, "hello")
            assert exc_429.value.retry_after == 7.0

            mode = "blocked"
            with pytest.raises(RecipientUnreachable):
                await api.send_message(100, "hello")

            mode = "502"
            # Non-idempotent sendMessage on 5xx is UnknownOutcomeError (never blind retry)
            with pytest.raises(UnknownOutcomeError):
                await api.send_message(100, "hello")
            # Idempotent getMe on 5xx is TransientError
            with pytest.raises(TransientError):
                await api.get_me()
        finally:
            await api.aclose()


async def test_llm_provider_adapters_openai_anthropic_gemini() -> None:
    def handler(req: Req) -> Resp:
        if req.path == "/v1/chat/completions":
            assert req.headers.get("authorization") == "Bearer sk-openai-test"
            return Resp(
                200,
                {"Content-Type": "application/json"},
                json.dumps(
                    {
                        "model": "gpt-4o-mini",
                        "choices": [{"message": {"content": '{"title":"OA"}'}}],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 8},
                    }
                ).encode(),
            )
        if req.path.endswith("/messages"):
            assert req.headers.get("x-api-key") == "sk-ant-test"
            return Resp(
                200,
                {"Content-Type": "application/json"},
                json.dumps(
                    {
                        "model": "claude-3-5-sonnet",
                        "content": [{"type": "text", "text": '{"title":"ANT"}'}],
                        "usage": {"input_tokens": 15, "output_tokens": 9},
                    }
                ).encode(),
            )
        if ":generateContent" in req.path:
            assert req.headers.get("x-goog-api-key") == "AIza-gemini-test"
            return Resp(
                200,
                {"Content-Type": "application/json"},
                json.dumps(
                    {
                        "candidates": [{"content": {"parts": [{"text": '{"title":"GEM"}'}]}}],
                        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
                    }
                ).encode(),
            )
        return Resp(401, {"Content-Type": "application/json"}, b'{"error":"invalid api key sk-secret1234567890123456"}')

    async with FakeServer(handler) as srv:
        policy = EgressPolicy.from_allowlist([srv.allow])
        async with SafeHttpClient(policy) as http:
            provider = HttpLlmProvider(http)

            r_oa = await provider.complete(
                LlmRequest("openai", srv.base, "gpt-4o-mini", "sys", "usr", api_key="sk-openai-test")
            )
            assert r_oa.text == '{"title":"OA"}' and r_oa.prompt_tokens == 12

            r_ant = await provider.complete(
                LlmRequest("anthropic", srv.base, "claude-3-5-sonnet", "sys", "usr", api_key="sk-ant-test")
            )
            assert r_ant.text == '{"title":"ANT"}' and r_ant.completion_tokens == 9

            r_gem = await provider.complete(
                LlmRequest("gemini", srv.base, "gemini-2.0-flash", "sys", "usr", api_key="AIza-gemini-test")
            )
            assert r_gem.text == '{"title":"GEM"}' and r_gem.prompt_tokens == 10

            with pytest.raises(PermanentError) as exc_401:
                await provider.complete(
                    LlmRequest("openai", f"{srv.base}/bad/chat/completions", "m", "s", "u", api_key="bad")
                )
            assert "sk-secret1234567890123456" not in str(exc_401.value)


async def test_ops_endpoints_distinguish_unknown_ok_and_degraded(db: Database) -> None:
    readiness = {
        "database": health.db_check(db),
        "migrations": health.migration_check(db, head_revision()),
    }
    detail = {
        "worker_heartbeat": health.heartbeat_check(db, "worker", max_age=30.0),
        "outbox": health.outbox_check(db),
        "ledger": health.ledger_check(db),
    }
    app = build_ops_app(role="worker", version="abc1234", readiness=readiness, health=detail)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ops") as client:
        live = await client.get("/livez")
        assert live.status_code == 200 and live.json()["status"] == "alive"

        ready = await client.get("/readyz")
        assert ready.status_code == 200 and ready.json()["ready"] is True

        # Before any worker heartbeat is recorded, overall health MUST be 'unknown', not 'ok'
        hz1 = await client.get("/healthz")
        assert hz1.json()["status"] == "unknown"
        assert hz1.json()["checks"]["worker_heartbeat"]["status"] == "unknown"

        await record_heartbeat(db, "worker", {"leader": True})
        hz2 = await client.get("/healthz")
        assert hz2.json()["status"] == "ok"

        metrics = await client.get("/metrics")
        assert metrics.status_code == 200


async def test_backup_verify_tamper_detection_and_restore_into_clean_db(
    admin_url: str, db_url: str, db: Database, tmp_path: Path
) -> None:
    await make_user(db, 777, role="superadmin")
    await db.dispose()

    res = backup.create_backup(db_url, tmp_path)
    assert res.path.exists() and res.manifest_path.exists()
    assert res.manifest["table_counts"]["users"] == 1

    # Tampered manifest sha256 must fail verification
    orig_manifest = res.manifest_path.read_text(encoding="utf-8")
    res.manifest_path.write_text(
        orig_manifest.replace(res.manifest["sha256"], "0" * 64), encoding="utf-8"
    )
    with pytest.raises(backup.BackupError, match="checksum mismatch"):
        backup.verify_backup(res.path)
    res.manifest_path.write_text(orig_manifest, encoding="utf-8")

    # Restore into a brand-new empty database and verify counts + schema revision
    restore_db = f"r_{uuid.uuid4().hex[:10]}"
    target_url = make_url(to_plain_dsn(admin_url)).set(database=restore_db).render_as_string(hide_password=False)
    with psycopg.connect(to_plain_dsn(admin_url), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{restore_db}"')
    try:
        manifest = backup.restore_backup(res.path, target_url)
        assert manifest["table_counts"]["users"] == 1
        # Refuses to restore again over a non-empty database
        with pytest.raises(backup.BackupError, match="not empty"):
            backup.restore_backup(res.path, target_url)
    finally:
        with psycopg.connect(to_plain_dsn(admin_url), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{restore_db}" WITH (FORCE)')
