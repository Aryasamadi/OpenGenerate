"""Unit tests for Phase 8: Legacy SQLite 25-Table Inspector & Deployment Packaging."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from opengenerate.infrastructure.legacy_migrator import (
    LEGACY_25_TABLES,
    inspect_legacy_sqlite,
)


def create_synthetic_legacy_sqlite(db_path: Path) -> None:
    """Create a realistic 25-table legacy SQLite snapshot for rehearsal testing."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE plans (
                plan_id INTEGER PRIMARY KEY,
                name_fa TEXT, name_en TEXT,
                price_tmn TEXT, price_usd TEXT,
                duration_days INTEGER, daily_post_limit INTEGER,
                max_sources INTEGER, max_channels INTEGER, daily_test_limit INTEGER,
                is_free INTEGER, is_active INTEGER
            );
            INSERT INTO plans VALUES (1, 'حرفه‌ای', 'Pro', '500000', '2000', 30, 20, 10, 3, 5, 0, 1);
            INSERT INTO plans VALUES (2, 'سازمانی', 'Enterprise', 'توافقی', 'Contact Us', 365, 100, 50, 20, 20, 0, 1);

            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT, full_name TEXT, role TEXT, lang TEXT,
                plan_id INTEGER, is_banned INTEGER, free_plan_used INTEGER
            );
            INSERT INTO users VALUES (101, 'founder', 'Founder', 'superadmin', 'fa', 1, 0, 0);
            INSERT INTO users VALUES (202, 'ali_admin', 'Ali', 'admin', 'fa', 1, 0, 1);
            INSERT INTO users VALUES (303, 'sara_user', 'Sara', 'user', 'en', NULL, 0, 0);

            CREATE TABLE discounts (
                code TEXT PRIMARY KEY, percent INTEGER, max_uses INTEGER, used_count INTEGER, is_active INTEGER, created_by INTEGER
            );
            INSERT INTO discounts VALUES ('LEGACY25', 25, 10, 2, 1, 101);

            CREATE TABLE channels (
                channel_id INTEGER PRIMARY KEY, username TEXT, title TEXT,
                owner_id INTEGER, original_owner_id INTEGER, enabled INTEGER, settings_json TEXT
            );
            INSERT INTO channels VALUES (-1007777777777, '@SciChannel', 'کانال علم', 202, 202, 1, '{"language":"fa","min_score_threshold":70}');

            CREATE TABLE sources (
                source_id INTEGER PRIMARY KEY, channel_id INTEGER, admin_id INTEGER,
                url TEXT, discovery_type TEXT, fetch_enabled INTEGER, publish_enabled INTEGER,
                custom_api_url TEXT, custom_api_key TEXT, failure_count INTEGER
            );
            INSERT INTO sources VALUES (10, -1007777777777, 202, 'https://example.com/rss?utm_source=old', 'rss', 1, 1, 'https://example.com/api', 'legacy_plain_secret_key_1', 0);

            CREATE TABLE usage (
                user_id INTEGER, usage_date TEXT, posts_count INTEGER, tests_count INTEGER, ai_calls_count INTEGER, ai_tokens_count INTEGER
            );
            INSERT INTO usage VALUES (202, '2026-11-10', 4, 1, 5, 4200);

            CREATE TABLE articles (
                article_id INTEGER PRIMARY KEY, channel_id INTEGER, source_id INTEGER,
                url TEXT, title TEXT, raw_body TEXT, generated_title TEXT, generated_post TEXT, generated_full TEXT,
                status TEXT, score REAL
            );
            INSERT INTO articles VALUES (500, -1007777777777, 10, 'https://example.com/post-500', 'کشف علمی جدید', 'متن کامل مقاله علمی...', 'کشف علمی', 'خلاصه کانال', '<b>متن کامل در ربات</b>', 'published', 88.5);
            INSERT INTO articles VALUES (501, -1007777777777, 10, 'https://example.com/post-501', 'مقاله در حال ارسال', 'متن مقاله ۵۰۱', 'عنوان ۵۰۱', 'پست ۵۰۱', 'ادامه ۵۰۱', 'in_flight', 79.0);

            CREATE TABLE posted (
                channel_id INTEGER, article_hash TEXT, article_id INTEGER, telegram_message_id INTEGER
            );
            INSERT INTO posted VALUES (-1007777777777, 'hash_post_500', 500, 9901);

            CREATE TABLE ch_tokens (
                token_id INTEGER PRIMARY KEY, channel_id INTEGER, token_hash TEXT, issued_by INTEGER
            );
            INSERT INTO ch_tokens VALUES (1, -1007777777777, 'old_token_hash_abc', 202);

            CREATE TABLE ai_models (
                model_id INTEGER PRIMARY KEY, owner_id INTEGER, provider_type TEXT, name TEXT,
                base_url TEXT, api_key TEXT, unauthenticated INTEGER, model_identifier TEXT,
                priority INTEGER, temperature REAL, max_tokens INTEGER, is_active INTEGER
            );
            INSERT INTO ai_models VALUES (1, NULL, 'openai', 'Global GPT', 'https://api.openai.com/v1', 'sk-legacy-openai-key', 0, 'gpt-4o-mini', 10, 0.3, 2048, 1);

            CREATE TABLE deeplinks (
                key TEXT PRIMARY KEY, article_id INTEGER, channel_id INTEGER, payload_json TEXT, views_count INTEGER
            );
            INSERT INTO deeplinks VALUES ('dl_legacyKey500', 500, -1007777777777, '{"html_chunks":["<b>متن کامل در ربات</b>"],"rich_blocks":[]}', 12);

            CREATE TABLE kv_cache (cache_key TEXT PRIMARY KEY, value_json TEXT);
            INSERT INTO kv_cache VALUES ('ephemeral_1', '{}');

            CREATE TABLE ai_cache (cache_key TEXT PRIMARY KEY, response_json TEXT);
            INSERT INTO ai_cache VALUES ('ephemeral_ai_1', '{}');

            CREATE TABLE ai_usage (
                id INTEGER PRIMARY KEY, user_id INTEGER, channel_id INTEGER, model_id INTEGER,
                article_id INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER,
                estimated_cost_usd INTEGER, latency_ms INTEGER, status TEXT
            );
            INSERT INTO ai_usage VALUES (1, 202, -1007777777777, 1, 500, 300, 150, 2, 420, 'ok');

            CREATE TABLE pub_journal (
                journal_id INTEGER PRIMARY KEY, channel_id INTEGER, article_id INTEGER,
                idempotency_key TEXT, phase TEXT, quota_reserved INTEGER, telegram_msg_id INTEGER
            );
            INSERT INTO pub_journal VALUES (1, -1007777777777, 501, 'legacy_pub_501', 'in_flight', 1, NULL);

            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO settings VALUES ('global_automation_enabled', '{"enabled":true}');
            INSERT INTO settings VALUES ('i18n:welcome:fa', '{"text":"<b>Unclosed broken HTML"}');

            CREATE TABLE support (ticket_id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT);
            INSERT INTO support VALUES (77, 303, 'open');

            CREATE TABLE support_map (
                superadmin_id INTEGER, forwarded_msg_id INTEGER, ticket_id INTEGER, user_id INTEGER, user_msg_id INTEGER
            );
            INSERT INTO support_map VALUES (101, 88001, 77, 303, 44001);

            CREATE TABLE plan_changes (
                change_id INTEGER PRIMARY KEY, user_id INTEGER, old_plan_id INTEGER, new_plan_id INTEGER, change_type TEXT
            );
            INSERT INTO plan_changes VALUES (1, 202, NULL, 1, 'purchase');

            CREATE TABLE pay_requests (
                payment_id INTEGER PRIMARY KEY, user_id INTEGER, plan_id INTEGER, currency TEXT,
                quoted_base_price INTEGER, discount_code TEXT, discount_amount INTEGER,
                wallet_reserved INTEGER, payable_amount INTEGER, receipt_type TEXT,
                receipt_text TEXT, status TEXT
            );
            INSERT INTO pay_requests VALUES (90, 303, 1, 'TMN', 500000, 'LEGACY25', 125000, 0, 375000, 'text', 'فیش بانکی ۱۲۳۴۵', 'processing');

            CREATE TABLE notifications (
                id INTEGER PRIMARY KEY, recipient_id INTEGER, kind TEXT, payload TEXT, status TEXT
            );
            INSERT INTO notifications VALUES (1, 101, 'text', '{"text":"Legacy notification"}', 'pending');

            CREATE TABLE referrals (
                referred_user_id INTEGER PRIMARY KEY, referrer_user_id INTEGER,
                total_reward_tmn INTEGER, total_reward_usd INTEGER, rewarded_payments_count INTEGER
            );
            INSERT INTO referrals VALUES (303, 202, 50000, 0, 1);

            CREATE TABLE wallet (user_id INTEGER, balance INTEGER);
            INSERT INTO wallet VALUES (202, 120000);

            CREATE TABLE credit_log (entry_id INTEGER PRIMARY KEY, user_id INTEGER, delta INTEGER, reference TEXT);
            INSERT INTO credit_log VALUES (1, 202, 50000, 'legacy_referral_bonus');

            CREATE TABLE rate_limits (bucket_key TEXT PRIMARY KEY, count INTEGER);
            INSERT INTO rate_limits VALUES ('rl_1', 3);
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_inspect_legacy_sqlite_read_only_and_anomaly_detection(tmp_path: Path) -> None:
    sqlite_file = tmp_path / "legacy_bot.db"
    create_synthetic_legacy_sqlite(sqlite_file)

    rep1 = inspect_legacy_sqlite(sqlite_file)
    rep2 = inspect_legacy_sqlite(sqlite_file)

    # Read-only guarantee: SHA-256 never changes
    assert rep1.file_sha256 == rep2.file_sha256
    assert rep1.missing_tables == []
    assert set(rep1.table_counts.keys()) == set(LEGACY_25_TABLES)
    assert rep1.in_flight_publications == 1
    assert rep1.processing_payments == 1
    assert rep1.untagged_wallet_rows == 1
    assert len(rep1.anomalies) == 3


def test_deployment_and_packaging_files_present_and_hardened() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dockerfile = (repo_root / "Dockerfile").read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile
    assert "HEALTHCHECK" in dockerfile

    compose = (repo_root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "postgres:16-alpine" in compose
    assert "service_healthy" in compose
    assert "/readyz" in compose

    assert (repo_root / ".env.example").is_file()
    assert (repo_root / "deploy" / "install.sh").is_file()
    assert (repo_root / "deploy" / "rollback.sh").is_file()
    assert (repo_root / "deploy" / "systemd" / "opengenerate.service").is_file()
    assert (repo_root / "tools" / "migrate_sqlite_to_pg.py").is_file()
