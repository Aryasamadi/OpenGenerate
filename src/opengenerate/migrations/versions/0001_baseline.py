"""Baseline: complete 25-table domain & operational schema with DB-enforced invariants.

Revision ID: 0001_baseline
Revises:
"""

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

UP = [
    # ---- generic helpers --------------------------------------------------------------------
    """
    CREATE FUNCTION og_forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION '% on % is not allowed (append-only)', TG_OP, TG_TABLE_NAME
            USING ERRCODE = 'integrity_constraint_violation';
    END $$
    """,
    # ---- 1. plans (Subscription) ------------------------------------------------------------
    """
    CREATE TABLE plans (
        plan_id          BIGSERIAL PRIMARY KEY,
        name_fa          TEXT NOT NULL,
        name_en          TEXT NOT NULL,
        desc_fa          TEXT NOT NULL DEFAULT '',
        desc_en          TEXT NOT NULL DEFAULT '',
        price_tmn        BIGINT CHECK (price_tmn IS NULL OR price_tmn >= 0),
        price_usd        BIGINT CHECK (price_usd IS NULL OR price_usd >= 0),
        price_text_fa    TEXT,
        price_text_en    TEXT,
        duration_days    INTEGER NOT NULL CHECK (duration_days > 0),
        daily_post_limit INTEGER NOT NULL CHECK (daily_post_limit >= 0),
        max_sources      INTEGER NOT NULL CHECK (max_sources >= 0),
        max_channels     INTEGER NOT NULL CHECK (max_channels >= 0),
        daily_test_limit INTEGER NOT NULL DEFAULT 3 CHECK (daily_test_limit >= 0),
        is_free          BOOLEAN NOT NULL DEFAULT FALSE,
        is_active        BOOLEAN NOT NULL DEFAULT TRUE,
        sort_order       INTEGER NOT NULL DEFAULT 0,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- 2. users (Identity) ----------------------------------------------------------------
    """
    CREATE TABLE users (
        user_id         BIGINT PRIMARY KEY CHECK (user_id > 0),
        username        TEXT,
        full_name       TEXT,
        role            TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin','superadmin')),
        lang            TEXT CHECK (lang IN ('fa','en')),
        plan_id         BIGINT REFERENCES plans(plan_id) ON DELETE RESTRICT,
        plan_expires_at TIMESTAMPTZ,
        queued_plan_id  BIGINT REFERENCES plans(plan_id) ON DELETE RESTRICT,
        is_banned       BOOLEAN NOT NULL DEFAULT FALSE,
        free_plan_used  BOOLEAN NOT NULL DEFAULT FALSE,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- 3. discounts (Discount) ------------------------------------------------------------
    """
    CREATE TABLE discounts (
        code       TEXT PRIMARY KEY CHECK (char_length(code) BETWEEN 3 AND 32),
        percent    INTEGER NOT NULL CHECK (percent BETWEEN 1 AND 100),
        expires_at TIMESTAMPTZ,
        max_uses   INTEGER NOT NULL DEFAULT 0 CHECK (max_uses >= 0),
        used_count INTEGER NOT NULL DEFAULT 0 CHECK (used_count >= 0 AND (max_uses = 0 OR used_count <= max_uses)),
        is_active  BOOLEAN NOT NULL DEFAULT TRUE,
        created_by BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- 4. channels (Channel) --------------------------------------------------------------
    """
    CREATE TABLE channels (
        channel_id        BIGINT PRIMARY KEY CHECK (channel_id < 0),
        username          TEXT,
        title             TEXT NOT NULL DEFAULT '',
        owner_id          BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
        original_owner_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
        enabled           BOOLEAN NOT NULL DEFAULT FALSE,
        settings_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
        last_run_at       TIMESTAMPTZ,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX channels_owner_idx ON channels (owner_id)",
    # ---- 5. sources (Source) ----------------------------------------------------------------
    """
    CREATE TABLE sources (
        source_id             BIGSERIAL PRIMARY KEY,
        channel_id            BIGINT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
        admin_id              BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
        url                   TEXT NOT NULL,
        canonical_url         TEXT NOT NULL,
        discovery_type        TEXT NOT NULL DEFAULT 'auto'
                              CHECK (discovery_type IN ('auto','rss','json_feed','sitemap','html','custom_api')),
        fetch_enabled         BOOLEAN NOT NULL DEFAULT TRUE,
        publish_enabled       BOOLEAN NOT NULL DEFAULT TRUE,
        custom_api_url        TEXT,
        custom_api_key_enc    TEXT,
        etag                  TEXT,
        last_modified         TEXT,
        failure_count         INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
        cooldown_until        TIMESTAMPTZ,
        last_diagnostics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (channel_id, canonical_url)
    )
    """,
    # ---- 6. usage (Usage) -------------------------------------------------------------------
    """
    CREATE TABLE usage (
        user_id         BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        usage_date      DATE NOT NULL DEFAULT CURRENT_DATE,
        posts_count     INTEGER NOT NULL DEFAULT 0 CHECK (posts_count >= 0),
        tests_count     INTEGER NOT NULL DEFAULT 0 CHECK (tests_count >= 0),
        ai_calls_count  INTEGER NOT NULL DEFAULT 0 CHECK (ai_calls_count >= 0),
        ai_tokens_count BIGINT NOT NULL DEFAULT 0 CHECK (ai_tokens_count >= 0),
        PRIMARY KEY (user_id, usage_date)
    )
    """,
    # ---- 7. articles (Content) --------------------------------------------------------------
    """
    CREATE TABLE articles (
        article_id          BIGSERIAL PRIMARY KEY,
        channel_id          BIGINT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
        source_id           BIGINT REFERENCES sources(source_id) ON DELETE SET NULL,
        url                 TEXT NOT NULL,
        canonical_url       TEXT NOT NULL,
        url_hash            TEXT NOT NULL,
        content_fingerprint TEXT,
        title               TEXT NOT NULL DEFAULT '',
        raw_body            TEXT NOT NULL DEFAULT '',
        published_at        TIMESTAMPTZ,
        scientific_domain   TEXT NOT NULL DEFAULT 'general',
        presentation_format TEXT NOT NULL DEFAULT 'narrative'
                            CHECK (presentation_format IN ('tabular','itemized','sequential','statistical','analytical','narrative')),
        generated_title     TEXT,
        generated_post      TEXT,
        generated_full      TEXT,
        rich_blocks_json    JSONB,
        category_name       TEXT,
        score               DOUBLE PRECISION,
        criteria_scores_json JSONB,
        media_urls_json     JSONB NOT NULL DEFAULT '[]'::jsonb,
        status              TEXT NOT NULL DEFAULT 'discovered' CHECK (status IN (
            'discovered','extracted','fetch_retryable','fetch_exhausted',
            'rejected_duplicate','rejected_date','rejected_ad_heuristic','rejected_ai_ad','rejected_low_score',
            'ai_pending','ai_processing','ai_retryable','ai_exhausted',
            'ready','publishing','published','delivery_unknown','publish_failed_safe')),
        rejection_reason    TEXT,
        failure_class       TEXT,
        retry_count         INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
        ai_model_used       TEXT,
        published_msg_id    BIGINT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (channel_id, url_hash)
    )
    """,
    "CREATE INDEX articles_channel_status_idx ON articles (channel_id, status, score DESC, created_at DESC)",
    # ---- 8. posted (Publication History) ----------------------------------------------------
    """
    CREATE TABLE posted (
        channel_id          BIGINT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
        article_hash        TEXT NOT NULL,
        article_id          BIGINT REFERENCES articles(article_id) ON DELETE SET NULL,
        telegram_message_id BIGINT,
        published_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (channel_id, article_hash)
    )
    """,
    # ---- 9. ch_tokens (Channel Transfer) ----------------------------------------------------
    """
    CREATE TABLE ch_tokens (
        token_id      BIGSERIAL PRIMARY KEY,
        channel_id    BIGINT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
        token_hash    TEXT NOT NULL UNIQUE,
        issued_by     BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        expires_at    TIMESTAMPTZ NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        max_attempts  INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts >= 1),
        consumed_at   TIMESTAMPTZ,
        consumed_by   BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- 10. ai_models (AI) -----------------------------------------------------------------
    """
    CREATE TABLE ai_models (
        model_id          BIGSERIAL PRIMARY KEY,
        owner_id          BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
        provider_type     TEXT NOT NULL CHECK (provider_type IN ('openai','anthropic','gemini')),
        name              TEXT NOT NULL,
        base_url          TEXT NOT NULL,
        api_key_enc       TEXT,
        unauthenticated   BOOLEAN NOT NULL DEFAULT FALSE,
        model_identifier  TEXT NOT NULL,
        priority          INTEGER NOT NULL DEFAULT 100,
        temperature       DOUBLE PRECISION NOT NULL DEFAULT 0.3 CHECK (temperature BETWEEN 0.0 AND 2.0),
        max_tokens        INTEGER NOT NULL DEFAULT 2048 CHECK (max_tokens > 0),
        is_active         BOOLEAN NOT NULL DEFAULT TRUE,
        is_default_test   BOOLEAN NOT NULL DEFAULT FALSE,
        health_status     TEXT NOT NULL DEFAULT 'unknown' CHECK (health_status IN ('ok','degraded','unknown')),
        failure_count     INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
        quarantined_until TIMESTAMPTZ,
        last_probe_at     TIMESTAMPTZ,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- 11. deeplinks (Deep Links) ---------------------------------------------------------
    """
    CREATE TABLE deeplinks (
        key          TEXT PRIMARY KEY,
        article_id   BIGINT REFERENCES articles(article_id) ON DELETE CASCADE,
        channel_id   BIGINT REFERENCES channels(channel_id) ON DELETE CASCADE,
        payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        views_count  INTEGER NOT NULL DEFAULT 0 CHECK (views_count >= 0),
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at   TIMESTAMPTZ
    )
    """,
    # ---- 12. kv_cache (General KV Cache) ----------------------------------------------------
    """
    CREATE TABLE kv_cache (
        cache_key  TEXT PRIMARY KEY,
        value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        expires_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- 13. ai_cache (AI Output Cache) -----------------------------------------------------
    """
    CREATE TABLE ai_cache (
        cache_key        TEXT PRIMARY KEY,
        response_json    JSONB NOT NULL,
        model_identifier TEXT NOT NULL,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at       TIMESTAMPTZ NOT NULL
    )
    """,
    # ---- 14. ai_usage (AI Usage Ledger) -----------------------------------------------------
    """
    CREATE TABLE ai_usage (
        id                 BIGSERIAL PRIMARY KEY,
        user_id            BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
        channel_id         BIGINT REFERENCES channels(channel_id) ON DELETE SET NULL,
        model_id           BIGINT REFERENCES ai_models(model_id) ON DELETE SET NULL,
        article_id         BIGINT REFERENCES articles(article_id) ON DELETE SET NULL,
        prompt_tokens      INTEGER NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
        completion_tokens  INTEGER NOT NULL DEFAULT 0 CHECK (completion_tokens >= 0),
        estimated_cost_usd BIGINT NOT NULL DEFAULT 0 CHECK (estimated_cost_usd >= 0),
        latency_ms         INTEGER NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
        status             TEXT NOT NULL,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- 15. pub_journal (Publication Recovery) ---------------------------------------------
    """
    CREATE TABLE pub_journal (
        journal_id      BIGSERIAL PRIMARY KEY,
        channel_id      BIGINT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
        article_id      BIGINT NOT NULL REFERENCES articles(article_id) ON DELETE CASCADE,
        idempotency_key TEXT NOT NULL UNIQUE,
        phase           TEXT NOT NULL CHECK (phase IN (
            'reserved','in_flight','acknowledged','settled','delivery_unknown','failed_safe')),
        quota_reserved  BOOLEAN NOT NULL DEFAULT TRUE,
        telegram_msg_id BIGINT,
        error_detail    TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- 16. settings (General Settings) ----------------------------------------------------
    """
    CREATE TABLE settings (
        key        TEXT PRIMARY KEY,
        value      JSONB NOT NULL,
        updated_by BIGINT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- 17. support (Support Sessions) -----------------------------------------------------
    """
    CREATE TABLE support (
        ticket_id       BIGSERIAL PRIMARY KEY,
        user_id         BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
        opened_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        closed_at       TIMESTAMPTZ,
        last_message_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- 18. support_map (Support Routing) --------------------------------------------------
    """
    CREATE TABLE support_map (
        superadmin_id    BIGINT NOT NULL,
        forwarded_msg_id BIGINT NOT NULL,
        ticket_id        BIGINT NOT NULL REFERENCES support(ticket_id) ON DELETE CASCADE,
        user_id          BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        user_msg_id      BIGINT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (superadmin_id, forwarded_msg_id)
    )
    """,
    # ---- 19. plan_changes (Subscription History) --------------------------------------------
    """
    CREATE TABLE plan_changes (
        change_id        BIGSERIAL PRIMARY KEY,
        idempotency_key  TEXT NOT NULL UNIQUE,
        user_id          BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
        old_plan_id      BIGINT REFERENCES plans(plan_id) ON DELETE RESTRICT,
        new_plan_id      BIGINT REFERENCES plans(plan_id) ON DELETE RESTRICT,
        change_type      TEXT NOT NULL CHECK (change_type IN (
            'free_claim','purchase','renewal','upgrade','admin_assign','revoke','refund')),
        proration_credit BIGINT NOT NULL DEFAULT 0 CHECK (proration_credit >= 0),
        wallet_debited   BIGINT NOT NULL DEFAULT 0 CHECK (wallet_debited >= 0),
        currency         TEXT CHECK (currency IN ('TMN','USD','LEGACY')),
        payment_ref      TEXT,
        actor_id         BIGINT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- 20. pay_requests (Payment Requests) ------------------------------------------------
    """
    CREATE TABLE pay_requests (
        payment_id           BIGSERIAL PRIMARY KEY,
        idempotency_key      TEXT NOT NULL UNIQUE,
        user_id              BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
        plan_id              BIGINT NOT NULL REFERENCES plans(plan_id) ON DELETE RESTRICT,
        currency             TEXT NOT NULL CHECK (currency IN ('TMN','USD')),
        quoted_base_price    BIGINT NOT NULL CHECK (quoted_base_price >= 0),
        discount_code        TEXT REFERENCES discounts(code) ON DELETE RESTRICT,
        discount_amount      BIGINT NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
        wallet_reserved      BIGINT NOT NULL DEFAULT 0 CHECK (wallet_reserved >= 0),
        payable_amount       BIGINT NOT NULL CHECK (payable_amount >= 0),
        receipt_type         TEXT NOT NULL CHECK (receipt_type IN ('text','photo','document','wallet_full')),
        receipt_file_id      TEXT,
        receipt_text         TEXT,
        status               TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
            'pending','processing','approved','rejected','refunded')),
        lease_until          TIMESTAMPTZ,
        reviewer_id          BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
        reviewed_at          TIMESTAMPTZ,
        applied_at           TIMESTAMPTZ,
        customer_notified_at TIMESTAMPTZ,
        created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE UNIQUE INDEX pay_requests_one_open_per_user_uq ON pay_requests (user_id)
        WHERE status IN ('pending','processing')
    """,
    # ---- 21. outbox / notifications (Durable Outbox) ----------------------------------------
    """
    CREATE TABLE outbox (
        id              BIGSERIAL PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        recipient_id    BIGINT NOT NULL,
        kind            TEXT NOT NULL,
        payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
        status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','sending','sent','dead_letter')),
        attempts        INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        max_attempts    INTEGER NOT NULL DEFAULT 8 CHECK (max_attempts >= 1),
        next_retry_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        lease_until     TIMESTAMPTZ,
        last_error      TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        sent_at         TIMESTAMPTZ
    )
    """,
    "CREATE INDEX outbox_claim_idx ON outbox (next_retry_at, id) WHERE status = 'pending'",
    "CREATE INDEX outbox_lease_idx ON outbox (lease_until) WHERE status = 'sending'",
    "CREATE VIEW notifications AS SELECT * FROM outbox",
    # ---- 22. referrals (Referral Attribution) -----------------------------------------------
    """
    CREATE TABLE referrals (
        referred_user_id        BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE RESTRICT,
        referrer_user_id        BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
        attributed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        total_reward_tmn        BIGINT NOT NULL DEFAULT 0 CHECK (total_reward_tmn >= 0),
        total_reward_usd        BIGINT NOT NULL DEFAULT 0 CHECK (total_reward_usd >= 0),
        rewarded_payments_count INTEGER NOT NULL DEFAULT 0 CHECK (rewarded_payments_count >= 0),
        CHECK (referred_user_id <> referrer_user_id)
    )
    """,
    # ---- 23. wallet_balances / wallet (Wallet Balances) -------------------------------------
    """
    CREATE TABLE wallet_balances (
        user_id    BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
        currency   TEXT NOT NULL CHECK (currency IN ('TMN','USD','LEGACY')),
        balance    BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (user_id, currency)
    )
    """,
    "CREATE VIEW wallet AS SELECT * FROM wallet_balances",
    # ---- 24. ledger_entries / credit_log (Financial Ledger) ---------------------------------
    """
    CREATE TABLE ledger_entries (
        entry_id        BIGSERIAL PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        user_id         BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
        currency        TEXT NOT NULL CHECK (currency IN ('TMN','USD','LEGACY')),
        delta           BIGINT NOT NULL CHECK (delta <> 0),
        balance_after   BIGINT NOT NULL CHECK (balance_after >= 0),
        reason          TEXT NOT NULL CHECK (reason IN (
            'payment_debit','payment_surplus','referral_reward','refund_credit',
            'refund_clawback','admin_adjustment','migration','legacy_reconcile')),
        reference       TEXT,
        actor_id        BIGINT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY (user_id, currency) REFERENCES wallet_balances(user_id, currency)
            DEFERRABLE INITIALLY DEFERRED
    )
    """,
    "CREATE INDEX ledger_entries_wallet_idx ON ledger_entries (user_id, currency)",
    "CREATE VIEW credit_log AS SELECT * FROM ledger_entries",
    """
    CREATE TRIGGER ledger_entries_immutable BEFORE UPDATE OR DELETE ON ledger_entries
        FOR EACH ROW EXECUTE FUNCTION og_forbid_mutation()
    """,
    """
    CREATE FUNCTION og_ledger_legacy_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.currency = 'LEGACY' AND NEW.reason NOT IN ('migration','legacy_reconcile') THEN
            RAISE EXCEPTION 'LEGACY balances are frozen (reason %)', NEW.reason
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END $$
    """,
    """
    CREATE TRIGGER ledger_entries_legacy_guard BEFORE INSERT ON ledger_entries
        FOR EACH ROW EXECUTE FUNCTION og_ledger_legacy_guard()
    """,
    """
    CREATE FUNCTION og_check_wallet_invariant() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE s BIGINT; b BIGINT;
    BEGIN
        SELECT COALESCE(SUM(delta), 0) INTO s FROM ledger_entries
            WHERE user_id = NEW.user_id AND currency = NEW.currency;
        SELECT balance INTO b FROM wallet_balances
            WHERE user_id = NEW.user_id AND currency = NEW.currency;
        IF COALESCE(b, 0) <> s THEN
            RAISE EXCEPTION 'wallet invariant violated for user % currency %: balance % <> ledger sum %',
                NEW.user_id, NEW.currency, COALESCE(b, 0), s
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NULL;
    END $$
    """,
    """
    CREATE CONSTRAINT TRIGGER ledger_wallet_invariant AFTER INSERT ON ledger_entries
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION og_check_wallet_invariant()
    """,
    """
    CREATE CONSTRAINT TRIGGER wallet_ledger_invariant AFTER INSERT OR UPDATE ON wallet_balances
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION og_check_wallet_invariant()
    """,
    # ---- 25. rate_limits (Abuse Controls) ---------------------------------------------------
    """
    CREATE TABLE rate_limits (
        bucket_key   TEXT PRIMARY KEY,
        window_start TIMESTAMPTZ NOT NULL,
        count        INTEGER NOT NULL CHECK (count >= 0)
    )
    """,
    # ---- Operational Foundation Tables ------------------------------------------------------
    """
    CREATE TABLE idempotency_keys (
        scope        TEXT NOT NULL,
        key          TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        response     JSONB,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (scope, key)
    )
    """,
    """
    CREATE TABLE audit_log (
        id             BIGSERIAL PRIMARY KEY,
        actor_id       BIGINT,
        action         TEXT NOT NULL,
        object_type    TEXT NOT NULL,
        object_id      TEXT,
        before_hash    TEXT,
        after_hash     TEXT,
        reason         TEXT,
        correlation_id TEXT,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TRIGGER audit_log_immutable BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION og_forbid_mutation()
    """,
    """
    CREATE TABLE system_heartbeats (
        name    TEXT PRIMARY KEY,
        beat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        info    JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE jobs (
        id             BIGSERIAL PRIMARY KEY,
        kind           TEXT NOT NULL,
        payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
        status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','running','done','failed')),
        run_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        lease_until    TIMESTAMPTZ,
        locked_by      TEXT,
        attempts       INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        max_attempts   INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts >= 1),
        dedupe_key     TEXT,
        last_error     TEXT,
        correlation_id TEXT,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX jobs_claim_idx ON jobs (run_at, id) WHERE status = 'pending'",
    "CREATE INDEX jobs_lease_idx ON jobs (lease_until) WHERE status = 'running'",
    """
    CREATE UNIQUE INDEX jobs_dedupe_active_uq ON jobs (dedupe_key)
        WHERE dedupe_key IS NOT NULL AND status IN ('pending','running')
    """,
]

DOWN = [
    "DROP TABLE IF EXISTS jobs",
    "DROP TABLE IF EXISTS system_heartbeats",
    "DROP TABLE IF EXISTS audit_log",
    "DROP TABLE IF EXISTS idempotency_keys",
    "DROP TABLE IF EXISTS rate_limits",
    "DROP VIEW IF EXISTS credit_log",
    "DROP TABLE IF EXISTS ledger_entries",
    "DROP VIEW IF EXISTS wallet",
    "DROP TABLE IF EXISTS wallet_balances",
    "DROP FUNCTION IF EXISTS og_check_wallet_invariant()",
    "DROP FUNCTION IF EXISTS og_ledger_legacy_guard()",
    "DROP TABLE IF EXISTS referrals",
    "DROP VIEW IF EXISTS notifications",
    "DROP TABLE IF EXISTS outbox",
    "DROP TABLE IF EXISTS pay_requests",
    "DROP TABLE IF EXISTS plan_changes",
    "DROP TABLE IF EXISTS support_map",
    "DROP TABLE IF EXISTS support",
    "DROP TABLE IF EXISTS settings",
    "DROP TABLE IF EXISTS pub_journal",
    "DROP TABLE IF EXISTS ai_usage",
    "DROP TABLE IF EXISTS ai_cache",
    "DROP TABLE IF EXISTS kv_cache",
    "DROP TABLE IF EXISTS deeplinks",
    "DROP TABLE IF EXISTS ai_models",
    "DROP TABLE IF EXISTS ch_tokens",
    "DROP TABLE IF EXISTS posted",
    "DROP TABLE IF EXISTS articles",
    "DROP TABLE IF EXISTS usage",
    "DROP TABLE IF EXISTS sources",
    "DROP TABLE IF EXISTS channels",
    "DROP TABLE IF EXISTS discounts",
    "DROP TABLE IF EXISTS users",
    "DROP TABLE IF EXISTS plans",
    "DROP FUNCTION IF EXISTS og_forbid_mutation()",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
