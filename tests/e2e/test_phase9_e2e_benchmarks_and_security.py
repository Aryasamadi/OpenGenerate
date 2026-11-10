"""Phase 9 E2E Verification, Performance Benchmarks, Security Redaction & 11-Deliverable Audit."""

from __future__ import annotations

import json
import time
import tracemalloc
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from opengenerate.application.ports import LlmRequest, LlmResponse
from opengenerate.domain.editorial import (
    detect_scientific_domain_and_format,
    parse_rich_markdown_to_blocks,
    render_blocks_to_telegram_html,
    render_rich_message_payload,
)
from opengenerate.domain.money import Currency
from opengenerate.domain.roles import Actor, Role
from opengenerate.domain.sources import canonicalize_url, compute_content_fingerprint
from opengenerate.infrastructure.crypto import FieldCipher
from opengenerate.infrastructure.db.ai_store import (
    process_article_with_ai,
    register_ai_model,
)
from opengenerate.infrastructure.db.billing_store import create_discount, create_plan
from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.db.identity_store import register_or_update_user
from opengenerate.infrastructure.db.ledger import get_balance
from opengenerate.infrastructure.db.publish_store import publish_ready_article
from opengenerate.infrastructure.db.source_store import ingest_discovered_article
from opengenerate.infrastructure.logging import redact_text
from opengenerate.interfaces.telegram.router import TelegramUiRouter


class _E2ELlmAdapter:
    async def complete(self, request: LlmRequest) -> LlmResponse:
        payload = {
            "is_advertisement": False,
            "ad_reason": None,
            "scientific_domain": "computer_science_ai",
            "presentation_format": "tabular",
            "category_name": "هوش مصنوعی و کوانتوم",
            "criteria_scores": {
                "relevance": 96,
                "novelty": 94,
                "depth": 91,
                "clarity": 90,
                "credibility": 95,
                "engagement": 89,
            },
            "generated_title": "پیشرفت بزرگ در الگوریتم‌های یادگیری کوانتومی",
            "generated_post_body": "مقایسه دقیق سه معماری کوانتومی در جدول زیر:",
            "comparison_table_headers": ["معماری", "دقت (%)", "تأخیر (ms)"],
            "comparison_table_rows": [
                ["Q-Transformer", "98.4", "12"],
                ["Variational-QC", "94.1", "28"],
            ],
            "key_points": [
                "کاهش ۵۰ درصدی خطای رمزگشایی کیوبیت‌ها",
                "افزایش سرعت همگرایی در شبکه‌های عمیق",
            ],
            "continuation_markdown": (
                "## تحلیل کامل ریاضی و آزمایشگاهی\n\n"
                "| مدل | دقت | مصرف انرژی |\n"
                "|---|---|---|\n"
                "| Q-Transformer | 98.4% | 15W |\n\n"
                ":::details جزئیات اثبات قضیه\n"
                "اثبات همگرایی بر پایه نابرابری طیف عملگر هرمیتی انجام شده است.\n"
                ":::\n\n"
                "```python\n"
                "def quantum_fidelity(state_a, state_b):\n"
                "    return abs(state_a @ state_b.conj()) ** 2\n"
                "```"
            ),
            "hashtags": ["کوانتوم", "هوش_مصنوعی"],
        }
        return LlmResponse(
            text=json.dumps(payload, ensure_ascii=False),
            prompt_tokens=300,
            completion_tokens=450,
            model=request.model,
        )


class _E2EBotPublisher:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, int | str, dict[str, Any]]] = []

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: Mapping[str, Any] | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        self.sent_messages.append(
            ("sendMessage", chat_id, {"text": text, "reply_markup": reply_markup})
        )
        return {"message_id": 7000 + len(self.sent_messages)}

    async def send_rich_message(
        self,
        chat_id: int | str,
        *,
        blocks: list[dict[str, Any]],
        fallback_html: str,
        reply_markup: Mapping[str, Any] | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        self.sent_messages.append(
            (
                "sendRichMessage",
                chat_id,
                {
                    "blocks": blocks,
                    "fallback_html": fallback_html,
                    "reply_markup": reply_markup,
                },
            )
        )
        return {"message_id": 7000 + len(self.sent_messages)}

    async def send_photo(
        self,
        chat_id: int | str,
        photo: str,
        *,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.sent_messages.append(("sendPhoto", chat_id, {"photo": photo, "caption": caption}))
        return {"message_id": 7000 + len(self.sent_messages)}

    async def send_video(
        self,
        chat_id: int | str,
        video: str,
        *,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.sent_messages.append(("sendVideo", chat_id, {"video": video, "caption": caption}))
        return {"message_id": 7000 + len(self.sent_messages)}

    async def send_media_group(
        self,
        chat_id: int | str,
        media: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        self.sent_messages.append(("sendMediaGroup", chat_id, {"media": media}))
        return [{"message_id": 7000 + len(self.sent_messages)}]


@pytest.mark.asyncio
async def test_complete_end_to_end_platform_lifecycle(
    db: Database,
    fernet_key: str,
) -> None:
    """Full lifecycle: Referral -> Free Plan -> Channel & Source -> AI Scientific Post -> DeepLink Reader -> Paid Upgrade & Referral Reward."""
    super_id = 999001
    referrer_id = 888001
    customer_id = 777001
    reader_id = 666001
    super_actor = Actor(user_id=super_id, role=Role.SUPERADMIN)
    keyring = FieldCipher([fernet_key])
    bot_pub = _E2EBotPublisher()

    async with db.transaction() as conn:
        await register_or_update_user(conn, user_id=super_id, bootstrap_superadmins={super_id})
        free_plan = await create_plan(
            conn,
            actor=super_actor,
            name_fa="پلن رایگان",
            name_en="Free",
            duration_days=3,
            daily_post_limit=5,
            max_sources=5,
            max_channels=2,
            daily_test_limit=5,
            is_free=True,
        )
        pro_plan = await create_plan(
            conn,
            actor=super_actor,
            name_fa="پلن الماس",
            name_en="Diamond",
            duration_days=30,
            daily_post_limit=50,
            max_sources=20,
            max_channels=5,
            daily_test_limit=20,
            price_tmn=1_000_000,
            price_usd=3_000,
            is_free=False,
        )
        await create_discount(conn, actor=super_actor, code="E2E20", percent=20, max_uses=5)
        await register_ai_model(
            conn,
            actor=super_actor,
            provider_type="openai",
            name="E2E Global Model",
            base_url="https://api.openai.com/v1",
            model_identifier="gpt-4o",
            api_key="sk-e2e-secret-key",
            is_global=True,
            priority=10,
            keyring=keyring,
        )

    router = TelegramUiRouter(
        db, bootstrap_superadmins={super_id}, keyring=keyring, bot_api=bot_pub
    )

    # 1. Referrer joins & selects Persian
    await router.dispatch(user_id=referrer_id, trigger="/start")
    await router.dispatch(user_id=referrer_id, trigger="u:lang:fa")

    # 2. Customer joins via referral deep-link, claims free trial, registers channel & source
    await router.dispatch(user_id=customer_id, trigger=f"/start ref_{referrer_id}")
    await router.dispatch(user_id=customer_id, trigger="u:lang:fa")
    await router.dispatch(user_id=customer_id, trigger=f"u:free:{free_plan.plan_id}")

    await router.dispatch(user_id=customer_id, trigger="a:chadd")
    await router.dispatch_message_input(user_id=customer_id, text_body="-1008888888888")
    cid = -1008888888888

    await router.dispatch(user_id=customer_id, trigger=f"a:srca:{cid}")
    await router.dispatch_message_input(
        user_id=customer_id, text_body="https://quantum-journal.example.org/rss.xml"
    )

    # 3. Ingest scientific article, score/format with AI, and publish to channel
    async with db.transaction() as conn:
        ing = await ingest_discovered_article(
            conn,
            channel_id=cid,
            source_id=None,
            url="https://quantum-journal.example.org/papers/q-transformer-2026",
            title="معماری Q-Transformer برای یادگیری ماشین کوانتومی",
            raw_body=(
                "در این پژوهش علمی، معماری Q-Transformer با دقت ۹۸.۴ درصد معرفی و "
                "جدول مقایسه بنچمارک و کدهای پیاده‌سازی آن ارائه شده است."
            ),
        )
        assert ing.article is not None
        art_id = ing.article.article_id

        ai_out = await process_article_with_ai(
            conn,
            article_id=art_id,
            llm_client=_E2ELlmAdapter(),
            keyring=keyring,
        )
        assert ai_out.scientific_domain == "computer_science_ai"
        assert ai_out.presentation_format == "tabular"
        assert ai_out.weighted_score > 90.0

    pub_res = await publish_ready_article(
        db,
        article_id=art_id,
        bot_api=bot_pub,
        bot_username="OpenGenerateBot",
        bootstrap_superadmins=[super_id],
    )
    assert pub_res.phase == "settled"
    assert pub_res.deeplink_key is not None and pub_res.deeplink_key.startswith("dl_")

    # 4. Reader clicks the channel's "ادامه مطلب" button (`/start dl_<key>`) in the bot
    reader_resp = await router.dispatch(
        user_id=reader_id, trigger=f"/start {pub_res.deeplink_key}"
    )
    assert reader_resp.action_id == "CMD_START"
    assert len(reader_resp.rich_blocks) >= 3
    assert "Q-Transformer" in reader_resp.text

    # 5. Customer upgrades to Pro plan with discount `E2E20`, submits receipt, Superadmin approves
    await router.dispatch(user_id=customer_id, trigger=f"u:disc:{pro_plan.plan_id}")
    await router.dispatch_message_input(user_id=customer_id, text_body="E2E20")
    await router.dispatch(user_id=customer_id, trigger=f"u:req:{pro_plan.plan_id}")
    pay_submitted = await router.dispatch_message_input(
        user_id=customer_id, text_body="رسید پرداخت بانکی ارتقاء به پلن الماس"
    )
    assert "#1" in pay_submitted.text

    approved = await router.dispatch(user_id=super_id, trigger="s:pay_ok:1")
    assert approved.action_id == "CB_SUPER_PAYMENTS"

    # Verify referrer received 20% of 800,000 TMN = 160,000 TMN in their wallet ledger!
    async with db.transaction() as conn:
        ref_bal = await get_balance(conn, referrer_id, Currency.TMN)
        assert ref_bal == 160_000


def test_performance_benchmarks_cpu_memory_and_rich_message_rendering() -> None:
    """Measure CPU latency and memory footprint for 100 scientific articles (Section 14.5)."""
    sample_md = (
        "## نتایج آزمایش بالینی و آماری\n\n"
        "| گروه | بهبود (%) | p-value |\n"
        "|---|---|---|\n"
        "| دارو A | 84.2% | 0.001 |\n"
        "| دارونما | 21.0% | 0.450 |\n\n"
        ":::details روش‌شناسی آماری\n"
        "آزمون دوطرفه تی استیودنت با سطح اطمینان ۹۹ درصد اجرا شد.\n"
        ":::\n\n"
        "- کاهش معنادار التهاب\n"
        "- پایداری پاسخ ایمنی در ۶ ماه\n"
    )

    # 1. Measure pure CPU throughput across 100 articles (without tracemalloc instrumentation overhead)
    t0 = time.perf_counter()
    for i in range(100):
        can_url = canonicalize_url(f"https://example.org/sci/paper-{i}?utm_source=feed")
        _fp = compute_content_fingerprint(f"عنوان علمی شماره {i}", sample_md)
        domain, fmt = detect_scientific_domain_and_format(
            title=f"کارآزمایی بالینی فاز ۳ دارو شماره {i}",
            body=sample_md,
        )
        assert domain == "medicine_clinical"
        assert fmt in ("statistical", "tabular", "itemized", "analytical")
        blocks = parse_rich_markdown_to_blocks(sample_md)
        _rich = render_rich_message_payload(blocks)
        _html = render_blocks_to_telegram_html(blocks)
        assert can_url.endswith(f"/sci/paper-{i}")
    elapsed = time.perf_counter() - t0

    # 2. Measure peak heap memory footprint via tracemalloc
    tracemalloc.start()
    for _i in range(20):
        blocks = parse_rich_markdown_to_blocks(sample_md)
        _ = render_rich_message_payload(blocks)
        _ = render_blocks_to_telegram_html(blocks)
    _current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert elapsed < 1.5, f"Benchmark exceeded 1.5s target: {elapsed:.3f}s"
    assert peak_mem < 15 * 1024 * 1024, f"Peak memory exceeded 15MB: {peak_mem} bytes"


def test_security_redaction_and_all_11_final_deliverables_present() -> None:
    """Verify secret redaction and presence of all 11 final engineering deliverables."""
    raw_secret_log = (
        "Failed calling https://api.telegram.org/bot123456789:AAH_secret_bot_token_value_1234567890/sendMessage "
        "with Authorization: Bearer sk-live-998877665544332211"
    )
    redacted = redact_text(raw_secret_log)
    assert "AAH_secret_bot_token_value" not in redacted
    assert "sk-live-998877665544332211" not in redacted

    repo_root = Path(__file__).resolve().parents[2]
    required_deliverables = [
        "docs/architecture/current-state.md",
        "docs/product/feature-inventory.md",
        "docs/product/ui-action-register.md",
        "docs/product/ui-action-register.json",
        "docs/architecture/current-risks.md",
        "docs/architecture/decision-matrix.md",
        "docs/architecture/decisions/README.md",
        "docs/architecture/target-blueprint.md",
        "docs/architecture/migration-plan.md",
        "docs/operations/runbook.md",
        "docs/release/final-handover-report.md",
        "Dockerfile",
        "docker-compose.yml",
        ".env.example",
        "deploy/install.sh",
        "deploy/rollback.sh",
        "deploy/systemd/opengenerate.service",
        "tools/migrate_sqlite_to_pg.py",
        "README.md",
    ]
    for rel in required_deliverables:
        p = repo_root / rel
        assert p.is_file() and p.stat().st_size > 100, f"Missing or empty deliverable: {rel}"
