"""Ergonomic & Visual UI Design System: Breadcrumbs, Progress Gauges, Keyboards & Bilingual Localization."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.application.policy import require_superadmin
from opengenerate.domain.errors import ValidationFailed
from opengenerate.domain.money import Currency
from opengenerate.domain.roles import Actor

Lang = Literal["fa", "en"]
MAX_CALLBACK_BYTES = 64

_ALLOWED_HTML_TAGS = frozenset({"b", "i", "u", "s", "code", "pre", "a", "blockquote", "tg-spoiler"})
_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z0-9-]+)(?:\s[^>]*)?>")

DEFAULT_SYSTEM_TEXTS: dict[str, dict[Lang, str]] = {
    "welcome": {
        "fa": (
            "✨ <b>به پلتفرم هوشمند مدیریت و تولید محتوای علمی و تحریریه OpenGenerate خوش آمدید!</b>\n\n"
            "از طریق دکمه‌های زیر می‌توانید اشتراک خود را مدیریت کنید، کانال‌های خود را به موتور "
            "هوش مصنوعی متصل نمایید و انتشار خودکار را با قالب‌بندی تخصصی هر شاخه علمی تجربه کنید."
        ),
        "en": (
            "✨ <b>Welcome to OpenGenerate — Intelligent Scientific & Editorial Channel Automation!</b>\n\n"
            "Use the buttons below to manage your subscription, connect your channels to our "
            "multi-provider AI engine, and automate domain-adaptive publishing."
        ),
    },
    "help": {
        "fa": (
            "📚 <b>راهنمای جامع سامانه OpenGenerate</b>\n\n"
            "۱. ابتدا از بخش <b>💎 اشتراک‌ها و پلن‌ها</b>، پلن رایگان یا تخصصی خود را فعال کنید.\n"
            "۲. از بخش <b>⚙️ مدیریت کانال‌ها</b>، ربات را در کانال خود ادمین کرده و کانال را ثبت نمایید.\n"
            "۳. منابع خبری/علمی (RSS، سایت‌مپ، JSON یا وب‌سایت) را اضافه کنید.\n"
            "۴. سبک نگارش، معیارهای امتیازدهی و زمان‌بندی انتشار را تنظیم کرده و اتوماسیون را روشن کنید."
        ),
        "en": (
            "📚 <b>OpenGenerate Platform Guide</b>\n\n"
            "1. Activate a free trial or paid plan under <b>💎 Plans & Subscriptions</b>.\n"
            "2. Make the bot an admin in your channel and register it under <b>⚙️ Manage Channels</b>.\n"
            "3. Add content sources (RSS, Sitemap, JSON Feed, or HTML).\n"
            "4. Configure editorial style, scoring criteria, and schedule, then enable automation."
        ),
    },
    "about": {
        "fa": (
            "🤖 <b>درباره OpenGenerate (نسخه ۲.۰ — معماری ماژولار PostgreSQL)</b>\n\n"
            "• موتور کشف ۵ لایه و استخراج متن کامل چندصفحه‌ای\n"
            "• تشخیص خودکار شاخه علمی و قالب‌بندی متناسب (جدولی، موردی، گام‌به‌گام، آماری، تحلیلی)\n"
            "• پشتیبانی کامل از Rich Message و ادامه مطلب تعاملی در ربات\n"
            "• دفترکل مالی تغییرناپذیر و انتشار ضدسقوط ۶ مرحله‌ای"
        ),
        "en": (
            "🤖 <b>About OpenGenerate (v2.0 — PostgreSQL Modular Monolith)</b>\n\n"
            "• 5-Strategy Discovery & Multi-Page Full-Text Extraction\n"
            "• Domain-Adaptive Scientific Structuring (Tabular, Itemized, Sequential, Statistical, Analytical)\n"
            "• Rich Message & Interactive Bot Continuation Reader\n"
            "• Immutable Financial Ledger & 6-Phase Crash-Safe Publishing"
        ),
    },
}


def norm_lang(lang: str | None) -> Lang:
    return "en" if lang == "en" else "fa"


def render_breadcrumb(crumbs: Sequence[str], *, lang: Lang = "fa") -> str:
    """Render an ergonomic visual breadcrumb header bar in RTL (`◂`) or LTR (`▸`)."""
    clean = [c.strip() for c in crumbs if c.strip()]
    if not clean:
        return ""
    sep = " ◂ " if lang == "fa" else " ▸ "
    return f"🧭 <code>{sep.join(clean)}</code>\n────────────────────"


def render_quota_gauge(used: int, limit: int, *, width: int = 8) -> str:
    """Render a sleek visual Unicode progress gauge (`▰▰▰▰▱▱▱▱ 50% (5/10)`)."""
    safe_used = max(0, used)
    if limit <= 0:
        return f"{'▱' * width} (0/0)"
    ratio = min(1.0, safe_used / limit)
    filled = round(ratio * width)
    bar = ("▰" * filled) + ("▱" * (width - filled))
    pct = round(ratio * 100)
    return f"<code>{bar}</code> <b>{pct}%</b> ({safe_used}/{limit})"


def validate_callback_data(cb_data: str) -> str:
    """Enforce Telegram's 1..64 UTF-8 byte limit on callback_data."""
    encoded_len = len(cb_data.encode("utf-8"))
    if encoded_len == 0 or encoded_len > MAX_CALLBACK_BYTES:
        raise ValidationFailed(
            f"callback_data '{cb_data}' must be 1..64 bytes (got {encoded_len} bytes)"
        )
    return cb_data


def render_status_badge(active: bool, *, lang: Lang = "fa") -> str:
    if active:
        return "🟢 فعال" if lang == "fa" else "🟢 Active"
    return "🔴 غیرفعال" if lang == "fa" else "🔴 Paused"


def format_money(amount: int | None, currency: Currency, *, lang: Lang = "fa") -> str:
    if amount is None:
        return "توافقی" if lang == "fa" else "Negotiated"
    if currency is Currency.TMN:
        unit = "تومان" if lang == "fa" else "TMN"
        return f"{amount:,} {unit}"
    if currency is Currency.USD:
        dollars = amount / 100.0
        return f"${dollars:,.2f} USD"
    return f"{amount:,} LEGACY"


def validate_safe_telegram_html(html_text: str) -> str:
    """Verify that custom localization HTML has balanced, allowed Telegram HTML tags."""
    stack: list[str] = []
    for match in _HTML_TAG_RE.finditer(html_text):
        is_closing = bool(match.group(1))
        tag = match.group(2).lower()
        if tag not in _ALLOWED_HTML_TAGS:
            raise ValidationFailed(f"unsupported HTML tag <{tag}>")
        if not is_closing:
            stack.append(tag)
        else:
            if not stack or stack[-1] != tag:
                raise ValidationFailed(f"unbalanced closing HTML tag </{tag}>")
            stack.pop()
    if stack:
        raise ValidationFailed(f"unclosed HTML tag <{stack[-1]}>")
    return html_text


@dataclass(slots=True)
class KeyboardGrid:
    """Ergonomic inline keyboard builder enforcing Telegram's 64-byte `callback_data` invariant."""

    rows: list[list[dict[str, str]]] = field(default_factory=list)

    def add_row(self, *buttons: tuple[str, str]) -> KeyboardGrid:
        row: list[dict[str, str]] = []
        for label, cb_data in buttons:
            encoded_len = len(cb_data.encode("utf-8"))
            if encoded_len > MAX_CALLBACK_BYTES:
                raise ValidationFailed(
                    f"callback_data '{cb_data}' exceeds 64 bytes ({encoded_len} bytes)"
                )
            row.append({"text": label, "callback_data": cb_data})
        if row:
            self.rows.append(row)
        return self

    def add_url_row(self, label: str, url: str) -> KeyboardGrid:
        self.rows.append([{"text": label, "url": url}])
        return self

    def add_pagination(
        self,
        *,
        base_prefix: str,
        page: int,
        total_items: int,
        page_size: int = 15,
        lang: Lang = "fa",
    ) -> KeyboardGrid:
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        if total_pages <= 1:
            return self
        nav_row: list[tuple[str, str]] = []
        if page > 1:
            prev_lbl = "⏮ قبلی" if lang == "fa" else "⏮ Prev"
            nav_row.append((prev_lbl, f"{base_prefix}:{page - 1}"))
        page_lbl = f"📄 {page}/{total_pages}"
        nav_row.append((page_lbl, f"{base_prefix}:{page}"))
        if page < total_pages:
            next_lbl = "بعدی ⏭" if lang == "fa" else "Next ⏭"
            nav_row.append((next_lbl, f"{base_prefix}:{page + 1}"))
        return self.add_row(*nav_row)

    def add_nav_footer(
        self,
        *,
        back_cb: str | None = None,
        home_cb: str = "u:home",
        lang: Lang = "fa",
    ) -> KeyboardGrid:
        buttons: list[tuple[str, str]] = []
        if back_cb:
            buttons.append(("🔙 بازگشت" if lang == "fa" else "🔙 Back", back_cb))
        buttons.append(("🏠 خانه" if lang == "fa" else "🏠 Home", home_cb))
        return self.add_row(*buttons)

    def add_cancel_button(self, *, lang: Lang = "fa") -> KeyboardGrid:
        lbl = "🔙 انصراف و بازگشت" if lang == "fa" else "🔙 Cancel & Back"
        return self.add_row((lbl, "c:cancel"))

    def build(self) -> dict[str, Any]:
        return {"inline_keyboard": self.rows}


def build_confirmation_screen(
    *,
    breadcrumbs: Sequence[str],
    warning_title: str,
    warning_body: str,
    confirm_cb: str,
    cancel_cb: str = "c:cancel",
    lang: Lang = "fa",
) -> tuple[str, dict[str, Any]]:
    """Build a standardized two-step confirmation dialog for destructive actions."""
    header = render_breadcrumb(breadcrumbs, lang=lang)
    confirm_lbl = "✅ بله، تایید و اجرا شود" if lang == "fa" else "✅ Yes, Confirm & Execute"
    cancel_lbl = "🔙 انصراف و بازگشت" if lang == "fa" else "🔙 Cancel & Back"
    kb = KeyboardGrid().add_row((confirm_lbl, confirm_cb)).add_row((cancel_lbl, cancel_cb))
    text_out = f"{header}\n\n⚠️ <b>{warning_title}</b>\n\n{warning_body}"
    return text_out, kb.build()


# ---- Customizable System Texts (`s:texts`, `s:txt:<key>:<lang>`, `s:txtr:<key>:<lang>`) ------


async def get_system_text(
    conn: AsyncConnection,
    *,
    key: str,
    lang: Lang,
) -> str:
    setting_key = f"i18n:{key}:{lang}"
    row = (
        await conn.execute(text("SELECT value FROM settings WHERE key = :k"), {"k": setting_key})
    ).first()
    if row is not None:
        val = row._mapping["value"]
        data = json.loads(val) if isinstance(val, str) else val
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            return str(data["text"])
    return DEFAULT_SYSTEM_TEXTS.get(key, {}).get(lang, key)


async def set_custom_system_text(
    conn: AsyncConnection,
    *,
    actor: Actor,
    key: str,
    lang: Lang,
    new_text: str,
) -> str:
    require_superadmin(actor)
    if key not in DEFAULT_SYSTEM_TEXTS:
        raise ValidationFailed(f"unknown system text key: {key}")
    if lang not in ("fa", "en"):
        raise ValidationFailed("lang must be 'fa' or 'en'")
    cleaned = validate_safe_telegram_html(new_text.strip())
    if not cleaned:
        raise ValidationFailed("custom text cannot be empty")
    setting_key = f"i18n:{key}:{lang}"
    payload = json.dumps({"text": cleaned}, ensure_ascii=False)
    await conn.execute(
        text(
            "INSERT INTO settings(key, value, updated_by, updated_at) "
            "VALUES (:k, CAST(:v AS jsonb), :u, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()"
        ),
        {"k": setting_key, "v": payload, "u": actor.user_id},
    )
    return cleaned


async def reset_custom_system_text(
    conn: AsyncConnection,
    *,
    actor: Actor,
    key: str,
    lang: Lang,
) -> str:
    require_superadmin(actor)
    setting_key = f"i18n:{key}:{lang}"
    await conn.execute(text("DELETE FROM settings WHERE key = :k"), {"k": setting_key})
    return DEFAULT_SYSTEM_TEXTS.get(key, {}).get(lang, key)
