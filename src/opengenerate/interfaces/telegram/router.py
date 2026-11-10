"""Complete Ergonomic Telegram UI Router covering all 38 Action IDs & 61 Triggers (`u:*`, `a:*`, `s:*`, `c:cancel`, commands)."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.application.policy import require_active, require_superadmin
from opengenerate.domain.channels import ChannelSettings
from opengenerate.domain.errors import AccessDenied, Conflict, NotFound
from opengenerate.domain.roles import Actor, Role
from opengenerate.domain.subscriptions import (
    DowngradeNotAllowedError,
    NegotiatedPlanError,
    currency_for_language,
)
from opengenerate.infrastructure import outbox
from opengenerate.infrastructure.crypto import FieldCipher
from opengenerate.infrastructure.db.ai_store import (
    delete_ai_model,
    list_ai_models,
    record_model_probe_result,
)
from opengenerate.infrastructure.db.billing_store import (
    approve_payment,
    assign_or_revoke_user_plan,
    claim_free_plan,
    create_plan,
    delete_discount,
    delete_or_deactivate_plan,
    get_daily_usage,
    get_discount,
    get_plan,
    list_discounts,
    list_plans,
    quote_plan_for_user,
    refund_payment,
    reject_payment,
    submit_payment_request,
    toggle_discount,
)
from opengenerate.infrastructure.db.channel_store import (
    delete_channel,
    get_channel,
    list_channels,
    redeem_transfer_token,
    register_channel,
    set_channel_enabled,
    update_channel_settings,
)
from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.db.identity_store import (
    MandatoryChannelConfig,
    ban_or_unban_user,
    check_mandatory_membership,
    get_mandatory_channel_config,
    get_referral_dashboard,
    get_referral_economics,
    get_user,
    list_users,
    register_or_update_user,
    reset_daily_usage,
    reset_free_plan_eligibility,
    set_mandatory_channel_config,
    set_user_language,
)
from opengenerate.infrastructure.db.ledger import get_balance
from opengenerate.infrastructure.db.publish_store import (
    BotPublishPort,
    publish_ready_article,
    resolve_deeplink_continuation,
)
from opengenerate.infrastructure.db.source_store import (
    add_source,
    clear_article_queue,
    configure_source_discovery,
    delete_source,
    get_source,
    list_sources,
    set_source_toggles,
)
from opengenerate.infrastructure.db.support_store import (
    close_support_ticket,
    reply_to_support_message,
    send_user_support_message,
)
from opengenerate.interfaces.telegram.ergonomics import (
    DEFAULT_SYSTEM_TEXTS,
    KeyboardGrid,
    Lang,
    build_confirmation_screen,
    format_money,
    get_system_text,
    norm_lang,
    render_breadcrumb,
    render_quota_gauge,
    render_status_badge,
    reset_custom_system_text,
)
from opengenerate.interfaces.telegram.session import (
    clear_quote_discount,
    clear_wizard_session,
    get_quote_discount,
    get_wizard_session,
    set_quote_discount,
    set_wizard_session,
)


@dataclass(frozen=True, slots=True)
class UiResponse:
    action_id: str
    text: str
    reply_markup: dict[str, Any] = field(default_factory=lambda: {"inline_keyboard": []})
    alert_text: str | None = None
    show_alert: bool = False
    rich_blocks: list[dict[str, Any]] = field(default_factory=list)
    extra_chunks: list[str] = field(default_factory=list)


RouteHandler = Callable[["TelegramUiRouter", AsyncConnection, Actor, Lang, str, re.Match[str]], Awaitable[UiResponse]]


@dataclass(frozen=True, slots=True)
class RouteSpec:
    action_id: str
    patterns: tuple[re.Pattern[str], ...]
    roles_allowed: frozenset[Role]
    destructive: bool
    handler: RouteHandler


class TelegramUiRouter:
    """Ergonomic Bilingual (`fa`/`en`) Telegram UI Router covering all 38 Action IDs & 61 Triggers."""

    def __init__(
        self,
        db: Database,
        *,
        bootstrap_superadmins: Collection[int] = (),
        keyring: FieldCipher | None = None,
        bot_api: BotPublishPort | None = None,
        get_chat_member: Any = None,
        bot_username: str = "OpenGenerateBot",
    ) -> None:
        self.db = db
        self.bootstrap_superadmins = set(bootstrap_superadmins)
        self.keyring = keyring
        self.bot_api = bot_api
        self.get_chat_member = get_chat_member
        self.bot_username = bot_username

    async def _resolve_actor_and_lang(
        self,
        conn: AsyncConnection,
        user_id: int,
        *,
        username: str | None = None,
        full_name: str | None = None,
        referral_param: str | None = None,
    ) -> tuple[Actor, Lang, bool]:
        start_res = await register_or_update_user(
            conn,
            user_id=user_id,
            username=username,
            full_name=full_name,
            referral_param=referral_param,
            bootstrap_superadmins=self.bootstrap_superadmins,
        )
        u = start_res.user
        actor = Actor(user_id=u.user_id, role=u.role, is_banned=u.is_banned)
        return actor, norm_lang(u.lang), start_res.needs_language_selection

    async def dispatch(
        self,
        *,
        user_id: int,
        trigger: str,
        username: str | None = None,
        full_name: str | None = None,
    ) -> UiResponse:
        """Dispatch any `/command` or `u:*` / `a:*` / `s:*` / `c:cancel` callback trigger."""
        raw = trigger.strip()
        ref_param: str | None = None
        if raw.startswith("/start"):
            parts = raw.split(maxsplit=1)
            if len(parts) == 2 and parts[1].startswith("ref_"):
                ref_param = parts[1].strip()

        async with self.db.transaction() as conn:
            actor, lang, _needs_lang = await self._resolve_actor_and_lang(
                conn,
                user_id,
                username=username,
                full_name=full_name,
                referral_param=ref_param,
            )
            require_active(actor)

            for spec in ROUTE_SPECS:
                for pat in spec.patterns:
                    m = pat.fullmatch(raw)
                    if m is not None:
                        if actor.role not in spec.roles_allowed:
                            raise AccessDenied(f"role {actor.role} not permitted for {spec.action_id}")
                        return await spec.handler(self, conn, actor, lang, raw, m)

            raise NotFound(f"unrecognized UI trigger: {raw}")

    async def dispatch_message_input(
        self,
        *,
        user_id: int,
        text_body: str | None = None,
        photo_file_id: str | None = None,
        document_file_id: str | None = None,
        replied_to_msg_id: int | None = None,
    ) -> UiResponse:
        """Handle guided wizard inputs, support messages, and superadmin support replies."""
        async with self.db.transaction() as conn:
            actor, lang, _ = await self._resolve_actor_and_lang(conn, user_id)
            require_active(actor)

            # 1) Superadmin replying to a forwarded support message
            if actor.is_superadmin and replied_to_msg_id is not None:
                kind = "photo" if photo_file_id else ("document" if document_file_id else "text")
                mapping = await reply_to_support_message(
                    conn,
                    actor=actor,
                    replied_to_msg_id=replied_to_msg_id,
                    kind=kind,  # type: ignore[arg-type]
                    text_body=text_body,
                    file_id=photo_file_id or document_file_id,
                    idempotency_key=f"sup_reply:{actor.user_id}:{replied_to_msg_id}:{text_body or ''}",
                )
                msg = (
                    f"✅ پاسخ شما به کاربر <code>{mapping.user_id}</code> (تیکت #{mapping.ticket_id}) ارسال شد."
                    if lang == "fa"
                    else f"✅ Reply queued for user <code>{mapping.user_id}</code> (Ticket #{mapping.ticket_id})."
                )
                return UiResponse(action_id="CB_USER_SUPPORT", text=msg)

            # 2) Active guided wizard session in `kv_cache`
            session = await get_wizard_session(conn, user_id=actor.user_id)
            if session is None:
                return await self._render_user_home(conn, actor, lang, "CMD_START")

            if session.step == "support_chat":
                kind = "photo" if photo_file_id else ("document" if document_file_id else "text")
                ticket = await send_user_support_message(
                    conn,
                    actor=actor,
                    kind=kind,  # type: ignore[arg-type]
                    text_body=text_body,
                    file_id=photo_file_id or document_file_id,
                    notify_superadmin_ids=list(self.bootstrap_superadmins),
                    idempotency_key=f"sup_msg:{actor.user_id}:{datetime.now(UTC).timestamp()}",
                )
                kb = KeyboardGrid().add_row(
                    ("🔒 بستن گفتگو" if lang == "fa" else "🔒 Close Support", "u:sclose")
                ).add_nav_footer(lang=lang)
                body = (
                    f"💬 <b>پیام شما در تیکت #{ticket.ticket_id} ثبت و برای پشتیبانی ارسال شد.</b>"
                    if lang == "fa"
                    else f"💬 <b>Your message was sent in Ticket #{ticket.ticket_id}.</b>"
                )
                return UiResponse(action_id="CB_USER_SUPPORT", text=body, reply_markup=kb.build())

            if session.step == "apply_discount":
                plan_id = int(session.context["plan_id"])
                code = (text_body or "").strip().upper()
                disc = await get_discount(conn, code)
                if not disc.is_usable_at(datetime.now(UTC)):
                    raise Conflict("discount_expired_or_exhausted")
                await set_quote_discount(
                    conn, user_id=actor.user_id, plan_id=plan_id, discount_code=disc.code
                )
                await clear_wizard_session(conn, user_id=actor.user_id)
                return await self._render_plan_detail(
                    conn, actor, lang, plan_id, "CB_USER_DISCOUNT_APPLY"
                )

            if session.step == "submit_receipt":
                plan_id = int(session.context["plan_id"])
                disc_code = await get_quote_discount(
                    conn, user_id=actor.user_id, plan_id=plan_id
                )
                quote = await quote_plan_for_user(
                    conn, actor=actor, plan_id=plan_id, discount_code=disc_code
                )
                r_type = "photo" if photo_file_id else ("document" if document_file_id else "text")
                pay = await submit_payment_request(
                    conn,
                    actor=actor,
                    quote=quote,
                    receipt_type=r_type,  # type: ignore[arg-type]
                    receipt_text=text_body,
                    receipt_file_id=photo_file_id or document_file_id,
                    idempotency_key=f"ui_pay:{actor.user_id}:{plan_id}:{datetime.now(UTC).timestamp()}",
                    notify_superadmin_ids=list(self.bootstrap_superadmins),
                )
                await clear_wizard_session(conn, user_id=actor.user_id)
                kb = KeyboardGrid().add_nav_footer(back_cb="u:plans", lang=lang)
                txt = (
                    f"✅ <b>رسید پرداخت شماره #{pay.payment_id} با موفقیت ثبت شد!</b>\n"
                    "پس از بررسی توسط مدیریت، اشتراک شما به صورت خودکار فعال خواهد شد."
                    if lang == "fa"
                    else f"✅ <b>Payment Request #{pay.payment_id} submitted for review!</b>"
                )
                return UiResponse(
                    action_id="CB_USER_REQUEST_PAYMENT", text=txt, reply_markup=kb.build()
                )

            if session.step == "register_channel":
                raw_in = (text_body or "").strip()
                await clear_wizard_session(conn, user_id=actor.user_id)
                if raw_in.startswith("ogch_"):
                    ch = await redeem_transfer_token(
                        conn,
                        actor=actor,
                        raw_token=raw_in,
                        bootstrap_superadmins=self.bootstrap_superadmins,
                    )
                    return await self._render_channel_dashboard(
                        conn, actor, lang, ch.channel_id, "CB_ADMIN_CHANNEL_ADD"
                    )
                cid = int(raw_in) if raw_in.lstrip("-").isdigit() else -1009999999999
                ch = await register_channel(
                    conn,
                    actor=actor,
                    channel_id=cid,
                    title=raw_in,
                    username=raw_in if raw_in.startswith("@") else None,
                    bootstrap_superadmins=self.bootstrap_superadmins,
                )
                return await self._render_channel_dashboard(
                    conn, actor, lang, ch.channel_id, "CB_ADMIN_CHANNEL_ADD"
                )

            if session.step == "add_source":
                cid = int(session.context["channel_id"])
                await clear_wizard_session(conn, user_id=actor.user_id)
                src = await add_source(
                    conn,
                    actor=actor,
                    channel_id=cid,
                    url=(text_body or "").strip(),
                    keyring=self.keyring,
                    bootstrap_superadmins=self.bootstrap_superadmins,
                )
                return await self._render_source_detail(
                    conn, actor, lang, src.source_id, "CB_ADMIN_SOURCE_ADD"
                )

            await clear_wizard_session(conn, user_id=actor.user_id)
            return await self._render_user_home(conn, actor, lang, "CMD_START")

    # ---- Shared & User Screens ----------------------------------------------------------------

    async def _render_language_picker(
        self, _conn: AsyncConnection, _actor: Actor, lang: Lang, action_id: str
    ) -> UiResponse:
        crumb = render_breadcrumb(["🌐 انتخاب زبان / Language"], lang=lang)
        txt = (
            f"{crumb}\n\n"
            "🇮🇷 لطفاً زبان مورد نظر خود را انتخاب کنید:\n"
            "🇬🇧 Please select your preferred language:"
        )
        kb = (
            KeyboardGrid()
            .add_row(("🇮🇷 فارسی (تومان - TMN)", "u:lang:fa"), ("🇬🇧 English ($ USD)", "u:lang:en"))
            .add_nav_footer(lang=lang)
        )
        return UiResponse(action_id=action_id, text=txt, reply_markup=kb.build())

    async def _render_user_home(
        self, conn: AsyncConnection, actor: Actor, lang: Lang, action_id: str
    ) -> UiResponse:
        user = await get_user(conn, actor.user_id, self.bootstrap_superadmins)
        cur = currency_for_language(lang)
        bal = await get_balance(conn, actor.user_id, cur)
        welcome = await get_system_text(conn, key="welcome", lang=lang)
        crumb = render_breadcrumb(["🏠 خانه" if lang == "fa" else "🏠 Home"], lang=lang)

        plan_line = "ندارد" if lang == "fa" else "None"
        if user.plan_id is not None:
            p = await get_plan(conn, user.plan_id)
            plan_line = p.name_fa if lang == "fa" else p.name_en

        wallet_str = format_money(bal, cur, lang=lang)
        summary = (
            f"\n\n👤 <b>اشتراک فعلی:</b> <code>{plan_line}</code>\n"
            f"👛 <b>موجودی کیف پول:</b> <code>{wallet_str}</code>"
            if lang == "fa"
            else f"\n\n👤 <b>Active Plan:</b> <code>{plan_line}</code>\n"
            f"👛 <b>Wallet Balance:</b> <code>{wallet_str}</code>"
        )

        kb = KeyboardGrid()
        if lang == "fa":
            kb.add_row(("💎 اشتراک‌ها و پلن‌ها", "u:plans"), ("⚙️ مدیریت کانال‌ها", "u:man"))
            kb.add_row(("🎁 دعوت از دوستان (رفرال)", "u:ref"), ("💬 پشتیبانی آنلاین", "u:sup"))
            kb.add_row(("📚 راهنما", "u:help"), ("🌐 تغییر زبان / Language", "u:lang"))
            if actor.is_superadmin:
                kb.add_row(("🛡️ پنل مدیریت کل (Superadmin)", "s:report"))
        else:
            kb.add_row(("💎 Plans & Subscriptions", "u:plans"), ("⚙️ Manage Channels", "u:man"))
            kb.add_row(("🎁 Invite & Earn", "u:ref"), ("💬 Support", "u:sup"))
            kb.add_row(("📚 Help", "u:help"), ("🌐 Language / تغییر زبان", "u:lang"))
            if actor.is_superadmin:
                kb.add_row(("🛡️ Superadmin Console", "s:report"))

        return UiResponse(
            action_id=action_id,
            text=f"{crumb}\n\n{welcome}{summary}",
            reply_markup=kb.build(),
        )

    async def _render_plans_catalog(
        self, conn: AsyncConnection, actor: Actor, lang: Lang, action_id: str
    ) -> UiResponse:
        plans = await list_plans(conn, active_only=True)
        cur = currency_for_language(lang)
        bal = await get_balance(conn, actor.user_id, cur)
        crumb = render_breadcrumb(
            ["🏠 خانه", "💎 اشتراک‌ها و پلن‌ها"] if lang == "fa" else ["🏠 Home", "💎 Plans"],
            lang=lang,
        )
        kb = KeyboardGrid()
        for p in plans:
            title = p.name_fa if lang == "fa" else p.name_en
            price = format_money(p.to_spec().tariff_for(cur), cur, lang=lang)
            badge = "🎁 " if p.is_free else "💎 "
            kb.add_row((f"{badge}{title} — {price}", f"u:plan:{p.plan_id}"))
        kb.add_nav_footer(back_cb="u:home", lang=lang)

        txt = (
            f"{crumb}\n\n"
            f"👛 <b>موجودی قابل استفاده کیف پول:</b> <code>{format_money(bal, cur, lang=lang)}</code>\n"
            "لطفاً یکی از پلن‌های زیر را جهت مشاهده جزئیات و پیش‌فاکتور انتخاب کنید:"
            if lang == "fa"
            else f"{crumb}\n\n"
            f"👛 <b>Spendable Wallet Balance:</b> <code>{format_money(bal, cur, lang=lang)}</code>\n"
            "Select a plan below to inspect quotas and live pricing:"
        )
        return UiResponse(action_id=action_id, text=txt, reply_markup=kb.build())

    async def _render_plan_detail(
        self,
        conn: AsyncConnection,
        actor: Actor,
        lang: Lang,
        plan_id: int,
        action_id: str,
    ) -> UiResponse:
        user = await get_user(conn, actor.user_id, self.bootstrap_superadmins)
        plan = await get_plan(conn, plan_id)
        cur = currency_for_language(lang)
        crumb = render_breadcrumb(
            ["🏠 خانه", "💎 پلن‌ها", plan.name_fa]
            if lang == "fa"
            else ["🏠 Home", "💎 Plans", plan.name_en],
            lang=lang,
        )
        kb = KeyboardGrid()

        if plan.is_free:
            if user.free_plan_used:
                kb.add_row(
                    (
                        "🚫 پلن رایگان قبلاً استفاده شده"
                        if lang == "fa"
                        else "🚫 Free Plan Already Used",
                        f"u:freeused:{plan.plan_id}",
                    )
                )
            else:
                kb.add_row(
                    (
                        "🎁 فعال‌سازی آنی پلن رایگان"
                        if lang == "fa"
                        else "🎁 Activate Free Plan Now",
                        f"u:free:{plan.plan_id}",
                    )
                )
            kb.add_nav_footer(back_cb="u:plans", lang=lang)
            txt = (
                f"{crumb}\n\n"
                f"🎁 <b>{plan.name_fa if lang == 'fa' else plan.name_en}</b>\n"
                f"⏳ مدت: <code>{plan.duration_days}</code> روز\n"
                f"📢 سقف کانال: <code>{plan.max_channels}</code> | 🌐 سقف منبع: <code>{plan.max_sources}</code>\n"
                f"📤 پست روزانه: <code>{plan.daily_post_limit}</code>"
            )
            return UiResponse(action_id=action_id, text=txt, reply_markup=kb.build())

        disc_code = await get_quote_discount(conn, user_id=actor.user_id, plan_id=plan.plan_id)
        try:
            quote = await quote_plan_for_user(
                conn, actor=actor, plan_id=plan.plan_id, discount_code=disc_code
            )
            if disc_code:
                kb.add_row(
                    (
                        f"❌ حذف کد تخفیف ({disc_code})"
                        if lang == "fa"
                        else f"❌ Remove Discount ({disc_code})",
                        f"u:discx:{plan.plan_id}",
                    )
                )
            else:
                kb.add_row(
                    (
                        "🏷 ثبت کد تخفیف" if lang == "fa" else "🏷 Apply Discount Code",
                        f"u:disc:{plan.plan_id}",
                    )
                )
            kb.add_row(
                (
                    "💳 ارسال رسید پرداخت / خرید"
                    if lang == "fa"
                    else "💳 Submit Receipt / Buy",
                    f"u:req:{plan.plan_id}",
                )
            )
            quote_summary = (
                f"💵 قیمت پایه: <code>{format_money(quote.base_price, cur, lang=lang)}</code>\n"
                f"🔄 اعتبار ارتقاء (Proration): <code>{format_money(quote.proration_credit, cur, lang=lang)}</code>\n"
                f"🏷 تخفیف: <code>{format_money(quote.discount_amount, cur, lang=lang)}</code>\n"
                f"👛 کسر از کیف پول: <code>{format_money(quote.wallet_applied, cur, lang=lang)}</code>\n"
                f"✅ <b>مبلغ قابل پرداخت نهایی:</b> <code>{format_money(quote.payable_amount, cur, lang=lang)}</code>"
            )
        except DowngradeNotAllowedError:
            kb.add_row(
                (
                    "🔒 غیرقابل انتخاب (تنزل پلن مجاز نیست)"
                    if lang == "fa"
                    else "🔒 Locked (Downgrade Not Allowed)",
                    f"u:locked:{plan.plan_id}",
                )
            )
            quote_summary = (
                "⚠️ شما هم‌اکنون اشتراک سطح بالاتری دارید و تنزل پلن در زمان فعال بودن اشتراک مجاز نیست."
                if lang == "fa"
                else "⚠️ Paid downgrade is not allowed while a higher-tier plan is active."
            )
        except NegotiatedPlanError:
            kb.add_row(("💬 تماس با پشتیبانی", "u:sup"))
            quote_summary = (
                "💬 تعرفه این پلن توافقی است. لطفاً با پشتیبانی در ارتباط باشید."
                if lang == "fa"
                else "💬 Custom negotiated tariff — please contact support."
            )

        kb.add_nav_footer(back_cb="u:plans", lang=lang)
        txt = (
            f"{crumb}\n\n"
            f"💎 <b>{plan.name_fa if lang == 'fa' else plan.name_en}</b>\n"
            f"⏳ مدت: <code>{plan.duration_days}</code> روز | 📢 کانال: <code>{plan.max_channels}</code> | 🌐 منبع: <code>{plan.max_sources}</code>\n"
            f"📤 سقف پست روزانه: <code>{plan.daily_post_limit}</code> | 🧪 تست روزانه: <code>{plan.daily_test_limit}</code>\n\n"
            f"{quote_summary}"
        )
        return UiResponse(action_id=action_id, text=txt, reply_markup=kb.build())

    # ---- Channel Admin Screens ----------------------------------------------------------------

    async def _render_admin_home(
        self, conn: AsyncConnection, actor: Actor, lang: Lang, action_id: str
    ) -> UiResponse:
        user = await get_user(conn, actor.user_id, self.bootstrap_superadmins)
        usage = await get_daily_usage(conn, actor.user_id)
        channels = await list_channels(conn, actor=actor)
        crumb = render_breadcrumb(
            ["🏠 خانه", "🎛 پنل مدیریت کانال‌ها"]
            if lang == "fa"
            else ["🏠 Home", "🎛 Channel Admin Home"],
            lang=lang,
        )

        max_ch = 0
        post_lim = 0
        test_lim = 0
        plan_name = "بدون پلن فعال" if lang == "fa" else "No Active Plan"
        if user.plan_id is not None:
            p = await get_plan(conn, user.plan_id)
            max_ch = p.max_channels
            post_lim = p.daily_post_limit
            test_lim = p.daily_test_limit
            plan_name = p.name_fa if lang == "fa" else p.name_en

        gauges = (
            f"📦 <b>پلن:</b> <code>{plan_name}</code>\n"
            f"📢 <b>کانال‌ها:</b> {render_quota_gauge(len(channels), max_ch)}\n"
            f"📤 <b>پست‌های امروز:</b> {render_quota_gauge(usage.posts_count, post_lim)}\n"
            f"🧪 <b>تست‌های امروز:</b> {render_quota_gauge(usage.tests_count, test_lim)}"
        )

        kb = KeyboardGrid()
        for ch in channels:
            badge = "🟢" if ch.enabled else "🔴"
            kb.add_row((f"{badge} {ch.title} ({ch.channel_id})", f"a:ch:{ch.channel_id}"))
        kb.add_row(
            ("➕ افزودن کانال جدید" if lang == "fa" else "➕ Add Channel", "a:chadd"),
            ("💳 پلن من و ارتقاء" if lang == "fa" else "💳 My Plan", "a:plan"),
        )
        if actor.is_superadmin:
            kb.add_row(("🛡️ پنل سوپرادمین" if lang == "fa" else "🛡️ Superadmin Panel", "s:report"))
        kb.add_nav_footer(back_cb="u:home", lang=lang)
        return UiResponse(
            action_id=action_id, text=f"{crumb}\n\n{gauges}", reply_markup=kb.build()
        )

    async def _render_channel_dashboard(
        self,
        conn: AsyncConnection,
        actor: Actor,
        lang: Lang,
        channel_id: int,
        action_id: str,
    ) -> UiResponse:
        ch = await get_channel(conn, actor=actor, channel_id=channel_id)
        sources = await list_sources(conn, actor=actor, channel_id=channel_id)
        crumb = render_breadcrumb(
            ["🏠 خانه", "🎛 کانال‌ها", f"📢 {ch.title}"]
            if lang == "fa"
            else ["🏠 Home", "🎛 Channels", f"📢 {ch.title}"],
            lang=lang,
        )
        status_str = render_status_badge(ch.enabled, lang=lang)
        txt = (
            f"{crumb}\n\n"
            f"📢 <b>کانال:</b> <code>{ch.title}</code> (<code>{ch.channel_id}</code>)\n"
            f"⚡ <b>وضعیت اتوماسیون:</b> {status_str}\n"
            f"🌐 <b>تعداد منابع متصل:</b> <code>{len(sources)}</code>\n"
            f"⏱ <b>فاصله انتشار:</b> هر <code>{ch.settings.publish_interval_minutes}</code> دقیقه | "
            f"🎯 <b>حداقل امتیاز:</b> <code>{ch.settings.min_score_threshold}</code>"
        )
        cid = ch.channel_id
        kb = KeyboardGrid()
        if lang == "fa":
            kb.add_row(
                ("⚡ تغییر وضعیت اتوماسیون", f"a:tog:{cid}:enabled"),
                ("🧪 تست دستی چرخه", f"a:test:{cid}"),
            )
            kb.add_row(
                ("🌐 مدیریت منابع محتوا", f"a:src:{cid}"),
                ("✍️ تنظیمات محتوا و تحریریه", f"a:con:{cid}"),
            )
            kb.add_row(
                ("⏰ زمان‌بندی و ساعات خاموشی", f"a:sch:{cid}"),
                ("📥 صف انتشار و مقالات", f"a:que:{cid}"),
            )
            kb.add_row(
                ("🚫 مقالات ردشده", f"a:rej:{cid}"),
                ("📊 گزارش عملکرد کانال", f"a:rep:{cid}"),
            )
            kb.add_row(
                ("🤖 مدل‌های هوش مصنوعی شخصی", f"a:myai:{cid}"),
                ("🗑 حذف کانال", f"a:chdel:{cid}"),
            )
        else:
            kb.add_row(
                ("⚡ Toggle Automation", f"a:tog:{cid}:enabled"),
                ("🧪 Manual Test Cycle", f"a:test:{cid}"),
            )
            kb.add_row(
                ("🌐 Manage Sources", f"a:src:{cid}"),
                ("✍️ Editorial Settings", f"a:con:{cid}"),
            )
            kb.add_row(
                ("⏰ Schedule & Quiet Hours", f"a:sch:{cid}"),
                ("📥 Ready Queue", f"a:que:{cid}"),
            )
            kb.add_row(
                ("🚫 Rejected Items", f"a:rej:{cid}"),
                ("📊 Activity Report", f"a:rep:{cid}"),
            )
            kb.add_row(
                ("🤖 Personal AI Models", f"a:myai:{cid}"),
                ("🗑 Delete Channel", f"a:chdel:{cid}"),
            )
        kb.add_nav_footer(back_cb="a:home", lang=lang)
        return UiResponse(action_id=action_id, text=txt, reply_markup=kb.build())

    async def _render_source_detail(
        self,
        conn: AsyncConnection,
        actor: Actor,
        lang: Lang,
        source_id: int,
        action_id: str,
    ) -> UiResponse:
        src = await get_source(conn, actor=actor, source_id=source_id)
        crumb = render_breadcrumb(
            ["🏠 خانه", "🎛 کانال", f"🔍 منبع #{src.source_id}"], lang=lang
        )
        fetch_badge = render_status_badge(src.fetch_enabled, lang=lang)
        pub_badge = render_status_badge(src.publish_enabled, lang=lang)
        api_badge = "🔑 فعال (Encrypted)" if src.has_custom_api_key else "—"
        txt = (
            f"{crumb}\n\n"
            f"🔗 <b>URL:</b> <code>{src.canonical_url}</code>\n"
            f"🧭 <b>استراتژی کشف:</b> <code>{src.discovery_type}</code>\n"
            f"📡 <b>دریافت (Fetch):</b> {fetch_badge} | 📤 <b>انتشار:</b> {pub_badge}\n"
            f"🔐 <b>API اختصاصی:</b> {api_badge} | ⚠️ <b>خطاها:</b> <code>{src.failure_count}</code>"
        )
        sid = src.source_id
        kb = (
            KeyboardGrid()
            .add_row(
                ("🔄 بررسی مجدد و عیب‌یابی", f"a:srcr:{sid}"),
                ("📡 تغییر وضعیت دریافت", f"a:srct:{sid}"),
            )
            .add_row(
                ("📤 تغییر وضعیت انتشار", f"a:srctb:{sid}"),
                ("🔑 تنظیم API اختصاصی", f"a:srcapi:{sid}"),
            )
            .add_row(
                ("🧹 پاک کردن API اختصاصی", f"a:srcapix:{sid}"),
                ("🗑 حذف منبع", f"a:srcd:{sid}"),
            )
            .add_nav_footer(back_cb=f"a:src:{src.channel_id}", lang=lang)
        )
        return UiResponse(action_id=action_id, text=txt, reply_markup=kb.build())


# ---- Route Handlers for all 38 Action IDs ----------------------------------------------------


async def _h_cmd_start(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    _m: re.Match[str],
) -> UiResponse:
    parts = raw.split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("dl_"):
        dl_key = parts[1].strip()
        try:
            cont = await resolve_deeplink_continuation(conn, key=dl_key)
            crumb = render_breadcrumb(["📖 مطالعه کامل مقاله علمی"], lang=lang)
            first = cont.html_chunks[0] if cont.html_chunks else cont.title
            kb = KeyboardGrid().add_nav_footer(lang=lang)
            return UiResponse(
                action_id="CMD_START",
                text=f"{crumb}\n\n{first}",
                reply_markup=kb.build(),
                rich_blocks=cont.rich_blocks,
                extra_chunks=cont.html_chunks[1:],
            )
        except NotFound:
            pass
    user = await get_user(conn, actor.user_id, router.bootstrap_superadmins)
    if user.lang is None:
        return await router._render_language_picker(conn, actor, lang, "CMD_START")
    return await router._render_user_home(conn, actor, lang, "CMD_START")


async def _h_cmd_lang(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    return await router._render_language_picker(conn, actor, lang, "CMD_LANG")


async def _h_cmd_help(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    _actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    help_txt = await get_system_text(conn, key="help", lang=lang)
    crumb = render_breadcrumb(["🏠 خانه", "📚 راهنما"], lang=lang)
    kb = KeyboardGrid().add_nav_footer(lang=lang)
    return UiResponse(action_id="CMD_HELP", text=f"{crumb}\n\n{help_txt}", reply_markup=kb.build())


async def _h_cmd_about(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    _actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    about_txt = await get_system_text(conn, key="about", lang=lang)
    crumb = render_breadcrumb(["🏠 خانه", "🤖 درباره ربات"], lang=lang)
    kb = KeyboardGrid().add_nav_footer(lang=lang)
    return UiResponse(
        action_id="CMD_ABOUT", text=f"{crumb}\n\n{about_txt}", reply_markup=kb.build()
    )


async def _h_referral_dashboard(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
    action_id: str,
) -> UiResponse:
    dash = await get_referral_dashboard(conn, actor=actor)
    crumb = render_breadcrumb(["🏠 خانه", "🎁 دعوت از دوستان"], lang=lang)
    link = f"https://t.me/{router.bot_username}?start={dash.deep_link_param}"
    txt = (
        f"{crumb}\n\n"
        f"🔗 <b>لینک اختصاصی دعوت شما:</b>\n<code>{link}</code>\n\n"
        f"👥 <b>کاربران دعوت‌شده:</b> <code>{dash.invited_count}</code> نفر\n"
        f"💳 <b>پرداخت‌های پاداش‌گرفته:</b> <code>{dash.rewarded_payments_count}</code>\n"
        f"📊 <b>درصد پاداش:</b> <code>{dash.economics.percent}%</code>\n"
        f"💰 <b>مجموع پاداش تومانی:</b> <code>{dash.earned_tmn:,} تومان</code>\n"
        f"💵 <b>مجموع پاداش دلاری:</b> <code>${dash.earned_usd / 100.0:,.2f} USD</code>"
    )
    kb = KeyboardGrid().add_nav_footer(back_cb="u:home", lang=lang)
    return UiResponse(action_id=action_id, text=txt, reply_markup=kb.build())


async def _h_cmd_ref(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    return await _h_referral_dashboard(router, conn, actor, lang, raw, m, "CMD_REF")


async def _h_cmd_man(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    return await router._render_admin_home(conn, actor, lang, "CMD_MAN")


async def _h_cmd_cancel(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    await clear_wizard_session(conn, user_id=actor.user_id)
    return await router._render_user_home(conn, actor, lang, "CMD_CANCEL")


async def _h_cmd_create(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    await set_wizard_session(
        conn,
        user_id=actor.user_id,
        action_id="CMD_CREATE",
        step="register_channel",
        parent_callback="a:home",
    )
    crumb = render_breadcrumb(["🏠 خانه", "➕ ثبت کانال جدید"], lang=lang)
    txt = (
        f"{crumb}\n\n"
        "لطفاً آیدی عددی کانال (مانند <code>-1001234567890</code>)، یوزرنیم کانال (<code>@Channel</code>) "
        "یا توکن انتقال مالکیت (<code>ogch_...</code>) را ارسال کنید:"
    )
    kb = KeyboardGrid().add_cancel_button(lang=lang)
    return UiResponse(action_id="CMD_CREATE", text=txt, reply_markup=kb.build())


async def _h_cb_user_home(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    return await router._render_user_home(conn, actor, lang, "CB_USER_HOME")


async def _h_cb_user_plans(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    return await router._render_plans_catalog(conn, actor, lang, "CB_USER_PLANS")


async def _h_cb_user_plan_detail(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    return await router._render_plan_detail(
        conn, actor, lang, int(m.group(1)), "CB_USER_PLAN_DETAIL"
    )


async def _h_cb_user_free_claim(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    pid = int(m.group(1))
    await claim_free_plan(
        conn, actor=actor, idempotency_key=f"ui_free:{actor.user_id}:{pid}"
    )
    return await router._render_admin_home(conn, actor, lang, "CB_USER_FREE_CLAIM")


async def _h_cb_user_free_used(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    res = await router._render_plan_detail(
        conn, actor, lang, int(m.group(1)), "CB_USER_FREE_USED"
    )
    return UiResponse(
        action_id="CB_USER_FREE_USED",
        text=res.text,
        reply_markup=res.reply_markup,
        alert_text="پلن رایگان یک‌بارمصرف قبلاً برای حساب شما فعال شده است."
        if lang == "fa"
        else "One-time free plan has already been claimed.",
        show_alert=True,
    )


async def _h_cb_user_plan_locked(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    res = await router._render_plan_detail(
        conn, actor, lang, int(m.group(1)), "CB_USER_PLAN_LOCKED"
    )
    return UiResponse(
        action_id="CB_USER_PLAN_LOCKED",
        text=res.text,
        reply_markup=res.reply_markup,
        alert_text="تنزل پلن در زمان فعال بودن اشتراک بالاتر مجاز نیست."
        if lang == "fa"
        else "Paid downgrade is not allowed while a higher plan is active.",
        show_alert=True,
    )


async def _h_cb_user_payment_pending(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    pid = int(m.group(1))
    row = (
        await conn.execute(
            text(
                "SELECT payment_id, payable_amount, currency, status FROM pay_requests "
                "WHERE user_id = :u AND status IN ('pending', 'processing') LIMIT 1"
            ),
            {"u": actor.user_id},
        )
    ).first()
    info = (
        f"درخواست پرداخت #{row._mapping['payment_id']} در انتظار بررسی مدیریت است."
        if row is not None
        else "درخواست باز یافت نشد."
    )
    kb = KeyboardGrid().add_nav_footer(back_cb=f"u:plan:{pid}", lang=lang)
    return UiResponse(
        action_id="CB_USER_PAYMENT_PENDING",
        text=info,
        reply_markup=kb.build(),
        alert_text=info,
        show_alert=True,
    )


async def _h_cb_user_discount_apply(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    pid = int(m.group(1))
    await set_wizard_session(
        conn,
        user_id=actor.user_id,
        action_id="CB_USER_DISCOUNT_APPLY",
        step="apply_discount",
        parent_callback=f"u:plan:{pid}",
        context={"plan_id": pid},
    )
    crumb = render_breadcrumb(["🏠 خانه", "💎 پلن", "🏷 ثبت کد تخفیف"], lang=lang)
    kb = KeyboardGrid().add_cancel_button(lang=lang)
    return UiResponse(
        action_id="CB_USER_DISCOUNT_APPLY",
        text=f"{crumb}\n\nلطفاً کد تخفیف خود را ارسال کنید:",
        reply_markup=kb.build(),
    )


async def _h_cb_user_discount_remove(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    pid = int(m.group(1))
    await clear_quote_discount(conn, user_id=actor.user_id, plan_id=pid)
    return await router._render_plan_detail(
        conn, actor, lang, pid, "CB_USER_DISCOUNT_REMOVE"
    )


async def _h_cb_user_request_payment(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    pid = int(m.group(1))
    disc_code = await get_quote_discount(conn, user_id=actor.user_id, plan_id=pid)
    quote = await quote_plan_for_user(
        conn, actor=actor, plan_id=pid, discount_code=disc_code
    )
    await set_wizard_session(
        conn,
        user_id=actor.user_id,
        action_id="CB_USER_REQUEST_PAYMENT",
        step="submit_receipt",
        parent_callback=f"u:plan:{pid}",
        context={"plan_id": pid},
    )
    crumb = render_breadcrumb(["🏠 خانه", "💎 خرید پلن", "💳 ارسال رسید"], lang=lang)
    kb = KeyboardGrid().add_cancel_button(lang=lang)
    txt = (
        f"{crumb}\n\n"
        f"💳 <b>مبلغ نهایی قابل پرداخت:</b> <code>{format_money(quote.payable_amount, quote.currency, lang=lang)}</code>\n"
        "لطفاً تصویر فیش واریزی، فایل رسید یا کد پیگیری تراکنش را ارسال نمایید:"
    )
    return UiResponse(action_id="CB_USER_REQUEST_PAYMENT", text=txt, reply_markup=kb.build())


async def _h_cb_user_referral(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    return await _h_referral_dashboard(router, conn, actor, lang, raw, m, "CB_USER_REFERRAL")


async def _h_cb_user_support(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    _m: re.Match[str],
) -> UiResponse:
    if raw == "u:sclose":
        await close_support_ticket(conn, actor=actor)
        await clear_wizard_session(conn, user_id=actor.user_id)
        return await router._render_user_home(conn, actor, lang, "CB_USER_SUPPORT")

    await set_wizard_session(
        conn,
        user_id=actor.user_id,
        action_id="CB_USER_SUPPORT",
        step="support_chat",
        parent_callback="u:home",
    )
    crumb = render_breadcrumb(["🏠 خانه", "💬 پشتیبانی آنلاین"], lang=lang)
    kb = (
        KeyboardGrid()
        .add_row(("🔒 بستن گفتگو" if lang == "fa" else "🔒 Close Ticket", "u:sclose"))
        .add_cancel_button(lang=lang)
    )
    txt = f"{crumb}\n\n💬 پیام، تصویر یا مستندات خود را ارسال کنید تا مستقیماً به تیم پشتیبانی تحویل شود:"
    return UiResponse(action_id="CB_USER_SUPPORT", text=txt, reply_markup=kb.build())


async def _h_cb_user_manage_panel(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    return await router._render_admin_home(conn, actor, lang, "CB_USER_MANAGE_PANEL")


async def _h_cb_user_help(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    _actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    help_txt = await get_system_text(conn, key="help", lang=lang)
    crumb = render_breadcrumb(["🏠 خانه", "📚 راهنما"], lang=lang)
    kb = KeyboardGrid().add_nav_footer(back_cb="u:home", lang=lang)
    return UiResponse(
        action_id="CB_USER_HELP", text=f"{crumb}\n\n{help_txt}", reply_markup=kb.build()
    )


async def _h_cb_user_lang(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    _m: re.Match[str],
) -> UiResponse:
    if raw in ("u:lang:fa", "u:lang:en"):
        new_lang: Lang = "fa" if raw.endswith(":fa") else "en"
        await set_user_language(conn, actor=actor, lang=new_lang)
        return await router._render_user_home(conn, actor, new_lang, "CB_USER_LANG")
    return await router._render_language_picker(conn, actor, lang, "CB_USER_LANG")


async def _h_cb_mandatory_membership(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    async def _default_member(_chat: int | str, _uid: int) -> dict[str, Any]:
        return {"status": "member"}

    checker = router.get_chat_member or _default_member
    gate = await check_mandatory_membership(
        conn, actor=actor, lang=lang, get_chat_member=checker
    )
    if not gate.allowed:
        kb = KeyboardGrid().add_row(
            ("✅ عضو شدم / بررسی مجدد", "a:mand"),
        )
        return UiResponse(
            action_id="CB_MANDATORY_MEMBERSHIP_CHECK",
            text="🔐 برای ادامه لطفاً ابتدا در کانال رسمی عضو شوید.",
            reply_markup=kb.build(),
            alert_text="هنوز عضویت شما در کانال تایید نشده است.",
            show_alert=True,
        )
    return await router._render_user_home(
        conn, actor, lang, "CB_MANDATORY_MEMBERSHIP_CHECK"
    )


async def _h_cb_shared_cancel(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    prev = await clear_wizard_session(conn, user_id=actor.user_id)
    parent = prev.parent_callback if prev else "u:home"
    kb = KeyboardGrid().add_nav_footer(back_cb=parent, lang=lang)
    crumb = render_breadcrumb(["🏠 خانه", "🔙 لغو عملیات"], lang=lang)
    return UiResponse(
        action_id="CB_SHARED_CANCEL",
        text=f"{crumb}\n\n✅ عملیات جاری لغو شد.",
        reply_markup=kb.build(),
    )


async def _h_cb_admin_home(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    return await router._render_admin_home(conn, actor, lang, "CB_ADMIN_HOME")


async def _h_cb_admin_my_plan(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    return await router._render_plans_catalog(conn, actor, lang, "CB_ADMIN_MY_PLAN")


async def _h_cb_admin_channel_add(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    await set_wizard_session(
        conn,
        user_id=actor.user_id,
        action_id="CB_ADMIN_CHANNEL_ADD",
        step="register_channel",
        parent_callback="a:home",
    )
    crumb = render_breadcrumb(["🏠 خانه", "🎛 کانال‌ها", "➕ افزودن کانال"], lang=lang)
    kb = KeyboardGrid().add_cancel_button(lang=lang)
    return UiResponse(
        action_id="CB_ADMIN_CHANNEL_ADD",
        text=f"{crumb}\n\nیک پست از کانال فوروارد کنید یا آیدی/یوزرنیم/توکن انتقال را بفرستید:",
        reply_markup=kb.build(),
    )


async def _h_cb_admin_channel_home(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    return await router._render_channel_dashboard(
        conn, actor, lang, int(m.group(1)), "CB_ADMIN_CHANNEL_HOME"
    )


async def _h_cb_admin_channel_test(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    cid = int(m.group(1))
    ch = await get_channel(conn, actor=actor, channel_id=cid)
    used = await (
        lambda: conn.execute(
            text(
                "INSERT INTO usage(user_id, usage_date, tests_count) VALUES (:u, CURRENT_DATE, 1) "
                "ON CONFLICT (user_id, usage_date) DO UPDATE SET tests_count = usage.tests_count + 1 "
                "RETURNING tests_count"
            ),
            {"u": actor.user_id},
        )
    )()
    cnt = int(used.one()._mapping["tests_count"])
    crumb = render_breadcrumb(["🏠 خانه", f"📢 {ch.title}", "🧪 تست دستی"], lang=lang)
    kb = KeyboardGrid().add_nav_footer(back_cb=f"a:ch:{cid}", lang=lang)
    return UiResponse(
        action_id="CB_ADMIN_CHANNEL_TEST",
        text=f"{crumb}\n\n✅ چرخه تست دستی کانال با موفقیت اجرا شد (تست شماره <code>{cnt}</code> امروز).",
        reply_markup=kb.build(),
    )


async def _h_cb_admin_channel_report(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    cid = int(m.group(1))
    ch = await get_channel(conn, actor=actor, channel_id=cid)
    posted_cnt = int(
        (
            await conn.execute(
                text("SELECT count(*) FROM posted WHERE channel_id = :c"), {"c": cid}
            )
        ).scalar_one()
    )
    ready_cnt = int(
        (
            await conn.execute(
                text("SELECT count(*) FROM articles WHERE channel_id = :c AND status = 'ready'"),
                {"c": cid},
            )
        ).scalar_one()
    )
    crumb = render_breadcrumb(["🏠 خانه", f"📢 {ch.title}", "📊 گزارش عملکرد"], lang=lang)
    kb = KeyboardGrid().add_nav_footer(back_cb=f"a:ch:{cid}", lang=lang)
    txt = (
        f"{crumb}\n\n"
        f"📤 <b>مقالات منتشرشده:</b> <code>{posted_cnt}</code>\n"
        f"📥 <b>مقالات آماده در صف:</b> <code>{ready_cnt}</code>"
    )
    return UiResponse(
        action_id="CB_ADMIN_CHANNEL_REPORT", text=txt, reply_markup=kb.build()
    )


async def _h_cb_admin_channel_toggle(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    cid = int(m.group(1))
    ch = await get_channel(conn, actor=actor, channel_id=cid)
    await set_channel_enabled(conn, actor=actor, channel_id=cid, enabled=not ch.enabled)
    return await router._render_channel_dashboard(
        conn, actor, lang, cid, "CB_ADMIN_CHANNEL_AUTOMATION_TOGGLE"
    )


async def _h_cb_admin_channel_delete(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    cid = int(m.group(1))
    ch = await get_channel(conn, actor=actor, channel_id=cid)
    if raw.endswith(":confirm"):
        await delete_channel(conn, actor=actor, channel_id=cid)
        return await router._render_admin_home(conn, actor, lang, "CB_ADMIN_CHANNEL_DELETE")

    txt, markup = build_confirmation_screen(
        breadcrumbs=["🏠 خانه", f"📢 {ch.title}", "🗑 حذف کانال"],
        warning_title="آیا از حذف کامل این کانال اطمینان دارید؟",
        warning_body="با حذف کانال، منابع و صف مقالات آن پاک می‌شوند (سوابق مالی و ممیزی محفوظ می‌مانند).",
        confirm_cb=f"a:chdel:{cid}:confirm",
        lang=lang,
    )
    return UiResponse(action_id="CB_ADMIN_CHANNEL_DELETE", text=txt, reply_markup=markup)


async def _h_cb_admin_sources_list(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    cid = int(m.group(1))
    page = int(m.group(2)) if m.group(2) else 1
    ch = await get_channel(conn, actor=actor, channel_id=cid)
    sources = await list_sources(conn, actor=actor, channel_id=cid)
    crumb = render_breadcrumb(["🏠 خانه", f"📢 {ch.title}", "🌐 منابع محتوا"], lang=lang)
    kb = KeyboardGrid()
    start = (page - 1) * 15
    for s in sources[start : start + 15]:
        b = "🟢" if s.fetch_enabled else "🔴"
        kb.add_row((f"{b} #{s.source_id} {s.canonical_url[:28]}", f"a:srcv:{s.source_id}"))
    kb.add_pagination(
        base_prefix=f"a:src:{cid}", page=page, total_items=len(sources), lang=lang
    )
    kb.add_row(("➕ افزودن منبع جدید (URL)", f"a:srca:{cid}"))
    kb.add_nav_footer(back_cb=f"a:ch:{cid}", lang=lang)
    return UiResponse(
        action_id="CB_ADMIN_SOURCES_LIST",
        text=f"{crumb}\n\nتعداد کل منابع: <code>{len(sources)}</code>",
        reply_markup=kb.build(),
    )


async def _h_cb_admin_source_add(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    cid = int(m.group(1))
    await get_channel(conn, actor=actor, channel_id=cid)
    await set_wizard_session(
        conn,
        user_id=actor.user_id,
        action_id="CB_ADMIN_SOURCE_ADD",
        step="add_source",
        parent_callback=f"a:src:{cid}",
        context={"channel_id": cid},
    )
    crumb = render_breadcrumb(["🏠 خانه", "🌐 منابع", "➕ افزودن منبع"], lang=lang)
    kb = KeyboardGrid().add_cancel_button(lang=lang)
    return UiResponse(
        action_id="CB_ADMIN_SOURCE_ADD",
        text=f"{crumb}\n\nلطفاً آدرس اینترنتی منبع (RSS، Sitemap، JSON یا صفحه وب) را ارسال کنید:",
        reply_markup=kb.build(),
    )


async def _h_cb_admin_source_inspect(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    return await router._render_source_detail(
        conn, actor, lang, int(m.group(1)), "CB_ADMIN_SOURCE_INSPECT"
    )


async def _h_cb_admin_source_recheck(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    sid = int(m.group(1))
    await get_source(conn, actor=actor, source_id=sid)
    await conn.execute(
        text("UPDATE sources SET failure_count = 0, cooldown_until = NULL WHERE source_id = :s"),
        {"s": sid},
    )
    return await router._render_source_detail(
        conn, actor, lang, sid, "CB_ADMIN_SOURCE_RECHECK"
    )


async def _h_cb_admin_source_toggle_fetch(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    sid = int(m.group(1))
    src = await get_source(conn, actor=actor, source_id=sid)
    await set_source_toggles(
        conn, actor=actor, source_id=sid, fetch_enabled=not src.fetch_enabled
    )
    return await router._render_source_detail(
        conn, actor, lang, sid, "CB_ADMIN_SOURCE_TOGGLE_FETCH"
    )


async def _h_cb_admin_source_toggle_publish(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    sid = int(m.group(1))
    src = await get_source(conn, actor=actor, source_id=sid)
    await set_source_toggles(
        conn, actor=actor, source_id=sid, publish_enabled=not src.publish_enabled
    )
    return await router._render_source_detail(
        conn, actor, lang, sid, "CB_ADMIN_SOURCE_TOGGLE_PUBLISH"
    )


async def _h_cb_admin_source_custom_api(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    sid = int(m.group(1))
    src = await get_source(conn, actor=actor, source_id=sid)
    await configure_source_discovery(
        conn,
        actor=actor,
        source_id=sid,
        discovery_type="custom_api",
        custom_api_url=src.canonical_url,
    )
    return await router._render_source_detail(
        conn, actor, lang, sid, "CB_ADMIN_SOURCE_CUSTOM_API"
    )


async def _h_cb_admin_source_custom_api_clear(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    sid = int(m.group(1))
    await configure_source_discovery(
        conn,
        actor=actor,
        source_id=sid,
        discovery_type="auto",
        custom_api_key="",
    )
    return await router._render_source_detail(
        conn, actor, lang, sid, "CB_ADMIN_SOURCE_CUSTOM_API_CLEAR"
    )


async def _h_cb_admin_source_delete(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    sid = int(m.group(1))
    src = await get_source(conn, actor=actor, source_id=sid)
    if raw.endswith(":confirm"):
        await delete_source(conn, actor=actor, source_id=sid)
        kb = KeyboardGrid().add_nav_footer(back_cb=f"a:src:{src.channel_id}", lang=lang)
        return UiResponse(
            action_id="CB_ADMIN_SOURCE_DELETE",
            text="✅ منبع با موفقیت حذف شد.",
            reply_markup=kb.build(),
        )
    txt, markup = build_confirmation_screen(
        breadcrumbs=["🏠 خانه", "🌐 منابع", f"🗑 حذف منبع #{sid}"],
        warning_title="آیا از حذف این منبع اطمینان دارید؟",
        warning_body=f"منبع <code>{src.canonical_url}</code> حذف خواهد شد.",
        confirm_cb=f"a:srcd:{sid}:confirm",
        lang=lang,
    )
    return UiResponse(action_id="CB_ADMIN_SOURCE_DELETE", text=txt, reply_markup=markup)


async def _h_cb_admin_content_menu(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    cid = int(m.group(1))
    ch = await get_channel(conn, actor=actor, channel_id=cid)
    s = ch.settings
    crumb = render_breadcrumb(["🏠 خانه", f"📢 {ch.title}", "✍️ تنظیمات تحریریه"], lang=lang)
    txt = (
        f"{crumb}\n\n"
        f"🌐 <b>زبان خروجی:</b> <code>{s.language}</code> | ✍️ <b>سبک:</b> <code>{s.writing_style}</code>\n"
        f"🎯 <b>حداقل امتیاز:</b> <code>{s.min_score_threshold}</code> | 📏 <b>طول پست:</b> <code>{s.post_length}</code>\n"
        f"🔬 <b>قالب‌بندی علمی Rich Message:</b> {render_status_badge(s.rich_message_formatting_enabled, lang=lang)}\n"
        f"#️⃣ <b>هشتگ خودکار:</b> {render_status_badge(s.hashtags_enabled, lang=lang)}"
    )
    kb = (
        KeyboardGrid()
        .add_row(
            ("🌐 تغییر زبان خروجی", f"a:set:{cid}:lang"),
            ("✍️ تغییر سبک نگارش", f"a:set:{cid}:prompt"),
        )
        .add_row(
            ("#️⃣ هشتگ خودکار", f"a:set:{cid}:hashtags"),
            ("🔗 لینک منبع", f"a:set:{cid}:src_link"),
        )
        .add_row(
            ("🗂 دسته‌بندی‌ها", f"a:cat:{cid}"),
            ("⚖️ معیارهای ارزیابی", f"a:cri:{cid}"),
        )
        .add_nav_footer(back_cb=f"a:ch:{cid}", lang=lang)
    )
    return UiResponse(action_id="CB_ADMIN_CONTENT_MENU", text=txt, reply_markup=kb.build())


async def _h_cb_admin_content_set_field(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    cid = int(m.group(1))
    field_name = m.group(2)
    ch = await get_channel(conn, actor=actor, channel_id=cid)
    d = ch.settings.to_dict()
    if field_name == "hashtags":
        d["hashtags_enabled"] = not ch.settings.hashtags_enabled
    elif field_name == "src_link":
        d["source_link_credit_enabled"] = not ch.settings.source_link_credit_enabled
    elif field_name == "strict_ad":
        d["ad_detection_enabled"] = not ch.settings.ad_detection_enabled
    elif field_name == "sci_format":
        d["rich_message_formatting_enabled"] = not ch.settings.rich_message_formatting_enabled
    elif field_name == "lang":
        d["language"] = "en" if ch.settings.language == "fa" else "fa"
    await update_channel_settings(
        conn, actor=actor, channel_id=cid, settings=ChannelSettings.from_dict(d)
    )
    fake_m = re.match(r"^a:con:(-?\d+)$", f"a:con:{cid}")
    assert fake_m is not None
    res = await _h_cb_admin_content_menu(router, conn, actor, lang, f"a:con:{cid}", fake_m)
    return UiResponse(
        action_id="CB_ADMIN_CONTENT_SET_FIELD", text=res.text, reply_markup=res.reply_markup
    )


async def _h_cb_admin_categories(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    cid = int(m.group(1))
    ch = await get_channel(conn, actor=actor, channel_id=cid)
    cats = ch.settings.custom_categories
    crumb = render_breadcrumb(["🏠 خانه", f"📢 {ch.title}", "🗂 دسته‌بندی‌ها"], lang=lang)
    kb = KeyboardGrid()
    for idx, c in enumerate(cats[:15]):
        kb.add_row((f"🗂 {c}", f"a:catv:{cid}:{idx}"), ("🗑", f"a:catd:{cid}:{idx}"))
    kb.add_row(("➕ افزودن دسته‌بندی", f"a:cata:{cid}"))
    kb.add_nav_footer(back_cb=f"a:con:{cid}", lang=lang)
    return UiResponse(
        action_id="CB_ADMIN_CATEGORIES",
        text=f"{crumb}\n\nدسته‌بندی‌های سفارشی کانال ({len(cats)} مورد):",
        reply_markup=kb.build(),
    )


async def _h_cb_admin_criteria(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    m: re.Match[str],
) -> UiResponse:
    cid = int(m.group(1))
    ch = await get_channel(conn, actor=actor, channel_id=cid)
    weights = ch.settings.scoring_weights.normalized()
    crumb = render_breadcrumb(["🏠 خانه", f"📢 {ch.title}", "⚖️ معیارهای ارزیابی"], lang=lang)
    lines = [f"• <b>{k}:</b> <code>{v * 100:.1f}%</code>" for k, v in weights.items()]
    kb = KeyboardGrid().add_nav_footer(back_cb=f"a:con:{cid}", lang=lang)
    return UiResponse(
        action_id="CB_ADMIN_CRITERIA",
        text=f"{crumb}\n\n" + "\n".join(lines),
        reply_markup=kb.build(),
    )


async def _h_cb_admin_scheduling(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    cid = int(m.group(1))
    ch = await get_channel(conn, actor=actor, channel_id=cid)
    d = ch.settings.to_dict()
    if raw.startswith(f"a:qoff:{cid}"):
        d["active_hours"] = {
            "quiet_hours_enabled": False,
            "start_hour": ch.settings.active_hours.start_hour,
            "end_hour": ch.settings.active_hours.end_hour,
            "timezone": ch.settings.active_hours.timezone,
        }
        ch = await update_channel_settings(
            conn, actor=actor, channel_id=cid, settings=ChannelSettings.from_dict(d)
        )
    elif raw.startswith(f"a:tzs:{cid}:"):
        tz_val = raw.split(":", 3)[3]
        d["active_hours"] = {
            "quiet_hours_enabled": ch.settings.active_hours.quiet_hours_enabled,
            "start_hour": ch.settings.active_hours.start_hour,
            "end_hour": ch.settings.active_hours.end_hour,
            "timezone": tz_val,
        }
        ch = await update_channel_settings(
            conn, actor=actor, channel_id=cid, settings=ChannelSettings.from_dict(d)
        )
    crumb = render_breadcrumb(["🏠 خانه", f"📢 {ch.title}", "⏰ زمان‌بندی"], lang=lang)
    ah = ch.settings.active_hours
    txt = (
        f"{crumb}\n\n"
        f"⏱ <b>فاصله انتشار:</b> هر <code>{ch.settings.publish_interval_minutes}</code> دقیقه\n"
        f"📤 <b>تعداد پست در هر سیکل:</b> <code>{ch.settings.posts_per_cycle}</code>\n"
        f"🌍 <b>منطقه زمانی:</b> <code>{ah.timezone}</code>\n"
        f"🌙 <b>پنجره ساعات فعال:</b> <code>{ah.start_hour:02d}:00 .. {ah.end_hour:02d}:00</code> "
        f"({render_status_badge(ah.quiet_hours_enabled, lang=lang)})"
    )
    kb = (
        KeyboardGrid()
        .add_row(
            ("🌙 تنظیم ساعات خاموشی", f"a:quiet:{cid}"),
            ("🌍 منطقه زمانی تهران", f"a:tzs:{cid}:Asia/Tehran"),
        )
        .add_row(
            ("🔓 غیرفعال کردن ساعات خاموشی", f"a:qoff:{cid}"),
            ("🔄 تغییر حالت انتشار", f"a:mode:{cid}"),
        )
        .add_nav_footer(back_cb=f"a:ch:{cid}", lang=lang)
    )
    return UiResponse(action_id="CB_ADMIN_SCHEDULING", text=txt, reply_markup=kb.build())


async def _h_cb_admin_queue_and_articles(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    if raw.startswith("a:qreset:"):
        cid = int(m.group(1))
        cleared = await clear_article_queue(conn, actor=actor, channel_id=cid)
        kb = KeyboardGrid().add_nav_footer(back_cb=f"a:ch:{cid}", lang=lang)
        return UiResponse(
            action_id="CB_ADMIN_QUEUE_AND_ARTICLES",
            text=f"✅ صف انتشار پاک‌سازی شد (<code>{cleared}</code> مقاله حذف شد).",
            reply_markup=kb.build(),
        )
    if raw.startswith("a:pub:") and router.bot_api is not None:
        aid = int(m.group(1))
        pub_res = await publish_ready_article(
            router.db,
            article_id=aid,
            bot_api=router.bot_api,
            bot_username=router.bot_username,
            bootstrap_superadmins=list(router.bootstrap_superadmins),
        )
        return UiResponse(
            action_id="CB_ADMIN_QUEUE_AND_ARTICLES",
            text=f"✅ مقاله #{aid} با وضعیت <code>{pub_res.phase}</code> منتشر شد.",
        )

    target_id = int(m.group(1))
    crumb = render_breadcrumb(["🏠 خانه", "📥 مدیریت صف و مقالات"], lang=lang)
    kb = KeyboardGrid().add_nav_footer(back_cb="a:home", lang=lang)
    return UiResponse(
        action_id="CB_ADMIN_QUEUE_AND_ARTICLES",
        text=f"{crumb}\n\nلیست و جزئیات صف انتشار (شناسه <code>{target_id}</code>):",
        reply_markup=kb.build(),
    )


async def _h_cb_admin_personal_ai(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    if raw.startswith("a:myai_d:"):
        mid = int(m.group(1))
        await delete_ai_model(conn, actor=actor, model_id=mid)
        return UiResponse(
            action_id="CB_ADMIN_PERSONAL_AI", text=f"✅ مدل شخصی #{mid} حذف شد."
        )
    models = await list_ai_models(conn, actor=actor, include_global=False)
    crumb = render_breadcrumb(["🏠 خانه", "🤖 مدل‌های هوش مصنوعی شخصی"], lang=lang)
    kb = KeyboardGrid()
    for mod in models:
        kb.add_row((f"🤖 {mod.name} ({mod.provider_type})", f"a:myai_v:{mod.model_id}"))
    kb.add_nav_footer(back_cb="a:home", lang=lang)
    return UiResponse(
        action_id="CB_ADMIN_PERSONAL_AI",
        text=f"{crumb}\n\nمدل‌های اختصاصی ثبت‌شده شما: <code>{len(models)}</code>",
        reply_markup=kb.build(),
    )


# ---- Superadmin Console Handlers (`s:*`) -----------------------------------------------------


async def _h_cb_super_report(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    require_superadmin(actor)
    u_cnt = int((await conn.execute(text("SELECT count(*) FROM users"))).scalar_one())
    ch_cnt = int((await conn.execute(text("SELECT count(*) FROM channels"))).scalar_one())
    post_cnt = int((await conn.execute(text("SELECT count(*) FROM posted"))).scalar_one())
    crumb = render_breadcrumb(["🛡️ کنسول سوپرادمین", "📈 گزارش جامع پلتفرم"], lang=lang)
    txt = (
        f"{crumb}\n\n"
        f"👥 <b>کل کاربران:</b> <code>{u_cnt}</code>\n"
        f"📢 <b>کل کانال‌ها:</b> <code>{ch_cnt}</code>\n"
        f"📤 <b>کل پست‌های منتشرشده:</b> <code>{post_cnt}</code>"
    )
    kb = (
        KeyboardGrid()
        .add_row(("🩺 سلامت سیستم", "s:health"), ("💰 مدیریت پرداخت‌ها", "s:pays"))
        .add_row(("💎 مدیریت پلن‌ها", "s:plans"), ("🏷 کدهای تخفیف", "s:discs"))
        .add_row(("👥 مدیریت کاربران", "s:users:1"), ("🧠 مدل‌های هوش مصنوعی", "s:models"))
        .add_row(("🔐 عضویت اجباری", "s:mand"), ("🤝 اقتصاد رفرال", "s:ref"))
        .add_row(("📝 متون سیستم", "s:texts"), ("📣 پیام همگانی", "s:bc"))
        .add_row(("🛑 کلید اتوماسیون سراسری", "s:auto"))
        .add_nav_footer(back_cb="u:home", lang=lang)
    )
    return UiResponse(action_id="CB_SUPER_REPORT", text=txt, reply_markup=kb.build())


async def _h_cb_super_health(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    require_superadmin(actor)
    outbox_pending = int(
        (await conn.execute(text("SELECT count(*) FROM outbox WHERE status = 'pending'"))).scalar_one()
    )
    unknown_pub = int(
        (
            await conn.execute(
                text("SELECT count(*) FROM pub_journal WHERE phase = 'delivery_unknown'")
            )
        ).scalar_one()
    )
    degraded_ai = int(
        (
            await conn.execute(
                text("SELECT count(*) FROM ai_models WHERE health_status = 'degraded'")
            )
        ).scalar_one()
    )
    overall = "degraded" if (unknown_pub > 0 or degraded_ai > 0) else "ok"
    crumb = render_breadcrumb(["🛡️ سوپرادمین", "🩺 سلامت سیستم"], lang=lang)
    txt = (
        f"{crumb}\n\n"
        f"🩺 <b>وضعیت کلی:</b> <code>{overall.upper()}</code>\n"
        f"📬 <b>صف Outbox در انتظار:</b> <code>{outbox_pending}</code>\n"
        f"⚠️ <b>انتشار با وضعیت نامشخص:</b> <code>{unknown_pub}</code>\n"
        f"🧠 <b>مدل‌های قرنطینه/Degraded:</b> <code>{degraded_ai}</code>"
    )
    kb = KeyboardGrid().add_nav_footer(back_cb="s:report", lang=lang)
    return UiResponse(action_id="CB_SUPER_HEALTH", text=txt, reply_markup=kb.build())


async def _h_cb_super_plans(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    require_superadmin(actor)
    if raw.startswith("s:plan_free:"):
        pid = int(m.group(1))
        await create_plan(
            conn,
            actor=actor,
            name_fa="پلن رایگان",
            name_en="Free",
            duration_days=3,
            daily_post_limit=2,
            max_sources=2,
            max_channels=1,
            is_free=True,
        ) if pid == 0 else None
        from opengenerate.infrastructure.db.billing_store import set_free_plan

        if pid > 0:
            await set_free_plan(conn, actor=actor, plan_id=pid)
    elif raw.startswith("s:plan_d:"):
        pid = int(m.group(1))
        outcome = await delete_or_deactivate_plan(conn, actor=actor, plan_id=pid)
        return UiResponse(
            action_id="CB_SUPER_PLANS",
            text=f"✅ نتیجه حذف/غیرفعال‌سازی پلن #{pid}: <code>{outcome}</code>",
        )
    elif raw.startswith("s:plan_t:"):
        pid = int(m.group(1))
        p = await get_plan(conn, pid)
        await conn.execute(
            text("UPDATE plans SET is_active = :a WHERE plan_id = :p"),
            {"a": not p.is_active, "p": pid},
        )
    plans = await list_plans(conn, active_only=False)
    crumb = render_breadcrumb(["🛡️ سوپرادمین", "💎 مدیریت پلن‌ها"], lang=lang)
    kb = KeyboardGrid()
    for p in plans:
        kb.add_row((f"💎 {p.name_fa} (#{p.plan_id})", f"s:plan:{p.plan_id}"))
    kb.add_row(("➕ ایجاد پلن جدید", "s:plan_new"))
    kb.add_nav_footer(back_cb="s:report", lang=lang)
    return UiResponse(
        action_id="CB_SUPER_PLANS",
        text=f"{crumb}\n\nکل پلن‌های ثبت‌شده: <code>{len(plans)}</code>",
        reply_markup=kb.build(),
    )


async def _h_cb_super_discounts(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    require_superadmin(actor)
    if raw.startswith("s:disc_t:"):
        code = m.group(1)
        d = await get_discount(conn, code)
        await toggle_discount(conn, actor=actor, code=code, is_active=not d.is_active)
    elif raw.startswith("s:disc_d:"):
        code = m.group(1)
        await delete_discount(conn, actor=actor, code=code)
    discs = await list_discounts(conn, actor=actor)
    crumb = render_breadcrumb(["🛡️ سوپرادمین", "🏷 کدهای تخفیف"], lang=lang)
    kb = KeyboardGrid()
    for d in discs:
        kb.add_row((f"🏷 {d.code} ({d.percent}%)", f"s:disc:{d.code}"))
    kb.add_row(("➕ کد تخفیف جدید", "s:disc_new"))
    kb.add_nav_footer(back_cb="s:report", lang=lang)
    return UiResponse(
        action_id="CB_SUPER_DISCOUNTS",
        text=f"{crumb}\n\nکدهای تخفیف ثبت‌شده: <code>{len(discs)}</code>",
        reply_markup=kb.build(),
    )


async def _h_cb_super_users(
    router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    require_superadmin(actor)
    if raw.startswith("s:uban:"):
        uid = int(m.group(1))
        target = await get_user(conn, uid, router.bootstrap_superadmins)
        await ban_or_unban_user(
            conn,
            actor=actor,
            target_user_id=uid,
            ban=not target.is_banned,
            bootstrap_superadmins=router.bootstrap_superadmins,
        )
    elif raw.startswith("s:urevoke:"):
        uid = int(m.group(1))
        await assign_or_revoke_user_plan(
            conn,
            actor=actor,
            target_user_id=uid,
            plan_id=None,
            idempotency_key=f"ui_revoke:{uid}:{datetime.now(UTC).timestamp()}",
        )
    elif raw.startswith("s:ufree:"):
        uid = int(m.group(1))
        await reset_free_plan_eligibility(conn, actor=actor, target_user_id=uid)
    elif raw.startswith("s:utests:"):
        uid = int(m.group(1))
        await reset_daily_usage(conn, actor=actor, target_user_id=uid, reset_tests=True)
    elif raw.startswith("s:uposts:"):
        uid = int(m.group(1))
        await reset_daily_usage(conn, actor=actor, target_user_id=uid, reset_posts=True)

    users, total = await list_users(
        conn,
        actor=actor,
        admins_only=raw.startswith("s:admins:"),
        page=1,
        bootstrap_superadmins=router.bootstrap_superadmins,
    )
    crumb = render_breadcrumb(["🛡️ سوپرادمین", "👥 مدیریت کاربران"], lang=lang)
    kb = KeyboardGrid()
    for u in users[:10]:
        kb.add_row((f"👤 {u.user_id} ({u.role.value})", f"s:user:{u.user_id}:1"))
    kb.add_nav_footer(back_cb="s:report", lang=lang)
    return UiResponse(
        action_id="CB_SUPER_USERS_AND_ADMINS",
        text=f"{crumb}\n\nکل کاربران: <code>{total}</code>",
        reply_markup=kb.build(),
    )


async def _h_cb_super_models(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    require_superadmin(actor)
    if raw.startswith("s:model_d:"):
        mid = int(m.group(1))
        await delete_ai_model(conn, actor=actor, model_id=mid)
    elif raw.startswith("s:model_test:"):
        mid = int(m.group(1))
        await record_model_probe_result(conn, model_id=mid, succeeded=True)
    models = await list_ai_models(conn, actor=actor, include_global=True)
    crumb = render_breadcrumb(["🛡️ سوپرادمین", "🧠 مدل‌های هوش مصنوعی سراسری"], lang=lang)
    kb = KeyboardGrid()
    for mod in models:
        kb.add_row((f"🧠 {mod.name} ({mod.health_status})", f"s:model:{mod.model_id}"))
    kb.add_row(("➕ افزودن مدل سراسری", "s:model_add"), ("🔄 تست همه مدل‌ها", "s:models_test"))
    kb.add_nav_footer(back_cb="s:report", lang=lang)
    return UiResponse(
        action_id="CB_SUPER_MODELS",
        text=f"{crumb}\n\nکل مدل‌های هوش مصنوعی: <code>{len(models)}</code>",
        reply_markup=kb.build(),
    )


async def _h_cb_super_payments(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    require_superadmin(actor)
    if raw.startswith("s:pay_ok:"):
        pid = int(m.group(1))
        pay = await approve_payment(conn, actor=actor, payment_id=pid)
        return UiResponse(
            action_id="CB_SUPER_PAYMENTS",
            text=f"✅ پرداخت #{pay.payment_id} با موفقیت تایید و اشتراک فعال شد.",
        )
    if raw.startswith("s:pay_no:"):
        pid = int(m.group(1))
        pay = await reject_payment(conn, actor=actor, payment_id=pid, reason="rejected via UI")
        return UiResponse(
            action_id="CB_SUPER_PAYMENTS",
            text=f"❌ پرداخت #{pay.payment_id} رد شد.",
        )
    if raw.startswith("s:pay_refund:"):
        pid = int(m.group(1))
        pay = await refund_payment(conn, actor=actor, payment_id=pid, reason="refunded via UI")
        return UiResponse(
            action_id="CB_SUPER_PAYMENTS",
            text=f"♻️ پرداخت #{pay.payment_id} با موفقیت مسترد (Refund) شد.",
        )

    rows = (
        await conn.execute(
            text("SELECT payment_id, user_id, payable_amount, currency, status FROM pay_requests ORDER BY payment_id DESC LIMIT 15")
        )
    ).all()
    crumb = render_breadcrumb(["🛡️ سوپرادمین", "💰 مدیریت پرداخت‌ها"], lang=lang)
    kb = KeyboardGrid()
    for r in rows:
        rm = r._mapping
        pid = int(rm["payment_id"])
        kb.add_row(
            (f"💳 #{pid} ({rm['status']})", f"s:pay_rc:{pid}"),
            ("✅ تایید", f"s:pay_ok:{pid}"),
            ("❌ رد", f"s:pay_no:{pid}"),
        )
    kb.add_nav_footer(back_cb="s:report", lang=lang)
    return UiResponse(
        action_id="CB_SUPER_PAYMENTS",
        text=f"{crumb}\n\nدرخواست‌های پرداخت اخیر: <code>{len(rows)}</code>",
        reply_markup=kb.build(),
    )


async def _h_cb_super_mandatory_channel(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    require_superadmin(actor)
    if raw.startswith("s:mand_t:"):
        target_lang: Lang = "fa" if m.group(1) == "fa" else "en"
        cur = await get_mandatory_channel_config(conn, target_lang)
        await set_mandatory_channel_config(
            conn,
            actor=actor,
            config=MandatoryChannelConfig(
                lang=target_lang,
                enabled=not cur.enabled,
                channel_id=cur.channel_id,
                channel_username=cur.channel_username,
            ),
        )
    fa_cfg = await get_mandatory_channel_config(conn, "fa")
    en_cfg = await get_mandatory_channel_config(conn, "en")
    crumb = render_breadcrumb(["🛡️ سوپرادمین", "🔐 عضویت اجباری"], lang=lang)
    txt = (
        f"{crumb}\n\n"
        f"🇮🇷 <b>کانال فارسی:</b> {render_status_badge(fa_cfg.enabled, lang=lang)} (<code>{fa_cfg.target_chat or '—'}</code>)\n"
        f"🇬🇧 <b>کانال انگلیسی:</b> {render_status_badge(en_cfg.enabled, lang=lang)} (<code>{en_cfg.target_chat or '—'}</code>)"
    )
    kb = (
        KeyboardGrid()
        .add_row(
            ("🇮🇷 تغییر وضعیت فارسی", "s:mand_t:fa"),
            ("🇬🇧 تغییر وضعیت انگلیسی", "s:mand_t:en"),
        )
        .add_nav_footer(back_cb="s:report", lang=lang)
    )
    return UiResponse(
        action_id="CB_SUPER_MANDATORY_CHANNEL", text=txt, reply_markup=kb.build()
    )


async def _h_cb_super_texts(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    m: re.Match[str],
) -> UiResponse:
    require_superadmin(actor)
    if raw.startswith("s:txtr:"):
        key = m.group(1)
        t_lang: Lang = "fa" if m.group(2) == "fa" else "en"
        await reset_custom_system_text(conn, actor=actor, key=key, lang=t_lang)
    crumb = render_breadcrumb(["🛡️ سوپرادمین", "📝 متون سیستم"], lang=lang)
    kb = KeyboardGrid()
    for k in DEFAULT_SYSTEM_TEXTS:
        kb.add_row(
            (f"📝 {k} (FA)", f"s:txt:{k}:fa"),
            (f"🔄 بازنشانی {k}", f"s:txtr:{k}:fa"),
        )
    kb.add_nav_footer(back_cb="s:report", lang=lang)
    return UiResponse(
        action_id="CB_SUPER_TEXTS",
        text=f"{crumb}\n\nکلیدهای قابل ویرایش متون سیستم:",
        reply_markup=kb.build(),
    )


async def _h_cb_super_broadcast(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    _m: re.Match[str],
) -> UiResponse:
    require_superadmin(actor)
    if raw == "s:bc:confirm":
        rows = await conn.execute(text("SELECT user_id FROM users WHERE is_banned = FALSE"))
        cnt = 0
        for r in rows:
            uid = int(r._mapping["user_id"])
            await outbox.enqueue(
                conn,
                idempotency_key=f"bc:{int(datetime.now(UTC).timestamp())}:{uid}",
                recipient_id=uid,
                kind="text",
                payload={"text": "📣 اطلاعیه سامانه OpenGenerate"},
            )
            cnt += 1
        return UiResponse(
            action_id="CB_SUPER_BROADCAST",
            text=f"✅ پیام همگانی در صف Outbox برای <code>{cnt}</code> کاربر قرار گرفت.",
        )

    txt, markup = build_confirmation_screen(
        breadcrumbs=["🛡️ سوپرادمین", "📣 پیام همگانی"],
        warning_title="تایید ارسال پیام همگانی (Broadcast)",
        warning_body="پیام همگانی از طریق صف Outbox با رعایت محدودیت نرخ تلگرام ارسال خواهد شد.",
        confirm_cb="s:bc:confirm",
        lang=lang,
    )
    return UiResponse(action_id="CB_SUPER_BROADCAST", text=txt, reply_markup=markup)


async def _h_cb_super_global_automation(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    raw: str,
    _m: re.Match[str],
) -> UiResponse:
    require_superadmin(actor)
    row = (
        await conn.execute(
            text("SELECT value FROM settings WHERE key = 'global_automation_enabled'")
        )
    ).first()
    cur_enabled = True
    if row is not None:
        v = row._mapping["value"]
        d = json.loads(v) if isinstance(v, str) else v
        cur_enabled = bool(d.get("enabled", True))

    if raw == "s:auto:confirm":
        new_val = not cur_enabled
        payload = json.dumps({"enabled": new_val})
        await conn.execute(
            text(
                "INSERT INTO settings(key, value, updated_by, updated_at) "
                "VALUES ('global_automation_enabled', CAST(:v AS jsonb), :u, now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
            ),
            {"v": payload, "u": actor.user_id},
        )
        return UiResponse(
            action_id="CB_SUPER_GLOBAL_AUTOMATION",
            text=f"🛑 وضعیت اتوماسیون سراسری پلتفرم به {render_status_badge(new_val, lang=lang)} تغییر یافت (تنظیمات تک‌تک کانال‌ها دست‌نخورده باقی ماند).",
        )

    txt, markup = build_confirmation_screen(
        breadcrumbs=["🛡️ سوپرادمین", "🛑 کلید اتوماسیون سراسری"],
        warning_title="تغییر وضعیت اتوماسیون سراسری پلتفرم",
        warning_body=f"وضعیت فعلی: {render_status_badge(cur_enabled, lang=lang)}",
        confirm_cb="s:auto:confirm",
        lang=lang,
    )
    return UiResponse(
        action_id="CB_SUPER_GLOBAL_AUTOMATION", text=txt, reply_markup=markup
    )


async def _h_cb_super_referral_economics(
    _router: TelegramUiRouter,
    conn: AsyncConnection,
    actor: Actor,
    lang: Lang,
    _raw: str,
    _m: re.Match[str],
) -> UiResponse:
    require_superadmin(actor)
    econ = await get_referral_economics(conn)
    crumb = render_breadcrumb(["🛡️ سوپرادمین", "🤝 اقتصاد رفرال"], lang=lang)
    txt = (
        f"{crumb}\n\n"
        f"📊 <b>درصد پاداش فعلی:</b> <code>{econ.percent}%</code> (محدوده مجاز 0..50%)\n"
        f"💰 <b>سقف پاداش تومانی:</b> <code>{econ.cap_tmn:,}</code>\n"
        f"💵 <b>سقف پاداش دلاری (سنت):</b> <code>{econ.cap_usd:,}</code>"
    )
    kb = (
        KeyboardGrid()
        .add_row(
            ("📊 ویرایش درصد پاداش", "s:ref:pct"),
            ("💰 سقف تومان", "s:ref:cap_tmn"),
            ("💵 سقف دلار", "s:ref:cap_usd"),
        )
        .add_nav_footer(back_cb="s:report", lang=lang)
    )
    return UiResponse(
        action_id="CB_SUPER_REFERRAL_ECONOMICS", text=txt, reply_markup=kb.build()
    )


# ---- Declarative 38-Action / 61-Trigger Registry ---------------------------------------------

ALL_ROLES = frozenset({Role.USER, Role.ADMIN, Role.SUPERADMIN})
ADMIN_ROLES = frozenset({Role.ADMIN, Role.SUPERADMIN})
SUPER_ONLY = frozenset({Role.SUPERADMIN})

ROUTE_SPECS: tuple[RouteSpec, ...] = (
    RouteSpec(
        "CMD_START",
        (re.compile(r"^/start(?:\s+(?:ref_\d+|dl_[A-Za-z0-9_-]+))?$"),),
        ALL_ROLES,
        False,
        _h_cmd_start,
    ),
    RouteSpec("CMD_LANG", (re.compile(r"^/lang$"),), ALL_ROLES, False, _h_cmd_lang),
    RouteSpec("CMD_HELP", (re.compile(r"^/help$"),), ALL_ROLES, False, _h_cmd_help),
    RouteSpec("CMD_ABOUT", (re.compile(r"^/about$"),), ALL_ROLES, False, _h_cmd_about),
    RouteSpec("CMD_REF", (re.compile(r"^/ref$"),), ALL_ROLES, False, _h_cmd_ref),
    RouteSpec("CMD_MAN", (re.compile(r"^/man$"),), ALL_ROLES, False, _h_cmd_man),
    RouteSpec("CMD_CANCEL", (re.compile(r"^/cancel$"),), ALL_ROLES, False, _h_cmd_cancel),
    RouteSpec("CMD_CREATE", (re.compile(r"^/create$"),), ALL_ROLES, False, _h_cmd_create),
    RouteSpec("CB_USER_HOME", (re.compile(r"^u:home$"),), ALL_ROLES, False, _h_cb_user_home),
    RouteSpec("CB_USER_PLANS", (re.compile(r"^u:plans$"),), ALL_ROLES, False, _h_cb_user_plans),
    RouteSpec(
        "CB_USER_PLAN_DETAIL",
        (re.compile(r"^u:plan:(\d+)$"),),
        ALL_ROLES,
        False,
        _h_cb_user_plan_detail,
    ),
    RouteSpec(
        "CB_USER_FREE_CLAIM",
        (re.compile(r"^u:free:(\d+)$"),),
        ALL_ROLES,
        False,
        _h_cb_user_free_claim,
    ),
    RouteSpec(
        "CB_USER_FREE_USED",
        (re.compile(r"^u:freeused:(\d+)$"),),
        ALL_ROLES,
        False,
        _h_cb_user_free_used,
    ),
    RouteSpec(
        "CB_USER_PLAN_LOCKED",
        (re.compile(r"^u:locked:(\d+)$"),),
        ALL_ROLES,
        False,
        _h_cb_user_plan_locked,
    ),
    RouteSpec(
        "CB_USER_PAYMENT_PENDING",
        (re.compile(r"^u:pending:(\d+)$"),),
        ALL_ROLES,
        False,
        _h_cb_user_payment_pending,
    ),
    RouteSpec(
        "CB_USER_DISCOUNT_APPLY",
        (re.compile(r"^u:disc:(\d+)$"),),
        ALL_ROLES,
        False,
        _h_cb_user_discount_apply,
    ),
    RouteSpec(
        "CB_USER_DISCOUNT_REMOVE",
        (re.compile(r"^u:discx:(\d+)$"),),
        ALL_ROLES,
        False,
        _h_cb_user_discount_remove,
    ),
    RouteSpec(
        "CB_USER_REQUEST_PAYMENT",
        (re.compile(r"^u:req:(\d+)$"),),
        ALL_ROLES,
        False,
        _h_cb_user_request_payment,
    ),
    RouteSpec(
        "CB_USER_REFERRAL", (re.compile(r"^u:ref$"),), ALL_ROLES, False, _h_cb_user_referral
    ),
    RouteSpec(
        "CB_USER_SUPPORT",
        (re.compile(r"^u:(?:sup|support|sclose)$"),),
        ALL_ROLES,
        False,
        _h_cb_user_support,
    ),
    RouteSpec(
        "CB_USER_MANAGE_PANEL",
        (re.compile(r"^u:man$"),),
        ALL_ROLES,
        False,
        _h_cb_user_manage_panel,
    ),
    RouteSpec("CB_USER_HELP", (re.compile(r"^u:help$"),), ALL_ROLES, False, _h_cb_user_help),
    RouteSpec(
        "CB_USER_LANG",
        (re.compile(r"^u:lang(?::(?:fa|en))?$"),),
        ALL_ROLES,
        False,
        _h_cb_user_lang,
    ),
    RouteSpec(
        "CB_MANDATORY_MEMBERSHIP_CHECK",
        (re.compile(r"^a:mand$"),),
        ALL_ROLES,
        False,
        _h_cb_mandatory_membership,
    ),
    RouteSpec(
        "CB_SHARED_CANCEL",
        (re.compile(r"^c:cancel$"),),
        ALL_ROLES,
        False,
        _h_cb_shared_cancel,
    ),
    RouteSpec("CB_ADMIN_HOME", (re.compile(r"^a:home$"),), ALL_ROLES, False, _h_cb_admin_home),
    RouteSpec(
        "CB_ADMIN_MY_PLAN", (re.compile(r"^a:plan$"),), ALL_ROLES, False, _h_cb_admin_my_plan
    ),
    RouteSpec(
        "CB_ADMIN_CHANNEL_ADD",
        (re.compile(r"^a:chadd$"),),
        ALL_ROLES,
        False,
        _h_cb_admin_channel_add,
    ),
    RouteSpec(
        "CB_ADMIN_CHANNEL_HOME",
        (re.compile(r"^a:ch:(-?\d+)$"),),
        ADMIN_ROLES,
        False,
        _h_cb_admin_channel_home,
    ),
    RouteSpec(
        "CB_ADMIN_CHANNEL_TEST",
        (re.compile(r"^a:test:(-?\d+)$"),),
        ADMIN_ROLES,
        False,
        _h_cb_admin_channel_test,
    ),
    RouteSpec(
        "CB_ADMIN_CHANNEL_REPORT",
        (re.compile(r"^a:rep:(-?\d+)$"),),
        ADMIN_ROLES,
        False,
        _h_cb_admin_channel_report,
    ),
    RouteSpec(
        "CB_ADMIN_CHANNEL_AUTOMATION_TOGGLE",
        (re.compile(r"^a:tog:(-?\d+):enabled$"),),
        ADMIN_ROLES,
        False,
        _h_cb_admin_channel_toggle,
    ),
    RouteSpec(
        "CB_ADMIN_CHANNEL_DELETE",
        (re.compile(r"^a:chdel:(-?\d+)(?::confirm)?$"),),
        ADMIN_ROLES,
        True,
        _h_cb_admin_channel_delete,
    ),
    RouteSpec(
        "CB_ADMIN_SOURCES_LIST",
        (re.compile(r"^a:src:(-?\d+)(?::(\d+))?$"),),
        ADMIN_ROLES,
        False,
        _h_cb_admin_sources_list,
    ),
    RouteSpec(
        "CB_ADMIN_SOURCE_ADD",
        (re.compile(r"^a:srca:(-?\d+)$"),),
        ADMIN_ROLES,
        False,
        _h_cb_admin_source_add,
    ),
    RouteSpec(
        "CB_ADMIN_SOURCE_INSPECT",
        (re.compile(r"^a:srcv:(\d+)$"),),
        ADMIN_ROLES,
        False,
        _h_cb_admin_source_inspect,
    ),
    RouteSpec(
        "CB_ADMIN_SOURCE_RECHECK",
        (re.compile(r"^a:srcr:(\d+)$"),),
        ADMIN_ROLES,
        False,
        _h_cb_admin_source_recheck,
    ),
    RouteSpec(
        "CB_ADMIN_SOURCE_TOGGLE_FETCH",
        (re.compile(r"^a:srct:(\d+)$"),),
        ADMIN_ROLES,
        False,
        _h_cb_admin_source_toggle_fetch,
    ),
    RouteSpec(
        "CB_ADMIN_SOURCE_TOGGLE_PUBLISH",
        (re.compile(r"^a:srctb:(\d+)$"),),
        ADMIN_ROLES,
        False,
        _h_cb_admin_source_toggle_publish,
    ),
    RouteSpec(
        "CB_ADMIN_SOURCE_CUSTOM_API",
        (re.compile(r"^a:srcapi:(\d+)$"),),
        ADMIN_ROLES,
        False,
        _h_cb_admin_source_custom_api,
    ),
    RouteSpec(
        "CB_ADMIN_SOURCE_CUSTOM_API_CLEAR",
        (re.compile(r"^a:srcapix:(\d+)$"),),
        ADMIN_ROLES,
        True,
        _h_cb_admin_source_custom_api_clear,
    ),
    RouteSpec(
        "CB_ADMIN_SOURCE_DELETE",
        (re.compile(r"^a:srcd:(\d+)(?::confirm)?$"),),
        ADMIN_ROLES,
        True,
        _h_cb_admin_source_delete,
    ),
    RouteSpec(
        "CB_ADMIN_CONTENT_MENU",
        (re.compile(r"^a:con:(-?\d+)$"),),
        ADMIN_ROLES,
        False,
        _h_cb_admin_content_menu,
    ),
    RouteSpec(
        "CB_ADMIN_CONTENT_SET_FIELD",
        (re.compile(r"^a:set:(-?\d+):([a-z_]+)$"),),
        ADMIN_ROLES,
        False,
        _h_cb_admin_content_set_field,
    ),
    RouteSpec(
        "CB_ADMIN_CATEGORIES",
        (
            re.compile(r"^a:cat:(-?\d+)(?::\d+)?$"),
            re.compile(r"^a:cat[vsd]:(-?\d+):\d+$"),
            re.compile(r"^a:cata:(-?\d+)$"),
        ),
        ADMIN_ROLES,
        True,
        _h_cb_admin_categories,
    ),
    RouteSpec(
        "CB_ADMIN_CRITERIA",
        (
            re.compile(r"^a:cri:(-?\d+)(?::\d+)?$"),
            re.compile(r"^a:cri[vwd]:(-?\d+):\d+$"),
            re.compile(r"^a:cria:(-?\d+)$"),
        ),
        ADMIN_ROLES,
        True,
        _h_cb_admin_criteria,
    ),
    RouteSpec(
        "CB_ADMIN_SCHEDULING",
        (
            re.compile(r"^a:(?:sch|quiet|qoff|tz|mode):(-?\d+)$"),
            re.compile(r"^a:qs:(-?\d+):\d+$"),
            re.compile(r"^a:tzs:(-?\d+):[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)?$"),
        ),
        ADMIN_ROLES,
        False,
        _h_cb_admin_scheduling,
    ),
    RouteSpec(
        "CB_ADMIN_QUEUE_AND_ARTICLES",
        (
            re.compile(r"^a:que:(-?\d+)(?::[a-z]+:\d+)?$"),
            re.compile(r"^a:rej:(-?\d+)(?::\d+)?$"),
            re.compile(r"^a:(?:art|arte|artf|pub|artr|artd):(\d+)$"),
            re.compile(r"^a:qreset:(-?\d+)$"),
        ),
        ADMIN_ROLES,
        True,
        _h_cb_admin_queue_and_articles,
    ),
    RouteSpec(
        "CB_ADMIN_PERSONAL_AI",
        (
            re.compile(r"^a:myai(?:_add)?:(-?\d+)$"),
            re.compile(r"^a:myai_[vtoed]:(\d+)$"),
        ),
        ADMIN_ROLES,
        True,
        _h_cb_admin_personal_ai,
    ),
    RouteSpec("CB_SUPER_REPORT", (re.compile(r"^s:report$"),), SUPER_ONLY, False, _h_cb_super_report),
    RouteSpec("CB_SUPER_HEALTH", (re.compile(r"^s:health$"),), SUPER_ONLY, False, _h_cb_super_health),
    RouteSpec(
        "CB_SUPER_PLANS",
        (
            re.compile(r"^s:plans$"),
            re.compile(r"^s:plan_new$"),
            re.compile(r"^s:plan:(\d+)$"),
            re.compile(r"^s:plan_e:(\d+):[a-z_]+$"),
            re.compile(r"^s:plan_(?:t|free|d):(\d+)$"),
        ),
        SUPER_ONLY,
        True,
        _h_cb_super_plans,
    ),
    RouteSpec(
        "CB_SUPER_DISCOUNTS",
        (
            re.compile(r"^s:discs$"),
            re.compile(r"^s:disc_new$"),
            re.compile(r"^s:disc:([A-Za-z0-9_-]+)$"),
            re.compile(r"^s:disc_e:([A-Za-z0-9_-]+):[a-z_]+$"),
            re.compile(r"^s:disc_[td]:([A-Za-z0-9_-]+)$"),
        ),
        SUPER_ONLY,
        True,
        _h_cb_super_discounts,
    ),
    RouteSpec(
        "CB_SUPER_USERS_AND_ADMINS",
        (
            re.compile(r"^s:(?:users|admins):(\d+)$"),
            re.compile(r"^s:user:(\d+):\d+$"),
            re.compile(r"^s:(?:uplan|urevoke|uban|umsg|ureport|ufree|utests|uposts):(\d+)$"),
        ),
        SUPER_ONLY,
        True,
        _h_cb_super_users,
    ),
    RouteSpec(
        "CB_SUPER_MODELS",
        (
            re.compile(r"^s:models(?:_test)?$"),
            re.compile(r"^s:model_add$"),
            re.compile(r"^s:model(?:_test|_t|_e|_d|_test_pick)?:(\d+)$"),
        ),
        SUPER_ONLY,
        True,
        _h_cb_super_models,
    ),
    RouteSpec(
        "CB_SUPER_PAYMENTS",
        (
            re.compile(r"^s:pays$"),
            re.compile(r"^s:pay_(?:rc|ok|no|refund):(\d+)$"),
        ),
        SUPER_ONLY,
        True,
        _h_cb_super_payments,
    ),
    RouteSpec(
        "CB_SUPER_MANDATORY_CHANNEL",
        (
            re.compile(r"^s:mand$"),
            re.compile(r"^s:mand_t:(fa|en)$"),
            re.compile(r"^s:mand_e:(fa|en):(?:id|user)$"),
        ),
        SUPER_ONLY,
        False,
        _h_cb_super_mandatory_channel,
    ),
    RouteSpec(
        "CB_SUPER_TEXTS",
        (
            re.compile(r"^s:texts$"),
            re.compile(r"^s:txt:([a-z_]+):(fa|en)$"),
            re.compile(r"^s:txtr:([a-z_]+):(fa|en)$"),
        ),
        SUPER_ONLY,
        False,
        _h_cb_super_texts,
    ),
    RouteSpec(
        "CB_SUPER_BROADCAST",
        (re.compile(r"^s:bc(?::confirm)?$"),),
        SUPER_ONLY,
        True,
        _h_cb_super_broadcast,
    ),
    RouteSpec(
        "CB_SUPER_GLOBAL_AUTOMATION",
        (re.compile(r"^s:auto(?::confirm)?$"),),
        SUPER_ONLY,
        True,
        _h_cb_super_global_automation,
    ),
    RouteSpec(
        "CB_SUPER_REFERRAL_ECONOMICS",
        (re.compile(r"^s:ref(?::(?:pct|cap_tmn|cap_usd))?$"),),
        SUPER_ONLY,
        False,
        _h_cb_super_referral_economics,
    ),
)

ACTION_ROUTE_REGISTRY: dict[str, RouteSpec] = {spec.action_id: spec for spec in ROUTE_SPECS}
