from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from opengenerate.application.ports import LlmRequest, LlmResponse
from opengenerate.domain.channels import ChannelSettings
from opengenerate.domain.errors import Conflict, TransientError, UnknownOutcomeError
from opengenerate.domain.roles import Actor, Role
from opengenerate.infrastructure.crypto import FieldCipher
from opengenerate.infrastructure.db.ai_store import (
    list_ai_models,
    process_article_with_ai,
    register_ai_model,
)
from opengenerate.infrastructure.db.billing_store import (
    assign_or_revoke_user_plan,
    create_plan,
    get_daily_usage,
)
from opengenerate.infrastructure.db.channel_store import register_channel, update_channel_settings
from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.db.identity_store import register_or_update_user
from opengenerate.infrastructure.db.publish_store import (
    publish_ready_article,
    resolve_deeplink_continuation,
    send_manual_test_post,
)
from opengenerate.infrastructure.db.source_store import ingest_discovered_article


class _FakeMultiProviderLlm:
    def __init__(self, responses_by_model: dict[str, Any]) -> None:
        self.responses_by_model = responses_by_model
        self.calls: list[str] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls.append(request.model)
        action = self.responses_by_model.get(request.model)
        if isinstance(action, Exception):
            raise action
        if callable(action):
            return action(request)
        return LlmResponse(
            text=json.dumps(action, ensure_ascii=False),
            prompt_tokens=250,
            completion_tokens=400,
            model=request.model,
        )


class _FakeBotPublisher:
    def __init__(self, *, mode: str = "ok") -> None:
        self.mode = mode
        self.sent: list[tuple[str, int | str, dict[str, Any]]] = []

    async def _handle(self, method: str, chat_id: int | str, info: dict[str, Any]) -> dict[str, Any]:
        if self.mode == "unknown_outcome":
            raise UnknownOutcomeError("socket dropped after send")
        if self.mode == "pre_send_error":
            raise TransientError("connection refused before send")
        self.sent.append((method, chat_id, info))
        return {"message_id": 5000 + len(self.sent)}

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: Mapping[str, Any] | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        return await self._handle(
            "sendMessage",
            chat_id,
            {"text": text, "parse_mode": parse_mode, "reply_markup": reply_markup},
        )

    async def send_rich_message(
        self,
        chat_id: int | str,
        *,
        blocks: list[dict[str, Any]],
        fallback_html: str,
        reply_markup: Mapping[str, Any] | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        return await self._handle(
            "sendRichMessage",
            chat_id,
            {"blocks": blocks, "fallback_html": fallback_html, "reply_markup": reply_markup},
        )

    async def send_photo(
        self,
        chat_id: int | str,
        photo: str,
        *,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._handle(
            "sendPhoto",
            chat_id,
            {"photo": photo, "caption": caption, "reply_markup": reply_markup},
        )

    async def send_video(
        self,
        chat_id: int | str,
        video: str,
        *,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._handle("sendVideo", chat_id, {"video": video, "caption": caption})

    async def send_media_group(
        self,
        chat_id: int | str,
        media: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        res = await self._handle("sendMediaGroup", chat_id, {"media": media})
        return [res]


@pytest.mark.asyncio
async def test_multi_provider_ai_failover_quarantine_caching_and_scientific_structuring(
    db: Database, fernet_key: str
) -> None:
    sa = Actor(user_id=100, role=Role.SUPERADMIN)
    owner = Actor(user_id=6001, role=Role.ADMIN)
    keyring = FieldCipher([fernet_key])

    async with db.transaction() as conn:
        await register_or_update_user(conn, user_id=100, bootstrap_superadmins={100})
        await register_or_update_user(conn, user_id=6001)
        plan = await create_plan(
            conn,
            actor=sa,
            name_fa="علمی ویژه",
            name_en="Scientific Pro",
            price_tmn=500_000,
            price_usd=2_500,
            duration_days=30,
            daily_post_limit=10,
            max_sources=5,
            max_channels=2,
            daily_test_limit=2,
        )
        await assign_or_revoke_user_plan(
            conn, actor=sa, target_user_id=6001, plan_id=plan.plan_id, idempotency_key="asgn:6001"
        )
        ch = await register_channel(
            conn, actor=owner, channel_id=-1006666666661, title="Quantum & AI Journal"
        )

        # Register 1 personal OpenAI model (priority 10) and 1 global Gemini model (priority 50)
        m_personal = await register_ai_model(
            conn,
            actor=owner,
            provider_type="openai",
            name="Personal GPT",
            base_url="https://api.openai.com",
            model_identifier="gpt-4o-mini-failing",
            api_key="sk-personal",
            priority=10,
            keyring=keyring,
        )
        await register_ai_model(
            conn,
            actor=sa,
            provider_type="gemini",
            name="Global Gemini",
            base_url="https://generativelanguage.googleapis.com",
            model_identifier="gemini-2.5-flash",
            api_key="goog-key",
            is_global=True,
            priority=50,
            keyring=keyring,
        )

        ing1 = await ingest_discovered_article(
            conn,
            channel_id=ch.channel_id,
            source_id=None,
            url="https://arxiv.example.org/abs/2606.0001",
            title="مقایسه بنچمارک معماری‌های ترنسفورمر کوانتومی",
            raw_body=(
                "در این مقاله نتایج مقایسه تجربی سه معماری شبکه عصبی کوانتومی روی پردازنده‌های "
                "ابرسانا ارائه شده و دقت و زمان استنتاج آن‌ها در جدول زیر مقایسه شده است."
            ),
        )
        assert ing1.article is not None
        art1_id = ing1.article.article_id

        ing2 = await ingest_discovered_article(
            conn,
            channel_id=ch.channel_id,
            source_id=None,
            url="https://arxiv.example.org/abs/2606.0002",
            title="مقاله دوم برای تست کش",
            raw_body="متن علمی دوم برای بررسی قرنطینه شدن مدل معیوب پس از سه خطا.",
        )
        assert ing2.article is not None
        art2_id = ing2.article.article_id

        ing3 = await ingest_discovered_article(
            conn,
            channel_id=ch.channel_id,
            source_id=None,
            url="https://arxiv.example.org/abs/2606.0003",
            title="مقاله سوم با امتیاز پایین",
            raw_body="متن بسیار کوتاه و کم‌عمق که امتیاز زیر حد نصاب دریافت می‌کند.",
        )
        assert ing3.article is not None
        art3_id = ing3.article.article_id

    gemini_payload = {
        "is_advertisement": False,
        "ad_reason": None,
        "scientific_domain": "computer_science_ai",
        "presentation_format": "tabular",
        "category_name": "هوش مصنوعی کوانتومی",
        "criteria_scores": {
            "relevance": 95,
            "novelty": 92,
            "depth": 90,
            "clarity": 88,
            "credibility": 94,
            "engagement": 86,
        },
        "generated_title": "جهش دقت در ترنسفورمرهای کوانتومی نسل جدید",
        "generated_post_body": "مقایسه عملکرد سه معماری برتر روی بنچمارک استاندارد:",
        "comparison_table_headers": ["معماری", "دقت", "تاخیر"],
        "comparison_table_rows": [["Q-Transformer", "99.1%", "8ms"], ["Baseline", "93.4%", "29ms"]],
        "key_points": ["کاهش مصرف حافظه کوانتومی", "پایداری در برابر نویز حرارتی"],
        "hashtags": ["کوانتوم", "هوش_مصنوعی"],
        "continuation_markdown": (
            "# تحلیل کامل معماری Q-Transformer\n\n"
            "| لایه | تعداد گیت | خطا |\n| --- | --- | --- |\n| Attention | 128 | 0.001 |\n\n"
            ":::details اثبات همگرایی الگوریتم\n"
            "همگرایی با نرخ نمایی در فضای هیلبرت تضمین شده است.\n:::\n\n"
            "```python\nqc.measure_all()\n```"
        ),
    }

    fake_llm = _FakeMultiProviderLlm(
        {
            "gpt-4o-mini-failing": TransientError("503 upstream overloaded"),
            "gemini-2.5-flash": gemini_payload,
        }
    )

    async with db.transaction() as conn:
        # 1st article: personal model fails (failure_count=1), fails over to Gemini and succeeds!
        out1 = await process_article_with_ai(
            conn, article_id=art1_id, llm_client=fake_llm, keyring=keyring
        )
        assert out1.scientific_domain == "computer_science_ai"
        assert out1.presentation_format == "tabular"
        assert out1.weighted_score > 90.0
        assert len(out1.continuation_blocks) == 4
        assert fake_llm.calls == ["gpt-4o-mini-failing", "gemini-2.5-flash"]

        # Re-processing art1_id hits `ai_cache` without making any new LLM calls!
        calls_before = len(fake_llm.calls)
        await process_article_with_ai(
            conn, article_id=art1_id, llm_client=fake_llm, keyring=keyring
        )
        assert len(fake_llm.calls) == calls_before

        # 2nd and 3rd articles trigger failure_count=2 and failure_count=3 on `gpt-4o-mini-failing`,
        # quarantining it automatically!
        await process_article_with_ai(
            conn, article_id=art2_id, llm_client=fake_llm, keyring=keyring
        )
        low_score_payload = dict(
            gemini_payload,
            criteria_scores={
                "relevance": 20,
                "novelty": 20,
                "depth": 20,
                "clarity": 20,
                "credibility": 20,
                "engagement": 20,
            },
        )
        fake_llm.responses_by_model["gemini-2.5-flash"] = low_score_payload
        out3 = await process_article_with_ai(
            conn, article_id=art3_id, llm_client=fake_llm, keyring=keyring
        )
        assert out3.weighted_score == pytest.approx(20.0)

        models = await list_ai_models(conn, actor=owner)
        personal_after = next(m for m in models if m.model_id == m_personal.model_id)
        assert personal_after.failure_count == 3
        assert personal_after.health_status == "degraded"
        assert personal_after.quarantined_until is not None

        usage = await get_daily_usage(conn, 6001)
        assert usage.ai_calls_count == 3
        assert usage.ai_tokens_count == 3 * 650


@pytest.mark.asyncio
async def test_crash_safe_pub_journal_deeplink_continuation_and_quota_safety(
    db: Database, fernet_key: str
) -> None:
    sa = Actor(user_id=100, role=Role.SUPERADMIN)
    owner = Actor(user_id=7001, role=Role.ADMIN)
    keyring = FieldCipher([fernet_key])

    async with db.transaction() as conn:
        await register_or_update_user(conn, user_id=100, bootstrap_superadmins={100})
        await register_or_update_user(conn, user_id=7001)
        plan = await create_plan(
            conn,
            actor=sa,
            name_fa="استاندارد",
            name_en="Standard",
            price_tmn=300_000,
            price_usd=1_500,
            duration_days=30,
            daily_post_limit=5,
            max_sources=5,
            max_channels=2,
            daily_test_limit=2,
        )
        await assign_or_revoke_user_plan(
            conn, actor=sa, target_user_id=7001, plan_id=plan.plan_id, idempotency_key="asgn:7001"
        )
        ch = await register_channel(
            conn, actor=owner, channel_id=-1007777777771, title="Medical Science Channel"
        )
        await update_channel_settings(
            conn,
            actor=owner,
            channel_id=ch.channel_id,
            settings=ChannelSettings(media_mode="none", continuation_in_bot_enabled=True),
        )
        await register_ai_model(
            conn,
            actor=sa,
            provider_type="openai",
            name="Global GPT",
            base_url="https://api.openai.com",
            model_identifier="gpt-4o",
            api_key="sk-global",
            is_global=True,
            keyring=keyring,
        )

        article_ids: list[int] = []
        for idx in range(1, 4):
            ing = await ingest_discovered_article(
                conn,
                channel_id=ch.channel_id,
                source_id=None,
                url=f"https://med.example.org/study/{idx}",
                title=f"پژوهش بالینی شماره {idx}",
                raw_body=f"نتایج کارآزمایی بالینی فاز سوم برای مطالعه شماره {idx} منتشر شد.",
            )
            assert ing.article is not None
            article_ids.append(ing.article.article_id)

        fake_llm = _FakeMultiProviderLlm(
            {
                "gpt-4o": {
                    "is_advertisement": False,
                    "scientific_domain": "medicine_clinical",
                    "presentation_format": "itemized",
                    "category_name": "پزشکی بالینی",
                    "criteria_scores": {
                        "relevance": 90,
                        "novelty": 90,
                        "depth": 90,
                        "clarity": 90,
                        "credibility": 90,
                        "engagement": 90,
                    },
                    "generated_title": "نتایج بالینی درمان نوین",
                    "generated_post_body": "خلاصه یافته‌های بالینی:",
                    "key_points": ["اثربخشی ۸۸ درصدی", "عوارض جانبی ناچیز"],
                    "hashtags": ["پزشکی", "درمان"],
                    "continuation_markdown": (
                        "# گزارش کامل کارآزمایی بالینی\n\n"
                        "| گروه | بهبود |\n| --- | --- |\n| دارو | 88% |\n| دارونما | 14% |\n\n"
                        ":::details پروتکل دوز مصرفی\nدوز روزانه ۱۰ میلی‌گرم به مدت ۱۴ روز.\n:::"
                    ),
                }
            }
        )
        for aid in article_ids:
            await process_article_with_ai(
                conn, article_id=aid, llm_client=fake_llm, keyring=keyring
            )

    # 1) Happy-path publication + deep-link continuation resolution
    bot_ok = _FakeBotPublisher(mode="ok")
    pub1 = await publish_ready_article(
        db, article_id=article_ids[0], bot_api=bot_ok, bot_username="OpenGenerateBot"
    )
    assert pub1.phase == "settled"
    assert pub1.telegram_msg_id == 5001
    assert pub1.deeplink_key is not None and pub1.deeplink_key.startswith("dl_")

    # Idempotent replay with same key does not re-send or double-debit quota
    replay = await publish_ready_article(
        db, article_id=article_ids[0], bot_api=bot_ok, bot_username="OpenGenerateBot"
    )
    assert replay.replayed
    assert len(bot_ok.sent) == 1

    async with db.transaction() as conn:
        assert (await get_daily_usage(conn, 7001)).posts_count == 1
        cont = await resolve_deeplink_continuation(conn, key=pub1.deeplink_key)
        assert cont.views_count == 1
        assert cont.scientific_domain == "medicine_clinical"
        assert any(b["kind"] == "table" for b in cont.rich_blocks)
        assert any(b["kind"] == "collapsible" for b in cont.rich_blocks)
        assert "<pre>┌─" in cont.html_chunks[0]

    # 2) Ambiguous network error after send -> `delivery_unknown` (never blindly resent!)
    bot_unknown = _FakeBotPublisher(mode="unknown_outcome")
    pub2 = await publish_ready_article(db, article_id=article_ids[1], bot_api=bot_unknown)
    assert pub2.phase == "delivery_unknown"
    with pytest.raises(Conflict):
        await publish_ready_article(db, article_id=article_ids[1], bot_api=bot_ok)

    # 3) Definite pre-send failure -> quota released back to previous count (2) and marked `failed_safe`
    bot_fail = _FakeBotPublisher(mode="pre_send_error")
    with pytest.raises(TransientError):
        await publish_ready_article(db, article_id=article_ids[2], bot_api=bot_fail)

    async with db.transaction() as conn:
        # Article 1 consumed 1 quota, Article 2 (delivery_unknown) kept 1 reserved, Article 3 released its quota!
        assert (await get_daily_usage(conn, 7001)).posts_count == 2

    # 4) Manual test post reserves `tests_count` and releases on pre-send failure
    test_msg_id = await send_manual_test_post(
        db,
        user_id=7001,
        channel_id=ch.channel_id,
        sample_title="تست قالب‌بندی علمی",
        sample_markdown="| شاخص | مقدار |\n| --- | --- |\n| SNR | 42dB |",
        bot_api=bot_ok,
    )
    assert test_msg_id == 5002
    with pytest.raises(TransientError):
        await send_manual_test_post(
            db,
            user_id=7001,
            channel_id=ch.channel_id,
            sample_title="تست ناموفق",
            sample_markdown="متن تست",
            bot_api=bot_fail,
        )
    async with db.transaction() as conn:
        assert (await get_daily_usage(conn, 7001)).tests_count == 1
