from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from opengenerate.domain.channels import ChannelSettings
from opengenerate.domain.errors import Conflict, QuotaExceeded, ValidationFailed
from opengenerate.domain.roles import Actor, Role
from opengenerate.infrastructure.crypto import FieldCipher
from opengenerate.infrastructure.db.billing_store import assign_or_revoke_user_plan, create_plan
from opengenerate.infrastructure.db.channel_store import (
    issue_transfer_token,
    list_active_transfer_tokens,
    redeem_transfer_token,
    register_channel,
    reset_channel_settings,
    revoke_transfer_token,
    set_channel_enabled,
    update_channel_settings,
)
from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.db.identity_store import register_or_update_user
from opengenerate.infrastructure.db.source_store import (
    add_source,
    clear_article_queue,
    ingest_discovered_article,
    list_sources,
    poll_and_extract_source,
)
from opengenerate.infrastructure.http_safe import SafeResponse


class _MockHttp:
    def __init__(self, routes: dict[str, SafeResponse]) -> None:
        self.routes = routes

    async def get(self, url: str, **_kwargs: Any) -> SafeResponse:
        if url not in self.routes:
            raise RuntimeError(f"upstream failed for {url}")
        return self.routes[url]


def _make_resp(url: str, body: str, content_type: str = "text/html; charset=utf-8") -> SafeResponse:
    return SafeResponse(
        status_code=200,
        headers=httpx.Headers({"content-type": content_type, "etag": '"etag-1"'}),
        content=body.encode("utf-8"),
        url=url,
    )


@pytest.mark.asyncio
async def test_channel_registration_bot_permissions_quotas_and_settings_reset(
    db: Database,
) -> None:
    sa = Actor(user_id=100, role=Role.SUPERADMIN)
    owner = Actor(user_id=2001, role=Role.ADMIN)

    async with db.transaction() as conn:
        await register_or_update_user(conn, user_id=100, bootstrap_superadmins={100})
        await register_or_update_user(conn, user_id=2001)

        plan = await create_plan(
            conn,
            actor=sa,
            name_fa="تک‌کاناله",
            name_en="Single Channel",
            price_tmn=100_000,
            price_usd=500,
            duration_days=30,
            daily_post_limit=10,
            max_sources=2,
            max_channels=1,
        )
        await assign_or_revoke_user_plan(
            conn, actor=sa, target_user_id=2001, plan_id=plan.plan_id, idempotency_key="asgn:2001"
        )

        # Positive channel_id is rejected
        with pytest.raises(ValidationFailed):
            await register_channel(conn, actor=owner, channel_id=12345, title="Invalid")

        # Bot without can_post_messages is rejected
        async def _no_post_perm(_cid: int) -> dict[str, Any]:
            return {"status": "administrator", "can_post_messages": False}

        with pytest.raises(ValidationFailed):
            await register_channel(
                conn,
                actor=owner,
                channel_id=-1001111111111,
                title="Sci",
                get_bot_member=_no_post_perm,
            )

        async def _ok_admin(_cid: int) -> dict[str, Any]:
            return {"status": "administrator", "can_post_messages": True}

        ch = await register_channel(
            conn,
            actor=owner,
            channel_id=-1001111111111,
            title="Science Today",
            username="@ScienceToday",
            get_bot_member=_ok_admin,
        )
        assert ch.username == "ScienceToday"
        assert not ch.enabled

        # Second channel exceeds max_channels=1 quota
        with pytest.raises(QuotaExceeded):
            await register_channel(
                conn,
                actor=owner,
                channel_id=-1002222222222,
                title="Second",
                get_bot_member=_ok_admin,
            )

        # Customize settings, add a source, then reset settings while preserving sources
        await add_source(
            conn,
            actor=owner,
            channel_id=ch.channel_id,
            url="https://sci.example.com/feed.xml",
        )
        custom = ChannelSettings(publish_interval_minutes=15, min_score_threshold=85)
        updated = await update_channel_settings(
            conn, actor=owner, channel_id=ch.channel_id, settings=custom
        )
        assert updated.settings.publish_interval_minutes == 15

        enabled_ch = await set_channel_enabled(
            conn, actor=owner, channel_id=ch.channel_id, enabled=True
        )
        assert enabled_ch.enabled

        reset_ch = await reset_channel_settings(conn, actor=owner, channel_id=ch.channel_id)
        assert reset_ch.settings.publish_interval_minutes == 60
        assert len(await list_sources(conn, actor=owner, channel_id=ch.channel_id)) == 1


@pytest.mark.asyncio
async def test_concurrent_channel_transfer_token_redemption_and_sibling_invalidation(
    db: Database,
) -> None:
    sa = Actor(user_id=100, role=Role.SUPERADMIN)
    owner = Actor(user_id=3001, role=Role.ADMIN)
    recipient1 = Actor(user_id=3002, role=Role.ADMIN)
    recipient2 = Actor(user_id=3003, role=Role.ADMIN)

    async with db.transaction() as conn:
        await register_or_update_user(conn, user_id=100, bootstrap_superadmins={100})
        plan = await create_plan(
            conn,
            actor=sa,
            name_fa="پلن استاندارد",
            name_en="Standard",
            price_tmn=200_000,
            price_usd=1_000,
            duration_days=30,
            daily_post_limit=10,
            max_sources=5,
            max_channels=2,
        )
        for uid in (3001, 3002, 3003):
            await register_or_update_user(conn, user_id=uid)
            await assign_or_revoke_user_plan(
                conn,
                actor=sa,
                target_user_id=uid,
                plan_id=plan.plan_id,
                idempotency_key=f"asgn:{uid}",
            )

        ch = await register_channel(
            conn, actor=owner, channel_id=-1003333333333, title="Transferable Channel"
        )
        raw_tok1, _tok_rec1 = await issue_transfer_token(
            conn, actor=owner, channel_id=ch.channel_id, ttl_hours=24
        )
        _raw_tok2, tok_rec2 = await issue_transfer_token(
            conn, actor=owner, channel_id=ch.channel_id, ttl_hours=24
        )
        _raw_tok3, _tok_rec3 = await issue_transfer_token(
            conn, actor=owner, channel_id=ch.channel_id, ttl_hours=24
        )
        await revoke_transfer_token(
            conn, actor=owner, channel_id=ch.channel_id, token_id=tok_rec2.token_id
        )
        active_tokens = await list_active_transfer_tokens(
            conn, actor=owner, channel_id=ch.channel_id
        )
        assert len(active_tokens) == 2

    # Two recipients race to redeem `raw_tok1` concurrently -> exactly one wins, the other gets Conflict!
    async def _redeem(actor: Actor) -> Any:
        async with db.transaction() as c:
            return await redeem_transfer_token(c, actor=actor, raw_token=raw_tok1)

    outcomes = await asyncio.gather(
        _redeem(recipient1), _redeem(recipient2), return_exceptions=True
    )
    successes = [o for o in outcomes if not isinstance(o, Exception)]
    errors = [o for o in outcomes if isinstance(o, Exception)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], Conflict)

    winner_Channel = successes[0]
    assert winner_Channel.original_owner_id == 3001
    assert winner_Channel.owner_id in (3002, 3003)

    # All sibling unconsumed tokens for that channel were automatically invalidated
    winner_actor = Actor(user_id=winner_Channel.owner_id, role=Role.ADMIN)
    async with db.transaction() as conn:
        remaining = await list_active_transfer_tokens(
            conn, actor=winner_actor, channel_id=ch.channel_id
        )
        assert remaining == []


@pytest.mark.asyncio
async def test_source_polling_deduplication_simhash_ad_filtering_and_cooldown(
    db: Database, fernet_key: str
) -> None:
    sa = Actor(user_id=100, role=Role.SUPERADMIN)
    owner = Actor(user_id=4001, role=Role.ADMIN)
    keyring = FieldCipher([fernet_key])

    async with db.transaction() as conn:
        await register_or_update_user(conn, user_id=100, bootstrap_superadmins={100})
        await register_or_update_user(conn, user_id=4001)
        plan = await create_plan(
            conn,
            actor=sa,
            name_fa="حرفه‌ای",
            name_en="Pro",
            price_tmn=400_000,
            price_usd=2_000,
            duration_days=30,
            daily_post_limit=20,
            max_sources=2,
            max_channels=2,
        )
        await assign_or_revoke_user_plan(
            conn, actor=sa, target_user_id=4001, plan_id=plan.plan_id, idempotency_key="asgn:4001"
        )
        ch = await register_channel(
            conn, actor=owner, channel_id=-1004444444444, title="AI Research Channel"
        )
        src1 = await add_source(
            conn,
            actor=owner,
            channel_id=ch.channel_id,
            url="https://ai.example.com/feed.xml?utm_source=test",
            discovery_type="rss",
            custom_api_key="secret-api-token",
            keyring=keyring,
        )
        assert src1.canonical_url == "https://ai.example.com/feed.xml"
        assert src1.has_custom_api_key

        # Duplicate source with tracking parameters is rejected
        with pytest.raises(Conflict):
            await add_source(
                conn,
                actor=owner,
                channel_id=ch.channel_id,
                url="https://ai.example.com/feed.xml?fbclid=123",
            )

        src2 = await add_source(
            conn,
            actor=owner,
            channel_id=ch.channel_id,
            url="https://failing.example.com/rss",
            discovery_type="rss",
        )

        # Third source exceeds max_sources=2 quota
        with pytest.raises(QuotaExceeded):
            await add_source(
                conn,
                actor=owner,
                channel_id=ch.channel_id,
                url="https://third.example.com/rss",
            )

    rss_payload = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>معماری جدید ترنسفورمر برای پردازش مقالات علمی</title>
          <link>https://ai.example.com/posts/transformer-2026</link>
          <description>خلاصه اولیه</description>
        </item>
        <item>
          <title>رپورتاژ آگهی: خرید سرور ابری ارزان</title>
          <link>https://ai.example.com/posts/sponsored-cloud</link>
          <description>همین حالا بخرید با کد تخفیف ویژه</description>
        </item>
      </channel>
    </rss>
    """
    article_html = """
    <html>
      <head><meta property="og:image" content="https://ai.example.com/img/arch.png" /></head>
      <body>
        <article>
          <p>پژوهشگران آزمایشگاه هوش مصنوعی موفق به طراحی یک معماری جدید ترنسفورمر شده‌اند که حافظه مورد نیاز برای پردازش متون بلند علمی را به یک‌چهارم کاهش می‌دهد و سرعت استنتاج را دو برابر می‌کند.</p>
        </article>
      </body>
    </html>
    """
    mock_http = _MockHttp(
        {
            "https://ai.example.com/feed.xml": _make_resp(
                "https://ai.example.com/feed.xml",
                rss_payload,
                content_type="application/rss+xml",
            ),
            "https://ai.example.com/posts/transformer-2026": _make_resp(
                "https://ai.example.com/posts/transformer-2026", article_html
            ),
            "https://ai.example.com/posts/sponsored-cloud": _make_resp(
                "https://ai.example.com/posts/sponsored-cloud",
                "<article><p>رپورتاژ آگهی: بهترین سرور ابری با کد تخفیف</p></article>",
            ),
        }
    )

    async with db.transaction() as conn:
        ingested = await poll_and_extract_source(
            conn, source_id=src1.source_id, http=mock_http, keyring=keyring
        )
        assert len(ingested) == 2
        assert ingested[0].accepted and ingested[0].article is not None
        assert ingested[0].article.status == "extracted"
        assert ingested[0].article.media_urls == ("https://ai.example.com/img/arch.png",)
        # Second item was rejected by the deterministic ad heuristic
        assert not ingested[1].accepted
        assert ingested[1].article is not None
        assert ingested[1].article.status == "rejected_ad_heuristic"

        # Near-duplicate article on a DIFFERENT URL in the same channel is caught by SimHash fingerprint
        syndicated = await ingest_discovered_article(
            conn,
            channel_id=ch.channel_id,
            source_id=src1.source_id,
            url="https://mirror.example.com/repost/transformer-2026",
            title="معماری جدید ترنسفورمر برای پردازش مقالات علمی",
            raw_body=(
                "پژوهشگران آزمایشگاه هوش مصنوعی موفق به طراحی یک معماری جدید ترنسفورمر شده‌اند "
                "که حافظه مورد نیاز برای پردازش متون بلند علمی را به یک‌چهارم کاهش می‌دهد و سرعت استنتاج را ۲ برابر می‌کند."
            ),
        )
        assert not syndicated.accepted
        assert syndicated.rejection_reason == "near_duplicate_fingerprint"

        # Polling failing source records failure_count and sets exponential cooldown_until
        await poll_and_extract_source(conn, source_id=src2.source_id, http=mock_http)
        sources_after = await list_sources(conn, actor=owner, channel_id=ch.channel_id)
        s2_after = next(s for s in sources_after if s.source_id == src2.source_id)
        assert s2_after.failure_count == 1
        assert s2_after.cooldown_until is not None

        # Clear article queue removes queued articles
        cleared = await clear_article_queue(conn, actor=owner, channel_id=ch.channel_id)
        assert cleared == 1
