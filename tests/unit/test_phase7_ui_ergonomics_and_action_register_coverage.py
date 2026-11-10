"""Unit tests for Phase 7: Telegram UI Ergonomics & 100% UI Action Register Coverage."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from opengenerate.domain.errors import ValidationFailed
from opengenerate.domain.subscriptions import Currency
from opengenerate.interfaces.telegram.ergonomics import (
    KeyboardGrid,
    build_confirmation_screen,
    format_money,
    render_breadcrumb,
    render_quota_gauge,
    render_status_badge,
    validate_callback_data,
    validate_safe_telegram_html,
)
from opengenerate.interfaces.telegram.router import ACTION_ROUTE_REGISTRY, ROUTE_SPECS


def _sample_trigger_from_template(raw_trigger_template: str) -> list[str]:
    """Expand a trigger specification from ui-action-register.json into concrete test strings."""
    if raw_trigger_template.startswith("/start"):
        return ["/start", "/start ref_1001", "/start dl_AbCdEf123"]
    parts = [p.strip() for p in raw_trigger_template.split("|")]
    samples: list[str] = []
    for part in parts:
        # Handle optional brackets like a:src:<channel_id>[:<page>] or a:srcd:<source_id>[:confirm]
        if "[" in part and "]" in part:
            base = re.sub(r"\[[^\]]*\]", "", part)
            with_opt = part.replace("[", "").replace("]", "")
            for variant in (base, with_opt):
                samples.append(_fill_placeholders(variant))
        else:
            samples.append(_fill_placeholders(part))
    return samples


def _fill_placeholders(template: str) -> str:
    rep = template
    rep = rep.replace("<channel_id>", "-1001234567890")
    rep = rep.replace("<ch_id>", "-1001234567890")
    rep = rep.replace("<plan_id>", "7")
    rep = rep.replace("<source_id>", "42")
    rep = rep.replace("<m_id>", "11")
    rep = rep.replace("<uid>", "9001")
    rep = rep.replace("<id>", "15")
    rep = rep.replace("<idx>", "0")
    rep = rep.replace("<page>", "2")
    rep = rep.replace("<p>", "1")
    rep = rep.replace("<h>", "8")
    rep = rep.replace("<tz>", "Asia/Tehran")
    rep = rep.replace("<sort>", "score")
    rep = rep.replace("<field>", "hashtags")
    rep = rep.replace("<code>", "SPRING20")
    rep = rep.replace("<lang>", "fa")
    rep = rep.replace("<key>", "welcome")
    return rep


def test_rtl_and_ltr_breadcrumbs_and_gauges() -> None:
    fa_crumb = render_breadcrumb(["🏠 خانه", "🎛 کانال‌ها", "📢 دیجیاتو"], lang="fa")
    en_crumb = render_breadcrumb(["🏠 Home", "🎛 Channels", "📢 Tech"], lang="en")
    assert " ◂ " in fa_crumb
    assert " ▸ " in en_crumb

    gauge = render_quota_gauge(5, 10, width=10)
    assert "▰" in gauge and "▱" in gauge
    assert "50%" in gauge and "(5/10)" in gauge
    assert "(0/0)" in render_quota_gauge(0, 0)

    assert "🟢" in render_status_badge(True, lang="fa")
    assert "🔴" in render_status_badge(False, lang="en")


def test_format_money_and_64_byte_callback_guard() -> None:
    assert "تومان" in format_money(250000, Currency.TMN, lang="fa")
    assert "USD" in format_money(1500, Currency.USD, lang="en")

    grid = KeyboardGrid()
    grid.add_row(("🏠 خانه", "u:home"), ("💎 پلن‌ها", "u:plans"))
    grid.add_pagination(base_prefix="a:src:-1001", page=2, total_items=40, page_size=15, lang="fa")
    grid.add_nav_footer(back_cb="a:ch:-1001", lang="fa")
    markup = grid.build()
    assert len(markup["inline_keyboard"]) == 3

    with pytest.raises(ValidationFailed):
        validate_callback_data("x" * 65)


def test_confirmation_screen_and_html_tag_balance_validation() -> None:
    txt, markup = build_confirmation_screen(
        breadcrumbs=["🏠 خانه", "🗑 حذف کانال"],
        warning_title="هشدار حذف",
        warning_body="آیا مطمئن هستید؟",
        confirm_cb="a:chdel:-1001:confirm",
        lang="fa",
    )
    assert "هشدار حذف" in txt
    callbacks = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    assert "a:chdel:-1001:confirm" in callbacks
    assert "c:cancel" in callbacks

    valid_html = "<b>سلام</b> <i>کاربر</i> <code>123</code>"
    assert validate_safe_telegram_html(valid_html) == valid_html

    with pytest.raises(ValidationFailed):
        validate_safe_telegram_html("<b>Unclosed bold")

    with pytest.raises(ValidationFailed):
        validate_safe_telegram_html("<script>alert(1)</script>")


def test_ui_action_register_100_percent_bijective_coverage() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    register_path = repo_root / "docs" / "product" / "ui-action-register.json"
    data = json.loads(register_path.read_text(encoding="utf-8"))

    assert data["total_actions"] == 61
    actions = data["actions"]
    register_ids = {item["action_id"] for item in actions}
    router_ids = set(ACTION_ROUTE_REGISTRY.keys())

    # 1. Exact 1-to-1 bijection on action_ids
    assert register_ids == router_ids, f"Diff: {register_ids ^ router_ids}"

    # 2. Every concrete trigger variant in ui-action-register.json matches its RouteSpec
    total_concrete_triggers_verified = 0
    for item in actions:
        aid = item["action_id"]
        spec = ACTION_ROUTE_REGISTRY[aid]
        samples = _sample_trigger_from_template(item["trigger"])
        for sample in samples:
            if not sample.startswith("/"):
                validate_callback_data(sample)
            matched = any(p.fullmatch(sample) is not None for p in spec.patterns)
            assert matched, f"Trigger sample '{sample}' for {aid} did not match {spec.patterns}"
            total_concrete_triggers_verified += 1

            # Also verify no earlier route in ROUTE_SPECS shadows this trigger under another action_id
            first_match_aid = next(
                s.action_id
                for s in ROUTE_SPECS
                if any(p.fullmatch(sample) is not None for p in s.patterns)
            )
            assert first_match_aid == aid, (
                f"Trigger '{sample}' routed to {first_match_aid} instead of {aid}"
            )

    assert total_concrete_triggers_verified >= 61
