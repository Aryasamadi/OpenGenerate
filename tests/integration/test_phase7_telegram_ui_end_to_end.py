"""Integration tests for Phase 7: End-to-End Telegram UI Router (`u:*`, `a:*`, `s:*`, `c:cancel`, commands, wizards)."""

from __future__ import annotations

import pytest

from opengenerate.domain.errors import AccessDenied
from opengenerate.domain.roles import Actor, Role
from opengenerate.infrastructure.crypto import FieldCipher
from opengenerate.infrastructure.db.billing_store import create_discount, create_plan
from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.db.identity_store import register_or_update_user
from opengenerate.interfaces.telegram.ergonomics import set_custom_system_text
from opengenerate.interfaces.telegram.router import TelegramUiRouter


@pytest.mark.asyncio
async def test_user_journey_start_lang_plans_discount_payment_referral_and_support(
    db: Database,
) -> None:
    super_id = 900001
    user_id = 100001
    friend_id = 100002
    super_actor = Actor(user_id=super_id, role=Role.SUPERADMIN)

    async with db.transaction() as conn:
        await register_or_update_user(conn, user_id=super_id, bootstrap_superadmins={super_id})
        free_plan = await create_plan(
            conn,
            actor=super_actor,
            name_fa="پلن رایگان آزمایشی",
            name_en="Free Trial",
            duration_days=3,
            daily_post_limit=5,
            max_sources=3,
            max_channels=1,
            daily_test_limit=5,
            is_free=True,
        )
        paid_plan = await create_plan(
            conn,
            actor=super_actor,
            name_fa="پلن حرفه‌ای",
            name_en="Pro Plan",
            duration_days=30,
            daily_post_limit=20,
            max_sources=10,
            max_channels=3,
            daily_test_limit=10,
            price_tmn=500_000,
            price_usd=2_000,
            is_free=False,
        )
        await create_discount(
            conn,
            actor=super_actor,
            code="WELCOME20",
            percent=20,
            max_uses=10,
        )

    router = TelegramUiRouter(db, bootstrap_superadmins={super_id})

    # 1. First-time /start renders language picker
    r1 = await router.dispatch(user_id=user_id, trigger="/start", username="ali")
    assert r1.action_id == "CMD_START"
    assert "u:lang:fa" in str(r1.reply_markup)

    # 2. Pick Persian language (`u:lang:fa`) -> renders localized Home
    r2 = await router.dispatch(user_id=user_id, trigger="u:lang:fa")
    assert r2.action_id == "CB_USER_LANG"
    assert "خانه" in r2.text

    # 3. Friend joins with referral link `/start ref_100001`
    await router.dispatch(user_id=friend_id, trigger=f"/start ref_{user_id}", username="reza")
    ref_view = await router.dispatch(user_id=user_id, trigger="u:ref")
    assert ref_view.action_id == "CB_USER_REFERRAL"
    assert "<code>1</code>" in ref_view.text

    # 4. Help & About commands
    assert (await router.dispatch(user_id=user_id, trigger="/help")).action_id == "CMD_HELP"
    assert (await router.dispatch(user_id=user_id, trigger="/about")).action_id == "CMD_ABOUT"
    assert (await router.dispatch(user_id=user_id, trigger="u:help")).action_id == "CB_USER_HELP"

    # 5. Claim one-time free plan (`u:free:<id>`) and check `u:freeused:<id>` alert
    cat = await router.dispatch(user_id=user_id, trigger="u:plans")
    assert cat.action_id == "CB_USER_PLANS"
    p_free_view = await router.dispatch(user_id=user_id, trigger=f"u:plan:{free_plan.plan_id}")
    assert f"u:free:{free_plan.plan_id}" in str(p_free_view.reply_markup)

    claimed = await router.dispatch(user_id=user_id, trigger=f"u:free:{free_plan.plan_id}")
    assert claimed.action_id == "CB_USER_FREE_CLAIM"

    used_alert = await router.dispatch(user_id=user_id, trigger=f"u:freeused:{free_plan.plan_id}")
    assert used_alert.action_id == "CB_USER_FREE_USED"
    assert used_alert.show_alert is True

    # 6. Apply discount code via guided wizard (`u:disc:<id>`) and submit receipt (`u:req:<id>`)
    disc_prompt = await router.dispatch(user_id=user_id, trigger=f"u:disc:{paid_plan.plan_id}")
    assert disc_prompt.action_id == "CB_USER_DISCOUNT_APPLY"
    after_disc = await router.dispatch_message_input(user_id=user_id, text_body="WELCOME20")
    assert after_disc.action_id == "CB_USER_DISCOUNT_APPLY"
    assert "400,000" in after_disc.text

    req_prompt = await router.dispatch(user_id=user_id, trigger=f"u:req:{paid_plan.plan_id}")
    assert req_prompt.action_id == "CB_USER_REQUEST_PAYMENT"
    after_req = await router.dispatch_message_input(
        user_id=user_id, text_body="رسید واریز بانکی شماره ۹۹۸۸۷۷۶۶"
    )
    assert after_req.action_id == "CB_USER_REQUEST_PAYMENT"
    assert "#1" in after_req.text

    # Pending payment check (`u:pending:<id>`)
    pend = await router.dispatch(user_id=user_id, trigger=f"u:pending:{paid_plan.plan_id}")
    assert pend.action_id == "CB_USER_PAYMENT_PENDING"
    assert pend.show_alert is True

    # 7. Support ticket open (`u:sup`), message input, and close (`u:sclose`)
    sup_open = await router.dispatch(user_id=user_id, trigger="u:sup")
    assert sup_open.action_id == "CB_USER_SUPPORT"
    sup_sent = await router.dispatch_message_input(user_id=user_id, text_body="سلام، سوالی داشتم.")
    assert sup_sent.action_id == "CB_USER_SUPPORT"
    sup_closed = await router.dispatch(user_id=user_id, trigger="u:sclose")
    assert sup_closed.action_id == "CB_USER_SUPPORT"


@pytest.mark.asyncio
async def test_channel_admin_journey_channels_sources_editorial_scheduling_and_two_step_delete(
    db: Database,
    fernet_key: str,
) -> None:
    super_id = 900010
    admin_id = 200010
    super_actor = Actor(user_id=super_id, role=Role.SUPERADMIN)
    keyring = FieldCipher([fernet_key])

    async with db.transaction() as conn:
        await register_or_update_user(conn, user_id=super_id, bootstrap_superadmins={super_id})
        free_plan = await create_plan(
            conn,
            actor=super_actor,
            name_fa="پلن پایه",
            name_en="Starter",
            duration_days=7,
            daily_post_limit=10,
            max_sources=5,
            max_channels=2,
            daily_test_limit=5,
            is_free=True,
        )

    router = TelegramUiRouter(db, bootstrap_superadmins={super_id}, keyring=keyring)
    await router.dispatch(user_id=admin_id, trigger="/start")
    await router.dispatch(user_id=admin_id, trigger="u:lang:fa")
    await router.dispatch(user_id=admin_id, trigger=f"u:free:{free_plan.plan_id}")

    # 1. Register channel via guided wizard (`a:chadd`)
    add_prompt = await router.dispatch(user_id=admin_id, trigger="a:chadd")
    assert add_prompt.action_id == "CB_ADMIN_CHANNEL_ADD"
    ch_dash = await router.dispatch_message_input(user_id=admin_id, text_body="-1005555555555")
    assert ch_dash.action_id == "CB_ADMIN_CHANNEL_ADD"
    cid = -1005555555555

    # 2. Add source via guided wizard (`a:srca:<cid>`)
    await router.dispatch(user_id=admin_id, trigger=f"a:srca:{cid}")
    src_view = await router.dispatch_message_input(
        user_id=admin_id, text_body="https://example.com/feed.xml"
    )
    assert src_view.action_id == "CB_ADMIN_SOURCE_ADD"

    # Extract source_id from callback buttons
    callbacks = [
        btn["callback_data"]
        for row in src_view.reply_markup["inline_keyboard"]
        for btn in row
    ]
    srct_cb = next(c for c in callbacks if c.startswith("a:srct:"))
    sid = int(srct_cb.split(":")[2])

    # 3. Source toggles, recheck, custom API, and two-step delete
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:src:{cid}")).action_id == "CB_ADMIN_SOURCES_LIST"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:srcv:{sid}")).action_id == "CB_ADMIN_SOURCE_INSPECT"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:srcr:{sid}")).action_id == "CB_ADMIN_SOURCE_RECHECK"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:srct:{sid}")).action_id == "CB_ADMIN_SOURCE_TOGGLE_FETCH"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:srctb:{sid}")).action_id == "CB_ADMIN_SOURCE_TOGGLE_PUBLISH"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:srcapi:{sid}")).action_id == "CB_ADMIN_SOURCE_CUSTOM_API"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:srcapix:{sid}")).action_id == "CB_ADMIN_SOURCE_CUSTOM_API_CLEAR"

    # Toggle channel automation (`a:tog:<cid>:enabled`), test (`a:test:<cid>`), report (`a:rep:<cid>`)
    # Re-enable fetch first so channel has an active source
    await router.dispatch(user_id=admin_id, trigger=f"a:srct:{sid}")
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:tog:{cid}:enabled")).action_id == "CB_ADMIN_CHANNEL_AUTOMATION_TOGGLE"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:test:{cid}")).action_id == "CB_ADMIN_CHANNEL_TEST"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:rep:{cid}")).action_id == "CB_ADMIN_CHANNEL_REPORT"

    # 4. Editorial settings, categories, criteria, scheduling, queue, personal AI
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:con:{cid}")).action_id == "CB_ADMIN_CONTENT_MENU"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:set:{cid}:hashtags")).action_id == "CB_ADMIN_CONTENT_SET_FIELD"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:cat:{cid}")).action_id == "CB_ADMIN_CATEGORIES"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:cri:{cid}")).action_id == "CB_ADMIN_CRITERIA"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:sch:{cid}")).action_id == "CB_ADMIN_SCHEDULING"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:tzs:{cid}:Asia/Tehran")).action_id == "CB_ADMIN_SCHEDULING"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:qoff:{cid}")).action_id == "CB_ADMIN_SCHEDULING"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:que:{cid}")).action_id == "CB_ADMIN_QUEUE_AND_ARTICLES"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:qreset:{cid}")).action_id == "CB_ADMIN_QUEUE_AND_ARTICLES"
    assert (await router.dispatch(user_id=admin_id, trigger=f"a:myai:{cid}")).action_id == "CB_ADMIN_PERSONAL_AI"

    # 5. Two-step source delete (`a:srcd:<sid>` -> `a:srcd:<sid>:confirm`)
    s_del_warn = await router.dispatch(user_id=admin_id, trigger=f"a:srcd:{sid}")
    assert s_del_warn.action_id == "CB_ADMIN_SOURCE_DELETE"
    assert f"a:srcd:{sid}:confirm" in str(s_del_warn.reply_markup)
    s_del_ok = await router.dispatch(user_id=admin_id, trigger=f"a:srcd:{sid}:confirm")
    assert s_del_ok.action_id == "CB_ADMIN_SOURCE_DELETE"

    # 6. Two-step channel delete (`a:chdel:<cid>` -> `a:chdel:<cid>:confirm`)
    c_del_warn = await router.dispatch(user_id=admin_id, trigger=f"a:chdel:{cid}")
    assert c_del_warn.action_id == "CB_ADMIN_CHANNEL_DELETE"
    assert f"a:chdel:{cid}:confirm" in str(c_del_warn.reply_markup)
    c_del_ok = await router.dispatch(user_id=admin_id, trigger=f"a:chdel:{cid}:confirm")
    assert c_del_ok.action_id == "CB_ADMIN_CHANNEL_DELETE"


@pytest.mark.asyncio
async def test_superadmin_console_rbac_and_all_s_actions(db: Database) -> None:
    super_id = 900020
    normal_id = 300020
    router = TelegramUiRouter(db, bootstrap_superadmins={super_id})

    await router.dispatch(user_id=normal_id, trigger="/start")
    await router.dispatch(user_id=super_id, trigger="/start")

    # 1. Non-superadmin blocked from every `s:*` trigger
    with pytest.raises(AccessDenied):
        await router.dispatch(user_id=normal_id, trigger="s:report")

    # 2. Superadmin executes all `s:*` console views & two-step global actions
    assert (await router.dispatch(user_id=super_id, trigger="s:report")).action_id == "CB_SUPER_REPORT"
    assert (await router.dispatch(user_id=super_id, trigger="s:health")).action_id == "CB_SUPER_HEALTH"
    assert (await router.dispatch(user_id=super_id, trigger="s:plans")).action_id == "CB_SUPER_PLANS"
    assert (await router.dispatch(user_id=super_id, trigger="s:discs")).action_id == "CB_SUPER_DISCOUNTS"
    assert (await router.dispatch(user_id=super_id, trigger="s:users:1")).action_id == "CB_SUPER_USERS_AND_ADMINS"
    assert (await router.dispatch(user_id=super_id, trigger="s:models")).action_id == "CB_SUPER_MODELS"
    assert (await router.dispatch(user_id=super_id, trigger="s:pays")).action_id == "CB_SUPER_PAYMENTS"
    assert (await router.dispatch(user_id=super_id, trigger="s:mand")).action_id == "CB_SUPER_MANDATORY_CHANNEL"
    assert (await router.dispatch(user_id=super_id, trigger="s:mand_t:fa")).action_id == "CB_SUPER_MANDATORY_CHANNEL"

    # Custom system text & reset (`s:texts`, `s:txtr:welcome:fa`)
    async with db.transaction() as conn:
        await set_custom_system_text(
            conn,
            actor=Actor(user_id=super_id, role=Role.SUPERADMIN),
            key="welcome",
            lang="fa",
            new_text="<b>خوش آمدید سفارشی</b>",
        )
    assert (await router.dispatch(user_id=super_id, trigger="s:texts")).action_id == "CB_SUPER_TEXTS"
    assert (await router.dispatch(user_id=super_id, trigger="s:txtr:welcome:fa")).action_id == "CB_SUPER_TEXTS"

    # Two-step broadcast (`s:bc` -> `s:bc:confirm`)
    bc_warn = await router.dispatch(user_id=super_id, trigger="s:bc")
    assert "s:bc:confirm" in str(bc_warn.reply_markup)
    bc_ok = await router.dispatch(user_id=super_id, trigger="s:bc:confirm")
    assert bc_ok.action_id == "CB_SUPER_BROADCAST"

    # Two-step global automation switch (`s:auto` -> `s:auto:confirm`)
    auto_warn = await router.dispatch(user_id=super_id, trigger="s:auto")
    assert "s:auto:confirm" in str(auto_warn.reply_markup)
    auto_ok = await router.dispatch(user_id=super_id, trigger="s:auto:confirm")
    assert auto_ok.action_id == "CB_SUPER_GLOBAL_AUTOMATION"

    # Referral economics (`s:ref`)
    assert (await router.dispatch(user_id=super_id, trigger="s:ref")).action_id == "CB_SUPER_REFERRAL_ECONOMICS"
