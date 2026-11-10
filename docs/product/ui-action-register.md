# Exhaustive UI Button, Callback & Action Register (`Stage A`)

This register documents **every command, inline button, callback family, guided input continuation, authorization boundary, database side effect, and automated test** across the Subscriber (`u:*`), Channel Admin (`a:*`), Superadmin (`s:*`), and Shared (`c:*`) interfaces.

> **Machine-Readable Companion:** [`docs/product/ui-action-register.json`](./ui-action-register.json) is maintained in lockstep with this document and is enforced by the automated callback-coverage test suite.

## Summary Matrix

| # | Action ID | Trigger / Callback | Label (FA) | Label (EN) | Roles | Destructive? | Automated Test |
|---|---|---|---|---|---|---|---|
| 1 | `CMD_START` | `/start [ref_<user_id> | dl_<key>]` | شروع / صفحه اصلی | Start / Home | `user`, `admin`, `superadmin` | No | `tests/ui/test_commands.py::test_start_command_and_deeplinks` |
| 2 | `CMD_LANG` | `/lang` | تغییر زبان (🇮🇷 فارسی / 🇬🇧 English) | Change Language (🇮🇷 Persian / 🇬🇧 English) | `user`, `admin`, `superadmin` | No | `tests/ui/test_commands.py::test_lang_command` |
| 3 | `CMD_HELP` | `/help` | راهنما | Help | `user`, `admin`, `superadmin` | No | `tests/ui/test_commands.py::test_help_command` |
| 4 | `CMD_ABOUT` | `/about` | درباره ربات | About Bot | `user`, `admin`, `superadmin` | No | `tests/ui/test_commands.py::test_about_command` |
| 5 | `CMD_REF` | `/ref` | دعوت از دوستان (رفرال) | Referral / Invite Friends | `user`, `admin`, `superadmin` | No | `tests/ui/test_commands.py::test_ref_command` |
| 6 | `CMD_MAN` | `/man` | پنل مدیریت کانالها | Channel Management Panel | `user`, `admin`, `superadmin` | No | `tests/ui/test_commands.py::test_man_command` |
| 7 | `CMD_CANCEL` | `/cancel` | لغو عملیات جاری | Cancel Current Operation | `user`, `admin`, `superadmin` | No | `tests/ui/test_commands.py::test_cancel_command` |
| 8 | `CMD_CREATE` | `/create` | ثبت کانال جدید | Register New Channel | `admin`, `superadmin`, `user` | No | `tests/ui/test_commands.py::test_create_command` |
| 9 | `CB_USER_HOME` | `u:home` | 🏠 خانه | 🏠 Home | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_home` |
| 10 | `CB_USER_PLANS` | `u:plans` | 💎 اشتراکها و پلنها | 💎 Plans & Subscriptions | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_plans` |
| 11 | `CB_USER_PLAN_DETAIL` | `u:plan:<plan_id>` | 📋 مشاهده جزئیات پلن | 📋 Inspect Plan Details | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_plan_detail` |
| 12 | `CB_USER_FREE_CLAIM` | `u:free:<plan_id>` | 🎁 فعالسازی پلن رایگان | 🎁 Claim Free Plan | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_free_claim_once` |
| 13 | `CB_USER_FREE_USED` | `u:freeused:<plan_id>` | 🚫 پلن رایگان قبلاً استفاده شده | 🚫 Free Plan Already Used | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_freeused_alert` |
| 14 | `CB_USER_PLAN_LOCKED` | `u:locked:<plan_id>` | 🔒 غیرقابل انتخاب (تنزل پلن مجاز نیست) | 🔒 Locked (Downgrade Not Allowed) | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_locked_downgrade` |
| 15 | `CB_USER_PAYMENT_PENDING` | `u:pending:<plan_id>` | ⏳ درخواست پرداخت در انتظار بررسی | ⏳ Payment Request Pending Review | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_pending_payment` |
| 16 | `CB_USER_DISCOUNT_APPLY` | `u:disc:<plan_id>` | 🏷 ثبت کد تخفیف | 🏷 Apply Discount Code | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_disc_apply_and_remove` |
| 17 | `CB_USER_DISCOUNT_REMOVE` | `u:discx:<plan_id>` | ❌ حذف کد تخفیف | ❌ Remove Discount Code | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_disc_apply_and_remove` |
| 18 | `CB_USER_REQUEST_PAYMENT` | `u:req:<plan_id>` | 💳 ارسال رسید پرداخت / خرید پلن | 💳 Submit Payment Receipt / Buy Plan | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_req_receipt_submission` |
| 19 | `CB_USER_REFERRAL` | `u:ref` | 🎁 دعوت از دوستان و کسب درآمد | 🎁 Invite Friends & Earn Rewards | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_ref` |
| 20 | `CB_USER_SUPPORT` | `u:sup | u:support | u:sclose` | 💬 پشتیبانی | 💬 Support | `user`, `admin`, `superadmin` | No | `tests/ui/test_support_flow.py::test_user_support_open_forward_reply_close` |
| 21 | `CB_USER_MANAGE_PANEL` | `u:man` | ⚙️ مدیریت کانالها | ⚙️ Manage Channels | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_man` |
| 22 | `CB_USER_HELP` | `u:help` | 📚 راهنما | 📚 Help | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_help` |
| 23 | `CB_USER_LANG` | `u:lang | u:lang:fa | u:lang:en` | 🌐 تغییر زبان / Language | 🌐 Language / تغییر زبان | `user`, `admin`, `superadmin` | No | `tests/ui/test_user_callbacks.py::test_u_lang_switch_preserves_custom_channel_settings` |
| 24 | `CB_MANDATORY_MEMBERSHIP_CHECK` | `a:mand` | ✅ عضو شدم / بررسی عضویت | ✅ I Joined / Verify Membership | `user`, `admin`, `superadmin` | No | `tests/ui/test_membership_gate.py::test_a_mand_verification_and_transient_error` |
| 25 | `CB_SHARED_CANCEL` | `c:cancel` | 🔙 انصراف و بازگشت | 🔙 Cancel & Back | `user`, `admin`, `superadmin` | No | `tests/ui/test_shared_callbacks.py::test_c_cancel` |
| 26 | `CB_ADMIN_HOME` | `a:home` | 🎛 پنل مدیریت کانالها | 🎛 Channel Admin Home | `user`, `admin`, `superadmin` | No | `tests/ui/test_admin_callbacks.py::test_a_home` |
| 27 | `CB_ADMIN_MY_PLAN` | `a:plan` | 💳 پلن من و تمدید/ارتقاء | 💳 My Plan & Renew/Upgrade | `user`, `admin`, `superadmin` | No | `tests/ui/test_admin_callbacks.py::test_a_plan` |
| 28 | `CB_ADMIN_CHANNEL_ADD` | `a:chadd` | ➕ افزودن کانال جدید | ➕ Add New Channel | `admin`, `superadmin`, `user` | No | `tests/ui/test_channel_registration.py::test_a_chadd_and_ownership_transfer` |
| 29 | `CB_ADMIN_CHANNEL_HOME` | `a:ch:<channel_id>` | 📢 مدیریت کانال | 📢 Channel Dashboard | `admin`, `superadmin` | No | `tests/ui/test_admin_callbacks.py::test_a_ch_dashboard_authz` |
| 30 | `CB_ADMIN_CHANNEL_TEST` | `a:test:<channel_id>` | 🧪 تست دستی چرخه کانال | 🧪 Run Manual Test Cycle | `admin`, `superadmin` | No | `tests/ui/test_admin_callbacks.py::test_a_test_manual_cycle_and_quota` |
| 31 | `CB_ADMIN_CHANNEL_REPORT` | `a:rep:<channel_id>` | 📊 گزارش عملکرد کانال | 📊 Channel Activity Report | `admin`, `superadmin` | No | `tests/ui/test_admin_callbacks.py::test_a_rep_channel_report` |
| 32 | `CB_ADMIN_CHANNEL_AUTOMATION_TOGGLE` | `a:tog:<channel_id>:enabled` | ⚡ روشن/خاموش کردن اتوماسیون کانال | ⚡ Toggle Channel Automation | `admin`, `superadmin` | No | `tests/ui/test_admin_callbacks.py::test_a_tog_channel_automation` |
| 33 | `CB_ADMIN_CHANNEL_DELETE` | `a:chdel:<channel_id> | a:chdel:<channel_id>:confirm` | 🗑 حذف کانال | 🗑 Delete Channel | `admin`, `superadmin` | Yes (Confirmed) | `tests/ui/test_admin_callbacks.py::test_a_chdel_confirmation_and_cleanup` |
| 34 | `CB_ADMIN_SOURCES_LIST` | `a:src:<channel_id>[:<page>]` | 🌐 مدیریت منابع محتوا | 🌐 Manage Content Sources | `admin`, `superadmin` | No | `tests/ui/test_sources_callbacks.py::test_a_src_list_and_pagination` |
| 35 | `CB_ADMIN_SOURCE_ADD` | `a:srca:<channel_id>` | ➕ افزودن منبع جدید (URL) | ➕ Add New Source (URL) | `admin`, `superadmin` | No | `tests/ui/test_sources_callbacks.py::test_a_srca_add_and_ssrf_block` |
| 36 | `CB_ADMIN_SOURCE_INSPECT` | `a:srcv:<source_id>` | 🔍 جزئیات و وضعیت منبع | 🔍 Inspect Source Details | `admin`, `superadmin` | No | `tests/ui/test_sources_callbacks.py::test_a_srcv_inspect` |
| 37 | `CB_ADMIN_SOURCE_RECHECK` | `a:srcr:<source_id>` | 🔄 بررسی مجدد و عیبیابی منبع | 🔄 Recheck Source & Diagnostics | `admin`, `superadmin` | No | `tests/ui/test_sources_callbacks.py::test_a_srcr_diagnostics` |
| 38 | `CB_ADMIN_SOURCE_TOGGLE_FETCH` | `a:srct:<source_id>` | 📡 روشن/خاموش کردن دریافت (Fetch) | 📡 Toggle Source Fetching | `admin`, `superadmin` | No | `tests/ui/test_sources_callbacks.py::test_a_srct_and_srctb_toggles` |
| 39 | `CB_ADMIN_SOURCE_TOGGLE_PUBLISH` | `a:srctb:<source_id>` | 📤 روشن/خاموش کردن استفاده برای انتشار | 📤 Toggle Source Publication Use | `admin`, `superadmin` | No | `tests/ui/test_sources_callbacks.py::test_a_srct_and_srctb_toggles` |
| 40 | `CB_ADMIN_SOURCE_CUSTOM_API` | `a:srcapi:<source_id>` | 🔑 تنظیم API اختصاصی و کلید منبع | 🔑 Configure Custom API & Key | `admin`, `superadmin` | No | `tests/ui/test_sources_callbacks.py::test_a_srcapi_encryption_and_clear` |
| 41 | `CB_ADMIN_SOURCE_CUSTOM_API_CLEAR` | `a:srcapix:<source_id>` | 🧹 پاک کردن تنظیمات API اختصاصی | 🧹 Clear Custom API Settings | `admin`, `superadmin` | Yes (Confirmed) | `tests/ui/test_sources_callbacks.py::test_a_srcapi_encryption_and_clear` |
| 42 | `CB_ADMIN_SOURCE_DELETE` | `a:srcd:<source_id>[:confirm]` | 🗑 حذف منبع | 🗑 Delete Source | `admin`, `superadmin` | Yes (Confirmed) | `tests/ui/test_sources_callbacks.py::test_a_srcd_delete` |
| 43 | `CB_ADMIN_CONTENT_MENU` | `a:con:<channel_id>` | ✍️ تنظیمات محتوا و تحریریه | ✍️ Editorial & Content Settings | `admin`, `superadmin` | No | `tests/ui/test_content_settings.py::test_a_con_and_set_fields` |
| 44 | `CB_ADMIN_CONTENT_SET_FIELD` | `a:set:<channel_id>:<field>` | ⚙️ ویرایش فیلد تنظیمات محتوا | ⚙️ Edit Content Setting Field | `admin`, `superadmin` | No | `tests/ui/test_content_settings.py::test_a_con_and_set_fields` |
| 45 | `CB_ADMIN_CATEGORIES` | `a:cat:<ch_id>[:<p>] | a:catv:<ch_id>:<idx> | a:cata:<ch_id> | a:cats:<ch_id>:<idx> | a:catd:<ch_id>:<idx>` | 🗂 مدیریت دستهبندیها (افزودن/مشاهده/ویرایش/حذف) | 🗂 Manage Categories (List/Inspect/Add/Style/Delete) | `admin`, `superadmin` | Yes (Confirmed) | `tests/ui/test_content_settings.py::test_categories_crud_and_pagination` |
| 46 | `CB_ADMIN_CRITERIA` | `a:cri:<ch_id>[:<p>] | a:criv:<ch_id>:<idx> | a:cria:<ch_id> | a:criw:<ch_id>:<idx> | a:crid:<ch_id>:<idx>` | ⚖️ مدیریت معیارهای ارزیابی (افزودن/مشاهده/وزن/حذف) | ⚖️ Manage Evaluation Criteria (List/Inspect/Add/Weight/Delete) | `admin`, `superadmin` | Yes (Confirmed) | `tests/ui/test_content_settings.py::test_criteria_crud_and_pagination` |
| 47 | `CB_ADMIN_SCHEDULING` | `a:sch:<ch_id> | a:quiet:<ch_id> | a:qs:<ch_id>:<h> | a:qoff:<ch_id> | a:tz:<ch_id> | a:tzs:<ch_id>:<tz> | a:mode:<ch_id>` | ⏰ زمانبندی، ساعات خاموشی، منطقه زمانی و حالت انتشار | ⏰ Scheduling, Quiet Hours, Timezone & Publish Mode | `admin`, `superadmin` | No | `tests/ui/test_scheduling_callbacks.py::test_scheduling_quiet_hours_and_dst_timezone` |
| 48 | `CB_ADMIN_QUEUE_AND_ARTICLES` | `a:que:<ch_id>[:<sort>:<p>] | a:rej:<ch_id>[:<p>] | a:art:<id> | a:arte:<id> | a:artf:<id> | a:pub:<id> | a:artr:<id> | a:artd:<id> | a:qreset:<ch_id>` | 📥 صف انتشار، مقالات ردشده، بازبینی، ویرایش و انتشار دستی | 📥 Ready Queue, Rejected Items, Inspect, Edit, Publish & Reset | `admin`, `superadmin` | Yes (Confirmed) | `tests/ui/test_queue_and_manual_publish.py::test_queue_review_edit_publish_restore_reset` |
| 49 | `CB_ADMIN_PERSONAL_AI` | `a:myai:<ch_id> | a:myai_add:<ch_id> | a:myai_v:<m_id> | a:myai_t:<m_id> | a:myai_o:<m_id> | a:myai_e:<m_id> | a:myai_d:<m_id>` | 🤖 مدیریت مدلهای هوش مصنوعی شخصی | 🤖 Manage Personal AI Models | `admin`, `superadmin` | Yes (Confirmed) | `tests/ui/test_personal_ai_callbacks.py::test_myai_crud_probe_and_isolation` |
| 50 | `CB_SUPER_REPORT` | `s:report` | 📈 گزارش جامع پلتفرم | 📈 Platform Overview Report | `superadmin` | No | `tests/ui/test_superadmin_callbacks.py::test_s_report_and_rbac` |
| 51 | `CB_SUPER_HEALTH` | `s:health` | 🩺 وضعیت سلامت سیستم (Health) | 🩺 System Health Dashboard | `superadmin` | No | `tests/ui/test_superadmin_callbacks.py::test_s_health_metrics_and_states` |
| 52 | `CB_SUPER_PLANS` | `s:plans | s:plan:<id> | s:plan_new | s:plan_e:<id>:<field> | s:plan_t:<id> | s:plan_free:<id> | s:plan_d:<id>` | 💎 مدیریت پلنها و تعرفهها | 💎 Manage Plans & Tariffs | `superadmin` | Yes (Confirmed) | `tests/ui/test_superadmin_callbacks.py::test_s_plans_crud_and_safe_delete` |
| 53 | `CB_SUPER_DISCOUNTS` | `s:discs | s:disc:<code> | s:disc_new | s:disc_e:<code>:<field> | s:disc_t:<code> | s:disc_d:<code>` | 🏷 مدیریت کدهای تخفیف | 🏷 Manage Discount Codes | `superadmin` | Yes (Confirmed) | `tests/ui/test_superadmin_callbacks.py::test_s_discounts_crud` |
| 54 | `CB_SUPER_USERS_AND_ADMINS` | `s:users:<p> | s:admins:<p> | s:user:<uid>:<p> | s:uplan:<uid> | s:urevoke:<uid> | s:uban:<uid> | s:umsg:<uid> | s:ureport:<uid> | s:ufree:<uid> | s:utests:<uid> | s:uposts:<uid>` | 👥 مدیریت کاربران و ادمینها | 👥 Manage Users & Channel Admins | `superadmin` | Yes (Confirmed) | `tests/ui/test_superadmin_callbacks.py::test_s_users_management_and_superadmin_protection` |
| 55 | `CB_SUPER_MODELS` | `s:models | s:model:<id> | s:model_add | s:models_test | s:model_test:<id> | s:model_t:<id> | s:model_e:<id> | s:model_d:<id> | s:model_test_pick:<id>` | 🧠 مدیریت مدلهای هوش مصنوعی سراسری | 🧠 Manage Global AI Models | `superadmin` | Yes (Confirmed) | `tests/ui/test_superadmin_callbacks.py::test_s_models_crud_and_probes` |
| 56 | `CB_SUPER_PAYMENTS` | `s:pays | s:pay_rc:<id> | s:pay_ok:<id> | s:pay_no:<id> | s:pay_refund:<id>` | 💰 مدیریت پرداختها، تایید، رد و استرداد وجه (Refund) | 💰 Manage Payments, Approve, Reject & Refund | `superadmin` | Yes (Confirmed) | `tests/money/test_payment_approval_and_refund_concurrency.py::test_atomic_approval_and_refund` |
| 57 | `CB_SUPER_MANDATORY_CHANNEL` | `s:mand | s:mand_t:<lang> | s:mand_e:<lang>:id | s:mand_e:<lang>:user` | 🔐 تنظیمات عضویت اجباری (فارسی و انگلیسی) | 🔐 Mandatory Membership Configuration (FA & EN) | `superadmin` | No | `tests/ui/test_superadmin_callbacks.py::test_s_mand_configuration` |
| 58 | `CB_SUPER_TEXTS` | `s:texts | s:txt:<key>:<lang> | s:txtr:<key>:<lang>` | 📝 ویرایشگر متون سیستم و بومیسازی | 📝 System Text & Localization Editor | `superadmin` | No | `tests/ui/test_superadmin_callbacks.py::test_s_texts_edit_html_validation_and_reset` |
| 59 | `CB_SUPER_BROADCAST` | `s:bc | s:bc:confirm` | 📣 ارسال پیام همگانی (Broadcast) | 📣 Broadcast Messaging | `superadmin` | Yes (Confirmed) | `tests/ui/test_superadmin_callbacks.py::test_s_bc_preview_confirm_and_nonblocking_rate_limit` |
| 60 | `CB_SUPER_GLOBAL_AUTOMATION` | `s:auto | s:auto:confirm` | 🛑 کلید اتوماسیون سراسری پلتفرم | 🛑 Global Platform Automation Switch | `superadmin` | Yes (Confirmed) | `tests/ui/test_superadmin_callbacks.py::test_s_auto_distinct_from_channel_toggle` |
| 61 | `CB_SUPER_REFERRAL_ECONOMICS` | `s:ref | s:ref:pct | s:ref:cap_tmn | s:ref:cap_usd` | 🤝 تنظیمات اقتصادی رفرال (درصد و سقف پاداش) | 🤝 Referral Economics Configuration | `superadmin` | No | `tests/ui/test_superadmin_callbacks.py::test_s_ref_bounds_and_currency_caps` |

---

## Detailed 14-Point Specification Per Action (Section 9.4)

### 1. `CMD_START` — Start / Home / شروع / صفحه اصلی

1. **Stable Action ID:** `CMD_START`
2. **Visible Labels:** **FA:** `شروع / صفحه اصلی` | **EN:** `Start / Home`
3. **Callback / Trigger Pattern:** `/start [ref_<user_id> | dl_<key>]`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Public entrypoint; referral attribution only on first qualifying /start; deep-link key must exist and be active.
6. **Parent Screen → Destination Screen:** `Telegram Chat Entry / Deep Link` → `Language Selection (first-time) | User Home | Full Article View (dl_<key>)`
7. **Required Input & Validation:** Optional payload max 64 chars matching ^(ref_\d+|dl_[A-Za-z0-9_-]+)$.
8. **Domain Operation Invoked:** `onboarding.handle_start_command`
9. **Database Entities (Read / Write):** **Read:** `users, referrals, deeplinks, articles, settings` | **Write:** `users, referrals, deeplinks`
10. **External Side Effects:** Telegram sendMessage / sendRichMessage (for deep-link full article).
11. **Success Feedback & Next Screen:** Renders language picker for new user, full rich article for dl_<key>, or role-appropriate Home menu.
12. **Error / Empty / Busy / Expired Behavior:** Invalid/expired deep-link shows localized notice and falls back to Home; self-referral ignored safely.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_commands.py::test_start_command_and_deeplinks`

### 2. `CMD_LANG` — Change Language (🇮🇷 Persian / 🇬🇧 English) / تغییر زبان (🇮🇷 فارسی / 🇬🇧 English)

1. **Stable Action ID:** `CMD_LANG`
2. **Visible Labels:** **FA:** `تغییر زبان (🇮🇷 فارسی / 🇬🇧 English)` | **EN:** `Change Language (🇮🇷 Persian / 🇬🇧 English)`
3. **Callback / Trigger Pattern:** `/lang`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated bot user.
6. **Parent Screen → Destination Screen:** `Any Screen` → `Language Selection Screen`
7. **Required Input & Validation:** None (command) -> followed by callback u:lang:fa or u:lang:en.
8. **Domain Operation Invoked:** `identity.open_language_selector`
9. **Database Entities (Read / Write):** **Read:** `users` | **Write:** `None`
10. **External Side Effects:** Telegram sendMessage with inline language buttons.
11. **Success Feedback & Next Screen:** Displays Persian and English selection buttons.
12. **Error / Empty / Busy / Expired Behavior:** Rate-limited if spammed.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_commands.py::test_lang_command`

### 3. `CMD_HELP` — Help / راهنما

1. **Stable Action ID:** `CMD_HELP`
2. **Visible Labels:** **FA:** `راهنما` | **EN:** `Help`
3. **Callback / Trigger Pattern:** `/help`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated bot user.
6. **Parent Screen → Destination Screen:** `Any Screen` → `Help Screen`
7. **Required Input & Validation:** None.
8. **Domain Operation Invoked:** `i18n.get_help_screen`
9. **Database Entities (Read / Write):** **Read:** `users, settings` | **Write:** `None`
10. **External Side Effects:** Telegram sendMessage.
11. **Success Feedback & Next Screen:** Shows localized platform guide and navigation back to Home.
12. **Error / Empty / Busy / Expired Behavior:** Falls back to built-in localized help text if custom override is absent.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_commands.py::test_help_command`

### 4. `CMD_ABOUT` — About Bot / درباره ربات

1. **Stable Action ID:** `CMD_ABOUT`
2. **Visible Labels:** **FA:** `درباره ربات` | **EN:** `About Bot`
3. **Callback / Trigger Pattern:** `/about`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated bot user.
6. **Parent Screen → Destination Screen:** `Any Screen` → `About Screen`
7. **Required Input & Validation:** None.
8. **Domain Operation Invoked:** `i18n.get_about_screen`
9. **Database Entities (Read / Write):** **Read:** `users, settings` | **Write:** `None`
10. **External Side Effects:** Telegram sendMessage.
11. **Success Feedback & Next Screen:** Displays platform overview and version info.
12. **Error / Empty / Busy / Expired Behavior:** Standard fallback to built-in localized string.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_commands.py::test_about_command`

### 5. `CMD_REF` — Referral / Invite Friends / دعوت از دوستان (رفرال)

1. **Stable Action ID:** `CMD_REF`
2. **Visible Labels:** **FA:** `دعوت از دوستان (رفرال)` | **EN:** `Referral / Invite Friends`
3. **Callback / Trigger Pattern:** `/ref`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated bot user; mandatory membership check if enabled.
6. **Parent Screen → Destination Screen:** `Any Screen` → `Referral Dashboard`
7. **Required Input & Validation:** None.
8. **Domain Operation Invoked:** `referrals.get_user_referral_dashboard`
9. **Database Entities (Read / Write):** **Read:** `users, referrals, wallet, settings` | **Write:** `None`
10. **External Side Effects:** Telegram sendMessage.
11. **Success Feedback & Next Screen:** Shows personal ref_<user_id> link, invited count, reward %, and earned TMN/USD rewards.
12. **Error / Empty / Busy / Expired Behavior:** If mandatory membership not satisfied, redirects to Mandatory Channel Gate.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_commands.py::test_ref_command`

### 6. `CMD_MAN` — Channel Management Panel / پنل مدیریت کانالها

1. **Stable Action ID:** `CMD_MAN`
2. **Visible Labels:** **FA:** `پنل مدیریت کانالها` | **EN:** `Channel Management Panel`
3. **Callback / Trigger Pattern:** `/man`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user with active plan or existing channels/superadmin.
6. **Parent Screen → Destination Screen:** `Any Screen` → `Admin Home Screen`
7. **Required Input & Validation:** None.
8. **Domain Operation Invoked:** `channels.open_admin_home`
9. **Database Entities (Read / Write):** **Read:** `users, plans, channels, sources, usage` | **Write:** `None`
10. **External Side Effects:** Telegram sendMessage.
11. **Success Feedback & Next Screen:** Opens Channel Admin Home with usage, plan status, and owned channels.
12. **Error / Empty / Busy / Expired Behavior:** If user has no active plan and no channels, prompts to view/claim a plan.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_commands.py::test_man_command`

### 7. `CMD_CANCEL` — Cancel Current Operation / لغو عملیات جاری

1. **Stable Action ID:** `CMD_CANCEL`
2. **Visible Labels:** **FA:** `لغو عملیات جاری` | **EN:** `Cancel Current Operation`
3. **Callback / Trigger Pattern:** `/cancel`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated bot user.
6. **Parent Screen → Destination Screen:** `Any Guided Input / Stateful Prompt` → `Previous Contextual Screen or Home`
7. **Required Input & Validation:** None.
8. **Domain Operation Invoked:** `session.cancel_guided_input`
9. **Database Entities (Read / Write):** **Read:** `users, kv_cache` | **Write:** `kv_cache`
10. **External Side Effects:** Telegram sendMessage.
11. **Success Feedback & Next Screen:** Clears active conversation state and returns user to safe menu.
12. **Error / Empty / Busy / Expired Behavior:** If no active operation exists, informs user cleanly.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_commands.py::test_cancel_command`

### 8. `CMD_CREATE` — Register New Channel / ثبت کانال جدید

1. **Stable Action ID:** `CMD_CREATE`
2. **Visible Labels:** **FA:** `ثبت کانال جدید` | **EN:** `Register New Channel`
3. **Callback / Trigger Pattern:** `/create`
4. **Roles Allowed:** `admin, superadmin, user`
5. **Ownership & Entitlement Requirements:** Requires active plan with remaining channel quota (or superadmin) + mandatory membership.
6. **Parent Screen → Destination Screen:** `Any Screen` → `Channel Registration Guided Input`
7. **Required Input & Validation:** Prompts for forwarded channel post, @channel_username, or negative numeric channel ID.
8. **Domain Operation Invoked:** `channels.start_registration_flow`
9. **Database Entities (Read / Write):** **Read:** `users, plans, channels` | **Write:** `kv_cache`
10. **External Side Effects:** Telegram sendMessage.
11. **Success Feedback & Next Screen:** Enters guided channel identification step.
12. **Error / Empty / Busy / Expired Behavior:** Quota exceeded or no active plan shows upgrade/plan prompt.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_commands.py::test_create_command`

### 9. `CB_USER_HOME` — 🏠 Home / 🏠 خانه

1. **Stable Action ID:** `CB_USER_HOME`
2. **Visible Labels:** **FA:** `🏠 خانه` | **EN:** `🏠 Home`
3. **Callback / Trigger Pattern:** `u:home`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated bot user.
6. **Parent Screen → Destination Screen:** `Any User Sub-Screen` → `User Home Screen`
7. **Required Input & Validation:** Exact callback 'u:home' (6 bytes).
8. **Domain Operation Invoked:** `onboarding.render_user_home`
9. **Database Entities (Read / Write):** **Read:** `users, plans, wallet` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Renders main subscriber menu (Plans, Support, Referral, Help, Language, Channel Panel).
12. **Error / Empty / Busy / Expired Behavior:** Gracefully ignores MessageIsNotModified.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_home`

### 10. `CB_USER_PLANS` — 💎 Plans & Subscriptions / 💎 اشتراکها و پلنها

1. **Stable Action ID:** `CB_USER_PLANS`
2. **Visible Labels:** **FA:** `💎 اشتراکها و پلنها` | **EN:** `💎 Plans & Subscriptions`
3. **Callback / Trigger Pattern:** `u:plans`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user; mandatory membership check.
6. **Parent Screen → Destination Screen:** `User Home / Admin Home` → `Plans Catalog Screen`
7. **Required Input & Validation:** Exact callback 'u:plans' (7 bytes).
8. **Domain Operation Invoked:** `plans.list_active_plans_for_user`
9. **Database Entities (Read / Write):** **Read:** `users, plans, pay_requests, wallet` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Lists active plans, current subscription summary, expiry, and wallet balance.
12. **Error / Empty / Busy / Expired Behavior:** Shows empty-state notice if no active plans are configured.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_plans`

### 11. `CB_USER_PLAN_DETAIL` — 📋 Inspect Plan Details / 📋 مشاهده جزئیات پلن

1. **Stable Action ID:** `CB_USER_PLAN_DETAIL`
2. **Visible Labels:** **FA:** `📋 مشاهده جزئیات پلن` | **EN:** `📋 Inspect Plan Details`
3. **Callback / Trigger Pattern:** `u:plan:<plan_id>`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user; target plan must be active.
6. **Parent Screen → Destination Screen:** `Plans Catalog Screen` → `Plan Detail & Quote Screen`
7. **Required Input & Validation:** Callback pattern ^u:plan:\d+$ (<= 20 bytes).
8. **Domain Operation Invoked:** `plans.inspect_plan_and_quote`
9. **Database Entities (Read / Write):** **Read:** `users, plans, discounts, wallet, pay_requests` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Displays quotas, duration, price (TMN for fa / USD for en), proration credit, discount, wallet applied, and payable total.
12. **Error / Empty / Busy / Expired Behavior:** If plan deactivated, returns to catalog with notice.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_plan_detail`

### 12. `CB_USER_FREE_CLAIM` — 🎁 Claim Free Plan / 🎁 فعالسازی پلن رایگان

1. **Stable Action ID:** `CB_USER_FREE_CLAIM`
2. **Visible Labels:** **FA:** `🎁 فعالسازی پلن رایگان` | **EN:** `🎁 Claim Free Plan`
3. **Callback / Trigger Pattern:** `u:free:<plan_id>`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** User must have free_plan_used == 0 and no active paid plan.
6. **Parent Screen → Destination Screen:** `Plan Detail Screen` → `Admin Home / Subscription Confirmation Screen`
7. **Required Input & Validation:** Callback pattern ^u:free:\d+$ (<= 20 bytes).
8. **Domain Operation Invoked:** `subscription.claim_one_time_free_plan`
9. **Database Entities (Read / Write):** **Read:** `users, plans` | **Write:** `users, plan_changes`
10. **External Side Effects:** Telegram answerCallbackQuery + editMessageText.
11. **Success Feedback & Next Screen:** Atomically sets free_plan_used=1, assigns free plan, logs plan_changes, and opens channel management.
12. **Error / Empty / Busy / Expired Behavior:** If already claimed, rejects and routes to u:freeused:<plan_id> behavior.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_free_claim_once`

### 13. `CB_USER_FREE_USED` — 🚫 Free Plan Already Used / 🚫 پلن رایگان قبلاً استفاده شده

1. **Stable Action ID:** `CB_USER_FREE_USED`
2. **Visible Labels:** **FA:** `🚫 پلن رایگان قبلاً استفاده شده` | **EN:** `🚫 Free Plan Already Used`
3. **Callback / Trigger Pattern:** `u:freeused:<plan_id>`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user with free_plan_used == 1.
6. **Parent Screen → Destination Screen:** `Plan Detail / Catalog Screen` → `Same Screen (Alert Toast)`
7. **Required Input & Validation:** Callback pattern ^u:freeused:\d+$ (<= 24 bytes).
8. **Domain Operation Invoked:** `subscription.notify_free_plan_already_used`
9. **Database Entities (Read / Write):** **Read:** `users` | **Write:** `None`
10. **External Side Effects:** Telegram answerCallbackQuery(show_alert=True).
11. **Success Feedback & Next Screen:** Shows clear localized alert that one-time free plan has already been used.
12. **Error / Empty / Busy / Expired Behavior:** N/A (idempotent alert).
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_freeused_alert`

### 14. `CB_USER_PLAN_LOCKED` — 🔒 Locked (Downgrade Not Allowed) / 🔒 غیرقابل انتخاب (تنزل پلن مجاز نیست)

1. **Stable Action ID:** `CB_USER_PLAN_LOCKED`
2. **Visible Labels:** **FA:** `🔒 غیرقابل انتخاب (تنزل پلن مجاز نیست)` | **EN:** `🔒 Locked (Downgrade Not Allowed)`
3. **Callback / Trigger Pattern:** `u:locked:<plan_id>`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user attempting disallowed downgrade while higher plan is active.
6. **Parent Screen → Destination Screen:** `Plan Detail / Catalog Screen` → `Same Screen (Alert Toast)`
7. **Required Input & Validation:** Callback pattern ^u:locked:\d+$ (<= 22 bytes).
8. **Domain Operation Invoked:** `subscription.notify_plan_locked_downgrade`
9. **Database Entities (Read / Write):** **Read:** `users, plans` | **Write:** `None`
10. **External Side Effects:** Telegram answerCallbackQuery(show_alert=True).
11. **Success Feedback & Next Screen:** Explains why paid downgrade is blocked while current higher plan is active.
12. **Error / Empty / Busy / Expired Behavior:** N/A (idempotent alert).
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_locked_downgrade`

### 15. `CB_USER_PAYMENT_PENDING` — ⏳ Payment Request Pending Review / ⏳ درخواست پرداخت در انتظار بررسی

1. **Stable Action ID:** `CB_USER_PAYMENT_PENDING`
2. **Visible Labels:** **FA:** `⏳ درخواست پرداخت در انتظار بررسی` | **EN:** `⏳ Payment Request Pending Review`
3. **Callback / Trigger Pattern:** `u:pending:<plan_id>`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** User already has an open pending/processing pay_request.
6. **Parent Screen → Destination Screen:** `Plan Detail Screen` → `Pending Payment Status Screen / Alert`
7. **Required Input & Validation:** Callback pattern ^u:pending:\d+$ (<= 23 bytes).
8. **Domain Operation Invoked:** `payments.inspect_pending_request_status`
9. **Database Entities (Read / Write):** **Read:** `pay_requests, plans` | **Write:** `None`
10. **External Side Effects:** Telegram answerCallbackQuery(show_alert=True) or editMessageText.
11. **Success Feedback & Next Screen:** Displays pending payment reference, submitted timestamp, and amount awaiting review.
12. **Error / Empty / Busy / Expired Behavior:** If request was already resolved, refreshes Plan Detail screen.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_pending_payment`

### 16. `CB_USER_DISCOUNT_APPLY` — 🏷 Apply Discount Code / 🏷 ثبت کد تخفیف

1. **Stable Action ID:** `CB_USER_DISCOUNT_APPLY`
2. **Visible Labels:** **FA:** `🏷 ثبت کد تخفیف` | **EN:** `🏷 Apply Discount Code`
3. **Callback / Trigger Pattern:** `u:disc:<plan_id>`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user viewing an eligible numeric-tariff plan.
6. **Parent Screen → Destination Screen:** `Plan Detail Screen` → `Discount Code Guided Input`
7. **Required Input & Validation:** Callback ^u:disc:\d+$; expects alphanumeric discount code text (3-32 chars).
8. **Domain Operation Invoked:** `discounts.prompt_and_validate_discount_code`
9. **Database Entities (Read / Write):** **Read:** `discounts, plans, users` | **Write:** `kv_cache`
10. **External Side Effects:** Telegram editMessageText + text reply handler.
11. **Success Feedback & Next Screen:** Validates active/unexpired/available discount code and updates Plan Quote view.
12. **Error / Empty / Busy / Expired Behavior:** Invalid, expired, or exhausted code shows localized error and keeps quote unchanged.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_disc_apply_and_remove`

### 17. `CB_USER_DISCOUNT_REMOVE` — ❌ Remove Discount Code / ❌ حذف کد تخفیف

1. **Stable Action ID:** `CB_USER_DISCOUNT_REMOVE`
2. **Visible Labels:** **FA:** `❌ حذف کد تخفیف` | **EN:** `❌ Remove Discount Code`
3. **Callback / Trigger Pattern:** `u:discx:<plan_id>`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user with discount applied to current quote session.
6. **Parent Screen → Destination Screen:** `Plan Detail Screen` → `Plan Detail Screen (Recalculated)`
7. **Required Input & Validation:** Callback ^u:discx:\d+$ (<= 21 bytes).
8. **Domain Operation Invoked:** `discounts.remove_discount_from_quote`
9. **Database Entities (Read / Write):** **Read:** `plans, wallet` | **Write:** `kv_cache`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Removes discount code from quote and refreshes payable amount.
12. **Error / Empty / Busy / Expired Behavior:** Idempotent if already removed.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_disc_apply_and_remove`

### 18. `CB_USER_REQUEST_PAYMENT` — 💳 Submit Payment Receipt / Buy Plan / 💳 ارسال رسید پرداخت / خرید پلن

1. **Stable Action ID:** `CB_USER_REQUEST_PAYMENT`
2. **Visible Labels:** **FA:** `💳 ارسال رسید پرداخت / خرید پلن` | **EN:** `💳 Submit Payment Receipt / Buy Plan`
3. **Callback / Trigger Pattern:** `u:req:<plan_id>`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user; no open pending payment; plan has valid numeric tariff in user's currency.
6. **Parent Screen → Destination Screen:** `Plan Detail Screen` → `Receipt Submission Guided Input (or Instant Activation if Wallet Covers 100%)`
7. **Required Input & Validation:** Callback ^u:req:\d+$; expects text receipt (10-1000 chars), photo (<=10MB), or document (<=15MB PDF/image).
8. **Domain Operation Invoked:** `payments.initiate_payment_request`
9. **Database Entities (Read / Write):** **Read:** `users, plans, discounts, wallet, pay_requests, rate_limits` | **Write:** `pay_requests, notifications, rate_limits`
10. **External Side Effects:** Telegram editMessageText + Outbox notification forwarding receipt to superadmins.
11. **Success Feedback & Next Screen:** Saves immutable quote snapshot & receipt, notifies superadmins, and shows pending confirmation.
12. **Error / Empty / Busy / Expired Behavior:** Rate-limited on repeated submissions; negotiated text-only plans prompt contacting support instead.
13. **Destructive & Confirmation Policy:** `Destructive=False` — Shows exact currency, base price, discount, wallet applied, and final payable amount before receipt input.
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_req_receipt_submission`

### 19. `CB_USER_REFERRAL` — 🎁 Invite Friends & Earn Rewards / 🎁 دعوت از دوستان و کسب درآمد

1. **Stable Action ID:** `CB_USER_REFERRAL`
2. **Visible Labels:** **FA:** `🎁 دعوت از دوستان و کسب درآمد` | **EN:** `🎁 Invite Friends & Earn Rewards`
3. **Callback / Trigger Pattern:** `u:ref`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user.
6. **Parent Screen → Destination Screen:** `User Home Screen` → `Referral Dashboard Screen`
7. **Required Input & Validation:** Exact callback 'u:ref' (5 bytes).
8. **Domain Operation Invoked:** `referrals.get_user_referral_dashboard`
9. **Database Entities (Read / Write):** **Read:** `users, referrals, wallet, settings` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Displays deep link, referral rules, invited count, and currency-separated rewards.
12. **Error / Empty / Busy / Expired Behavior:** Handles zero referrals with clear explanation.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_ref`

### 20. `CB_USER_SUPPORT` — 💬 Support / 💬 پشتیبانی

1. **Stable Action ID:** `CB_USER_SUPPORT`
2. **Visible Labels:** **FA:** `💬 پشتیبانی` | **EN:** `💬 Support`
3. **Callback / Trigger Pattern:** `u:sup | u:support | u:sclose`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user; not banned from support; subject to support rate limits.
6. **Parent Screen → Destination Screen:** `User Home Screen` → `Support Conversation Mode`
7. **Required Input & Validation:** Text, photo, video, voice, or document within safe size limits.
8. **Domain Operation Invoked:** `support.open_or_close_user_support_session`
9. **Database Entities (Read / Write):** **Read:** `users, support, rate_limits` | **Write:** `support, support_map, notifications, rate_limits`
10. **External Side Effects:** Forwards message/media to superadmins with sender context and records support_map.
11. **Success Feedback & Next Screen:** Confirms message delivery to support and provides Close Support button.
12. **Error / Empty / Busy / Expired Behavior:** Rate limit exceeded returns cooldown notice.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_support_flow.py::test_user_support_open_forward_reply_close`

### 21. `CB_USER_MANAGE_PANEL` — ⚙️ Manage Channels / ⚙️ مدیریت کانالها

1. **Stable Action ID:** `CB_USER_MANAGE_PANEL`
2. **Visible Labels:** **FA:** `⚙️ مدیریت کانالها` | **EN:** `⚙️ Manage Channels`
3. **Callback / Trigger Pattern:** `u:man`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user; mandatory membership check.
6. **Parent Screen → Destination Screen:** `User Home Screen` → `Channel Admin Home Screen`
7. **Required Input & Validation:** Exact callback 'u:man' (5 bytes).
8. **Domain Operation Invoked:** `channels.open_admin_home`
9. **Database Entities (Read / Write):** **Read:** `users, plans, channels, sources, usage` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Transitions to Channel Administrator Home.
12. **Error / Empty / Busy / Expired Behavior:** If no plan and no channels, displays prompt to activate a plan.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_man`

### 22. `CB_USER_HELP` — 📚 Help / 📚 راهنما

1. **Stable Action ID:** `CB_USER_HELP`
2. **Visible Labels:** **FA:** `📚 راهنما` | **EN:** `📚 Help`
3. **Callback / Trigger Pattern:** `u:help`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user.
6. **Parent Screen → Destination Screen:** `User Home Screen` → `Help Screen`
7. **Required Input & Validation:** Exact callback 'u:help' (6 bytes).
8. **Domain Operation Invoked:** `i18n.get_help_screen`
9. **Database Entities (Read / Write):** **Read:** `users, settings` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Renders localized help text with Back button.
12. **Error / Empty / Busy / Expired Behavior:** Falls back to default help string if custom string is unset.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_help`

### 23. `CB_USER_LANG` — 🌐 Language / تغییر زبان / 🌐 تغییر زبان / Language

1. **Stable Action ID:** `CB_USER_LANG`
2. **Visible Labels:** **FA:** `🌐 تغییر زبان / Language` | **EN:** `🌐 Language / تغییر زبان`
3. **Callback / Trigger Pattern:** `u:lang | u:lang:fa | u:lang:en`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user.
6. **Parent Screen → Destination Screen:** `First-Time Onboarding / User Home` → `Language Picker -> User Home (Localized)`
7. **Required Input & Validation:** Callback in {'u:lang', 'u:lang:fa', 'u:lang:en'}.
8. **Domain Operation Invoked:** `identity.set_user_language`
9. **Database Entities (Read / Write):** **Read:** `users, channels` | **Write:** `users, channels`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Updates user.lang, relocalizes only unmodified default channel texts (preserving custom edits), and renders Home.
12. **Error / Empty / Busy / Expired Behavior:** Unsupported language code rejected.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_user_callbacks.py::test_u_lang_switch_preserves_custom_channel_settings`

### 24. `CB_MANDATORY_MEMBERSHIP_CHECK` — ✅ I Joined / Verify Membership / ✅ عضو شدم / بررسی عضویت

1. **Stable Action ID:** `CB_MANDATORY_MEMBERSHIP_CHECK`
2. **Visible Labels:** **FA:** `✅ عضو شدم / بررسی عضویت` | **EN:** `✅ I Joined / Verify Membership`
3. **Callback / Trigger Pattern:** `a:mand`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user gated by mandatory channel requirement.
6. **Parent Screen → Destination Screen:** `Mandatory Membership Gate Screen` → `Originally Requested Screen or Home`
7. **Required Input & Validation:** Exact callback 'a:mand' (6 bytes).
8. **Domain Operation Invoked:** `membership.verify_mandatory_channel_membership`
9. **Database Entities (Read / Write):** **Read:** `users, settings` | **Write:** `None`
10. **External Side Effects:** Telegram getChatMember API call + editMessageText/answerCallbackQuery.
11. **Success Feedback & Next Screen:** Confirms membership and unlocks the requested screen.
12. **Error / Empty / Busy / Expired Behavior:** If user not joined, shows alert; if Telegram API has transient network failure, fails safe without permanent lockout.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_membership_gate.py::test_a_mand_verification_and_transient_error`

### 25. `CB_SHARED_CANCEL` — 🔙 Cancel & Back / 🔙 انصراف و بازگشت

1. **Stable Action ID:** `CB_SHARED_CANCEL`
2. **Visible Labels:** **FA:** `🔙 انصراف و بازگشت` | **EN:** `🔙 Cancel & Back`
3. **Callback / Trigger Pattern:** `c:cancel`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user in an active guided input or confirmation prompt.
6. **Parent Screen → Destination Screen:** `Any Guided Input / Confirmation Dialog` → `Parent Screen Recorded in Session`
7. **Required Input & Validation:** Exact callback 'c:cancel' (8 bytes).
8. **Domain Operation Invoked:** `session.cancel_and_return_to_parent`
9. **Database Entities (Read / Write):** **Read:** `kv_cache` | **Write:** `kv_cache`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Aborts pending input/confirmation and restores parent view.
12. **Error / Empty / Busy / Expired Behavior:** If session expired, returns safely to role Home screen.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_shared_callbacks.py::test_c_cancel`

### 26. `CB_ADMIN_HOME` — 🎛 Channel Admin Home / 🎛 پنل مدیریت کانالها

1. **Stable Action ID:** `CB_ADMIN_HOME`
2. **Visible Labels:** **FA:** `🎛 پنل مدیریت کانالها` | **EN:** `🎛 Channel Admin Home`
3. **Callback / Trigger Pattern:** `a:home`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user; displays only channels owned by actor (plus Superadmin Panel button only for superadmins).
6. **Parent Screen → Destination Screen:** `User Home / Channel Menu / Superadmin Dashboard` → `Channel Admin Home Screen`
7. **Required Input & Validation:** Exact callback 'a:home' (6 bytes).
8. **Domain Operation Invoked:** `channels.render_admin_home`
9. **Database Entities (Read / Write):** **Read:** `users, plans, channels, sources, usage` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Shows current plan/expiry, today's post/test usage, channel/source counts vs limits, owned channels, Add Channel, My Plan, and Superadmin entry (if superadmin).
12. **Error / Empty / Busy / Expired Behavior:** Gracefully displays empty channel list with Add Channel button.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_admin_callbacks.py::test_a_home`

### 27. `CB_ADMIN_MY_PLAN` — 💳 My Plan & Renew/Upgrade / 💳 پلن من و تمدید/ارتقاء

1. **Stable Action ID:** `CB_ADMIN_MY_PLAN`
2. **Visible Labels:** **FA:** `💳 پلن من و تمدید/ارتقاء` | **EN:** `💳 My Plan & Renew/Upgrade`
3. **Callback / Trigger Pattern:** `a:plan`
4. **Roles Allowed:** `user, admin, superadmin`
5. **Ownership & Entitlement Requirements:** Authenticated user.
6. **Parent Screen → Destination Screen:** `Channel Admin Home Screen` → `Current Plan Summary & Plans Catalog`
7. **Required Input & Validation:** Exact callback 'a:plan' (6 bytes).
8. **Domain Operation Invoked:** `plans.inspect_current_plan_from_admin`
9. **Database Entities (Read / Write):** **Read:** `users, plans, usage, wallet` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Shows detailed entitlement usage, expiry, and renewal/upgrade options.
12. **Error / Empty / Busy / Expired Behavior:** Prompts plan selection if no active plan.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_admin_callbacks.py::test_a_plan`

### 28. `CB_ADMIN_CHANNEL_ADD` — ➕ Add New Channel / ➕ افزودن کانال جدید

1. **Stable Action ID:** `CB_ADMIN_CHANNEL_ADD`
2. **Visible Labels:** **FA:** `➕ افزودن کانال جدید` | **EN:** `➕ Add New Channel`
3. **Callback / Trigger Pattern:** `a:chadd`
4. **Roles Allowed:** `admin, superadmin, user`
5. **Ownership & Entitlement Requirements:** Requires active plan with channels_count < max_channels (or superadmin) + mandatory membership.
6. **Parent Screen → Destination Screen:** `Channel Admin Home Screen` → `Channel Registration Guided Input / Conflict Resolution Flow`
7. **Required Input & Validation:** Forwarded post from channel, @username, or -100... ID; or 10-digit transfer token if channel conflict recovery is triggered.
8. **Domain Operation Invoked:** `channels.register_or_recover_channel`
9. **Database Entities (Read / Write):** **Read:** `users, plans, channels, ch_tokens, pub_journal, rate_limits` | **Write:** `channels, ch_tokens, notifications, rate_limits`
10. **External Side Effects:** Telegram getChat, getChatMember (bot & user permissions), Outbox notifications on ownership transfer.
11. **Success Feedback & Next Screen:** Registers channel or completes audited ownership transfer and opens Channel Home.
12. **Error / Empty / Busy / Expired Behavior:** Rejects if bot lacks post permission, user is not channel admin, quota full, or transfer token invalid/expired/locked by active publication.
13. **Destructive & Confirmation Policy:** `Destructive=False` — Ownership transfer requires 10-digit token verification and notifies old owner + superadmins.
14. **Automated Verification Test:** `tests/ui/test_channel_registration.py::test_a_chadd_and_ownership_transfer`

### 29. `CB_ADMIN_CHANNEL_HOME` — 📢 Channel Dashboard / 📢 مدیریت کانال

1. **Stable Action ID:** `CB_ADMIN_CHANNEL_HOME`
2. **Visible Labels:** **FA:** `📢 مدیریت کانال` | **EN:** `📢 Channel Dashboard`
3. **Callback / Trigger Pattern:** `a:ch:<channel_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Strict ownership check: channels.owner_id == user_id (or superadmin).
6. **Parent Screen → Destination Screen:** `Channel Admin Home Screen` → `Channel Home Menu (10 Functional Areas)`
7. **Required Input & Validation:** Callback ^a:ch:-?\d+$ (<= 24 bytes).
8. **Domain Operation Invoked:** `channels.open_channel_dashboard`
9. **Database Entities (Read / Write):** **Read:** `channels, sources, articles, usage` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Renders 10-area Channel Menu: Test, Report, Sources, Queue, Scheduling, Content, Rejected, Automation Toggle, Personal AI, Delete Channel.
12. **Error / Empty / Busy / Expired Behavior:** Unauthorized or deleted channel returns permission/not-found error and redirects to Admin Home.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_admin_callbacks.py::test_a_ch_dashboard_authz`

### 30. `CB_ADMIN_CHANNEL_TEST` — 🧪 Run Manual Test Cycle / 🧪 تست دستی چرخه کانال

1. **Stable Action ID:** `CB_ADMIN_CHANNEL_TEST`
2. **Visible Labels:** **FA:** `🧪 تست دستی چرخه کانال` | **EN:** `🧪 Run Manual Test Cycle`
3. **Callback / Trigger Pattern:** `a:test:<channel_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Channel owner; requires active plan and usage.tests_count < plan.daily_test_limit.
6. **Parent Screen → Destination Screen:** `Channel Home Menu` → `Channel Test Diagnostics Result Screen`
7. **Required Input & Validation:** Callback ^a:test:-?\d+$ (<= 26 bytes).
8. **Domain Operation Invoked:** `workflows.channel_test.run_manual_test_cycle`
9. **Database Entities (Read / Write):** **Read:** `channels, sources, articles, ai_models, usage, plans` | **Write:** `usage, articles, sources, ai_usage, ai_cache`
10. **External Side Effects:** SSRF-safe source fetch, AI provider call, Telegram progress & diagnostic summary update.
11. **Success Feedback & Next Screen:** Shows stage-by-stage outcome (discovered, extracted, filtered, AI scored/formatted, ready/rejected) and remaining test quota.
12. **Error / Empty / Busy / Expired Behavior:** Blocks if daily test quota exhausted or a test is already running for this channel.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_admin_callbacks.py::test_a_test_manual_cycle_and_quota`

### 31. `CB_ADMIN_CHANNEL_REPORT` — 📊 Channel Activity Report / 📊 گزارش عملکرد کانال

1. **Stable Action ID:** `CB_ADMIN_CHANNEL_REPORT`
2. **Visible Labels:** **FA:** `📊 گزارش عملکرد کانال` | **EN:** `📊 Channel Activity Report`
3. **Callback / Trigger Pattern:** `a:rep:<channel_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Channel owner (or superadmin).
6. **Parent Screen → Destination Screen:** `Channel Home Menu` → `Channel Report Screen`
7. **Required Input & Validation:** Callback ^a:rep:-?\d+$ (<= 25 bytes).
8. **Domain Operation Invoked:** `reports.get_channel_activity_report`
9. **Database Entities (Read / Write):** **Read:** `channels, sources, articles, posted, usage, plans` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Displays recent publications, active/failing sources, ready/rejected counts, and daily quota usage.
12. **Error / Empty / Busy / Expired Behavior:** Shows clean zero-state metrics for newly created channels.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_admin_callbacks.py::test_a_rep_channel_report`

### 32. `CB_ADMIN_CHANNEL_AUTOMATION_TOGGLE` — ⚡ Toggle Channel Automation / ⚡ روشن/خاموش کردن اتوماسیون کانال

1. **Stable Action ID:** `CB_ADMIN_CHANNEL_AUTOMATION_TOGGLE`
2. **Visible Labels:** **FA:** `⚡ روشن/خاموش کردن اتوماسیون کانال` | **EN:** `⚡ Toggle Channel Automation`
3. **Callback / Trigger Pattern:** `a:tog:<channel_id>:enabled`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Channel owner with active plan (to enable).
6. **Parent Screen → Destination Screen:** `Channel Home Menu` → `Channel Home Menu (Updated Status)`
7. **Required Input & Validation:** Callback ^a:tog:-?\d+:enabled$ (<= 33 bytes).
8. **Domain Operation Invoked:** `channels.toggle_channel_automation`
9. **Database Entities (Read / Write):** **Read:** `channels, users, plans` | **Write:** `channels`
10. **External Side Effects:** Telegram answerCallbackQuery + editMessageText.
11. **Success Feedback & Next Screen:** Flips channel.enabled state and updates badge on Channel Home Menu.
12. **Error / Empty / Busy / Expired Behavior:** Cannot enable automation if subscription is expired or channel has 0 enabled sources.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_admin_callbacks.py::test_a_tog_channel_automation`

### 33. `CB_ADMIN_CHANNEL_DELETE` — 🗑 Delete Channel / 🗑 حذف کانال

1. **Stable Action ID:** `CB_ADMIN_CHANNEL_DELETE`
2. **Visible Labels:** **FA:** `🗑 حذف کانال` | **EN:** `🗑 Delete Channel`
3. **Callback / Trigger Pattern:** `a:chdel:<channel_id> | a:chdel:<channel_id>:confirm`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Strict channel owner verification.
6. **Parent Screen → Destination Screen:** `Channel Home Menu` → `Delete Confirmation Dialog -> Channel Admin Home`
7. **Required Input & Validation:** Callback ^a:chdel:-?\d+(:confirm)?$ (<= 35 bytes).
8. **Domain Operation Invoked:** `channels.delete_channel_registration`
9. **Database Entities (Read / Write):** **Read:** `channels, pub_journal` | **Write:** `channels, sources, articles, ch_tokens`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** After explicit confirmation, removes active channel registration while preserving immutable financial/audit records.
12. **Error / Empty / Busy / Expired Behavior:** Blocks deletion if an active publication is currently in-flight for the channel.
13. **Destructive & Confirmation Policy:** `Destructive=True` — Explicit two-step confirmation screen warning that channel sources and queues will be removed.
14. **Automated Verification Test:** `tests/ui/test_admin_callbacks.py::test_a_chdel_confirmation_and_cleanup`

### 34. `CB_ADMIN_SOURCES_LIST` — 🌐 Manage Content Sources / 🌐 مدیریت منابع محتوا

1. **Stable Action ID:** `CB_ADMIN_SOURCES_LIST`
2. **Visible Labels:** **FA:** `🌐 مدیریت منابع محتوا` | **EN:** `🌐 Manage Content Sources`
3. **Callback / Trigger Pattern:** `a:src:<channel_id>[:<page>]`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Channel owner.
6. **Parent Screen → Destination Screen:** `Channel Home Menu` → `Paginated Source List Screen`
7. **Required Input & Validation:** Callback ^a:src:-?\d+(:\d+)?$ (<= 30 bytes).
8. **Domain Operation Invoked:** `sources.list_channel_sources`
9. **Database Entities (Read / Write):** **Read:** `channels, sources, plans` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Lists configured sources with fetch/publish badges, pagination controls, and Add Source button.
12. **Error / Empty / Busy / Expired Behavior:** Shows empty-state guide when no sources exist.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_sources_callbacks.py::test_a_src_list_and_pagination`

### 35. `CB_ADMIN_SOURCE_ADD` — ➕ Add New Source (URL) / ➕ افزودن منبع جدید (URL)

1. **Stable Action ID:** `CB_ADMIN_SOURCE_ADD`
2. **Visible Labels:** **FA:** `➕ افزودن منبع جدید (URL)` | **EN:** `➕ Add New Source (URL)`
3. **Callback / Trigger Pattern:** `a:srca:<channel_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Channel owner; total sources < plan.max_sources.
6. **Parent Screen → Destination Screen:** `Paginated Source List Screen` → `Add Source Guided Input -> Source Detail Screen`
7. **Required Input & Validation:** Callback ^a:srca:-?\d+$; expects valid HTTP/HTTPS URL passing SSRF check.
8. **Domain Operation Invoked:** `sources.add_and_probe_source`
9. **Database Entities (Read / Write):** **Read:** `channels, sources, plans` | **Write:** `sources, kv_cache`
10. **External Side Effects:** SSRF-safe discovery probe (RSS/Atom, JSON Feed, Sitemap, HTML, JSON API).
11. **Success Feedback & Next Screen:** Detects discovery strategy, saves source, and displays diagnostic summary.
12. **Error / Empty / Busy / Expired Behavior:** Rejects private/loopback IPs (SSRF), duplicate URLs, or quota exhaustion.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_sources_callbacks.py::test_a_srca_add_and_ssrf_block`

### 36. `CB_ADMIN_SOURCE_INSPECT` — 🔍 Inspect Source Details / 🔍 جزئیات و وضعیت منبع

1. **Stable Action ID:** `CB_ADMIN_SOURCE_INSPECT`
2. **Visible Labels:** **FA:** `🔍 جزئیات و وضعیت منبع` | **EN:** `🔍 Inspect Source Details`
3. **Callback / Trigger Pattern:** `a:srcv:<source_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Owner of the channel to which source_id belongs.
6. **Parent Screen → Destination Screen:** `Paginated Source List Screen` → `Source Detail & Diagnostics Screen`
7. **Required Input & Validation:** Callback ^a:srcv:\d+$ (<= 22 bytes).
8. **Domain Operation Invoked:** `sources.inspect_source`
9. **Database Entities (Read / Write):** **Read:** `sources, channels` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Shows URL, discovery strategy, fetch/publish toggles, custom API indicator (key masked), ETag/cooldown, and actions.
12. **Error / Empty / Busy / Expired Behavior:** Returns to source list if source was deleted.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_sources_callbacks.py::test_a_srcv_inspect`

### 37. `CB_ADMIN_SOURCE_RECHECK` — 🔄 Recheck Source & Diagnostics / 🔄 بررسی مجدد و عیبیابی منبع

1. **Stable Action ID:** `CB_ADMIN_SOURCE_RECHECK`
2. **Visible Labels:** **FA:** `🔄 بررسی مجدد و عیبیابی منبع` | **EN:** `🔄 Recheck Source & Diagnostics`
3. **Callback / Trigger Pattern:** `a:srcr:<source_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Owner of the parent channel.
6. **Parent Screen → Destination Screen:** `Source Detail Screen` → `Source Detail Screen (With Updated Diagnostics)`
7. **Required Input & Validation:** Callback ^a:srcr:\d+$ (<= 22 bytes).
8. **Domain Operation Invoked:** `sources.recheck_source_diagnostics`
9. **Database Entities (Read / Write):** **Read:** `sources, channels` | **Write:** `sources`
10. **External Side Effects:** SSRF-safe HTTP fetch + Telegram editMessageText.
11. **Success Feedback & Next Screen:** Displays candidate article count, sample titles, date parsing status, and resets cooldown on success.
12. **Error / Empty / Busy / Expired Behavior:** Shows safe, sanitized error reason (without leaking API keys) if remote host fails.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_sources_callbacks.py::test_a_srcr_diagnostics`

### 38. `CB_ADMIN_SOURCE_TOGGLE_FETCH` — 📡 Toggle Source Fetching / 📡 روشن/خاموش کردن دریافت (Fetch)

1. **Stable Action ID:** `CB_ADMIN_SOURCE_TOGGLE_FETCH`
2. **Visible Labels:** **FA:** `📡 روشن/خاموش کردن دریافت (Fetch)` | **EN:** `📡 Toggle Source Fetching`
3. **Callback / Trigger Pattern:** `a:srct:<source_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Owner of the parent channel.
6. **Parent Screen → Destination Screen:** `Source Detail Screen` → `Source Detail Screen`
7. **Required Input & Validation:** Callback ^a:srct:\d+$ (<= 22 bytes).
8. **Domain Operation Invoked:** `sources.toggle_fetch_enabled`
9. **Database Entities (Read / Write):** **Read:** `sources, channels` | **Write:** `sources`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Toggles fetch_enabled and updates Source Detail view.
12. **Error / Empty / Busy / Expired Behavior:** Unauthorized access blocked.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_sources_callbacks.py::test_a_srct_and_srctb_toggles`

### 39. `CB_ADMIN_SOURCE_TOGGLE_PUBLISH` — 📤 Toggle Source Publication Use / 📤 روشن/خاموش کردن استفاده برای انتشار

1. **Stable Action ID:** `CB_ADMIN_SOURCE_TOGGLE_PUBLISH`
2. **Visible Labels:** **FA:** `📤 روشن/خاموش کردن استفاده برای انتشار` | **EN:** `📤 Toggle Source Publication Use`
3. **Callback / Trigger Pattern:** `a:srctb:<source_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Owner of the parent channel.
6. **Parent Screen → Destination Screen:** `Source Detail Screen` → `Source Detail Screen`
7. **Required Input & Validation:** Callback ^a:srctb:\d+$ (<= 23 bytes).
8. **Domain Operation Invoked:** `sources.toggle_publish_enabled`
9. **Database Entities (Read / Write):** **Read:** `sources, channels` | **Write:** `sources`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Separately toggles publish_enabled without altering fetch_enabled.
12. **Error / Empty / Busy / Expired Behavior:** Unauthorized access blocked.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_sources_callbacks.py::test_a_srct_and_srctb_toggles`

### 40. `CB_ADMIN_SOURCE_CUSTOM_API` — 🔑 Configure Custom API & Key / 🔑 تنظیم API اختصاصی و کلید منبع

1. **Stable Action ID:** `CB_ADMIN_SOURCE_CUSTOM_API`
2. **Visible Labels:** **FA:** `🔑 تنظیم API اختصاصی و کلید منبع` | **EN:** `🔑 Configure Custom API & Key`
3. **Callback / Trigger Pattern:** `a:srcapi:<source_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Owner of the parent channel.
6. **Parent Screen → Destination Screen:** `Source Detail Screen` → `Custom API Guided Input -> Source Detail Screen`
7. **Required Input & Validation:** Callback ^a:srcapi:\d+$; expects valid HTTPS endpoint URL (SSRF-checked) and optional API key.
8. **Domain Operation Invoked:** `sources.configure_custom_api`
9. **Database Entities (Read / Write):** **Read:** `sources, channels` | **Write:** `sources, kv_cache`
10. **External Side Effects:** Encrypts API key at rest via Fernet; deletes user message containing plaintext key where possible.
11. **Success Feedback & Next Screen:** Saves encrypted custom API config and returns to Source Detail.
12. **Error / Empty / Busy / Expired Behavior:** Invalid/private URL rejected by SSRF guard.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_sources_callbacks.py::test_a_srcapi_encryption_and_clear`

### 41. `CB_ADMIN_SOURCE_CUSTOM_API_CLEAR` — 🧹 Clear Custom API Settings / 🧹 پاک کردن تنظیمات API اختصاصی

1. **Stable Action ID:** `CB_ADMIN_SOURCE_CUSTOM_API_CLEAR`
2. **Visible Labels:** **FA:** `🧹 پاک کردن تنظیمات API اختصاصی` | **EN:** `🧹 Clear Custom API Settings`
3. **Callback / Trigger Pattern:** `a:srcapix:<source_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Owner of the parent channel.
6. **Parent Screen → Destination Screen:** `Source Detail Screen` → `Source Detail Screen`
7. **Required Input & Validation:** Callback ^a:srcapix:\d+$ (<= 25 bytes).
8. **Domain Operation Invoked:** `sources.clear_custom_api`
9. **Database Entities (Read / Write):** **Read:** `sources, channels` | **Write:** `sources`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Wipes custom_api_url and custom_api_key_enc and reverts source to standard discovery.
12. **Error / Empty / Busy / Expired Behavior:** Idempotent if already empty.
13. **Destructive & Confirmation Policy:** `Destructive=True` — Confirmation prompt before wiping stored API credentials.
14. **Automated Verification Test:** `tests/ui/test_sources_callbacks.py::test_a_srcapi_encryption_and_clear`

### 42. `CB_ADMIN_SOURCE_DELETE` — 🗑 Delete Source / 🗑 حذف منبع

1. **Stable Action ID:** `CB_ADMIN_SOURCE_DELETE`
2. **Visible Labels:** **FA:** `🗑 حذف منبع` | **EN:** `🗑 Delete Source`
3. **Callback / Trigger Pattern:** `a:srcd:<source_id>[:confirm]`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Owner of the parent channel.
6. **Parent Screen → Destination Screen:** `Source Detail Screen` → `Delete Confirmation -> Paginated Source List`
7. **Required Input & Validation:** Callback ^a:srcd:\d+(:confirm)?$ (<= 30 bytes).
8. **Domain Operation Invoked:** `sources.delete_source`
9. **Database Entities (Read / Write):** **Read:** `sources, channels` | **Write:** `sources`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Deletes source after confirmation and returns to channel's source list.
12. **Error / Empty / Busy / Expired Behavior:** Gracefully handles already-deleted source.
13. **Destructive & Confirmation Policy:** `Destructive=True` — Explicit confirmation button before deleting source.
14. **Automated Verification Test:** `tests/ui/test_sources_callbacks.py::test_a_srcd_delete`

### 43. `CB_ADMIN_CONTENT_MENU` — ✍️ Editorial & Content Settings / ✍️ تنظیمات محتوا و تحریریه

1. **Stable Action ID:** `CB_ADMIN_CONTENT_MENU`
2. **Visible Labels:** **FA:** `✍️ تنظیمات محتوا و تحریریه` | **EN:** `✍️ Editorial & Content Settings`
3. **Callback / Trigger Pattern:** `a:con:<channel_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Channel owner.
6. **Parent Screen → Destination Screen:** `Channel Home Menu` → `Channel Editorial Configuration Screen`
7. **Required Input & Validation:** Callback ^a:con:-?\d+$ (<= 25 bytes).
8. **Domain Operation Invoked:** `channels.open_content_settings`
9. **Database Entities (Read / Write):** **Read:** `channels` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Displays topic, output language, prompt, signature, categories, criteria, min score, max words, max caption, and formatting switches (including domain-adaptive scientific formatting).
12. **Error / Empty / Busy / Expired Behavior:** Unauthorized channel access blocked.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_content_settings.py::test_a_con_and_set_fields`

### 44. `CB_ADMIN_CONTENT_SET_FIELD` — ⚙️ Edit Content Setting Field / ⚙️ ویرایش فیلد تنظیمات محتوا

1. **Stable Action ID:** `CB_ADMIN_CONTENT_SET_FIELD`
2. **Visible Labels:** **FA:** `⚙️ ویرایش فیلد تنظیمات محتوا` | **EN:** `⚙️ Edit Content Setting Field`
3. **Callback / Trigger Pattern:** `a:set:<channel_id>:<field>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Channel owner.
6. **Parent Screen → Destination Screen:** `Channel Editorial Configuration Screen` → `Guided Input (for text/numeric fields) or Immediate Toggle -> Editorial Screen`
7. **Required Input & Validation:** Fields: topic, lang, prompt, sig, min_score (0-100), max_words (50-4000), max_caption (200-1024), hashtags, src_link, media, strict_ad, sci_format.
8. **Domain Operation Invoked:** `channels.update_content_setting_field`
9. **Database Entities (Read / Write):** **Read:** `channels` | **Write:** `channels, kv_cache`
10. **External Side Effects:** Telegram editMessageText; invalidates ai_cache fingerprint for channel.
11. **Success Feedback & Next Screen:** Updates only the target channel's settings_json and refreshes screen.
12. **Error / Empty / Busy / Expired Behavior:** Out-of-range numbers or invalid HTML in signature rejected with helpful message.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_content_settings.py::test_a_con_and_set_fields`

### 45. `CB_ADMIN_CATEGORIES` — 🗂 Manage Categories (List/Inspect/Add/Style/Delete) / 🗂 مدیریت دستهبندیها (افزودن/مشاهده/ویرایش/حذف)

1. **Stable Action ID:** `CB_ADMIN_CATEGORIES`
2. **Visible Labels:** **FA:** `🗂 مدیریت دستهبندیها (افزودن/مشاهده/ویرایش/حذف)` | **EN:** `🗂 Manage Categories (List/Inspect/Add/Style/Delete)`
3. **Callback / Trigger Pattern:** `a:cat:<ch_id>[:<p>] | a:catv:<ch_id>:<idx> | a:cata:<ch_id> | a:cats:<ch_id>:<idx> | a:catd:<ch_id>:<idx>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Channel owner.
6. **Parent Screen → Destination Screen:** `Channel Editorial Configuration Screen` → `Paginated Categories (15/page) / Category Detail / Guided Input`
7. **Required Input & Validation:** Category name (1-60 chars), emoji (valid unicode emoji), style instruction (1-500 chars).
8. **Domain Operation Invoked:** `channels.categories.manage_categories`
9. **Database Entities (Read / Write):** **Read:** `channels` | **Write:** `channels, kv_cache`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Supports paginated listing (15/page), inspection, adding, style editing, and deletion.
12. **Error / Empty / Busy / Expired Behavior:** Prevents duplicate category names within the same channel.
13. **Destructive & Confirmation Policy:** `Destructive=True` — a:catd requires confirmation before deleting a category.
14. **Automated Verification Test:** `tests/ui/test_content_settings.py::test_categories_crud_and_pagination`

### 46. `CB_ADMIN_CRITERIA` — ⚖️ Manage Evaluation Criteria (List/Inspect/Add/Weight/Delete) / ⚖️ مدیریت معیارهای ارزیابی (افزودن/مشاهده/وزن/حذف)

1. **Stable Action ID:** `CB_ADMIN_CRITERIA`
2. **Visible Labels:** **FA:** `⚖️ مدیریت معیارهای ارزیابی (افزودن/مشاهده/وزن/حذف)` | **EN:** `⚖️ Manage Evaluation Criteria (List/Inspect/Add/Weight/Delete)`
3. **Callback / Trigger Pattern:** `a:cri:<ch_id>[:<p>] | a:criv:<ch_id>:<idx> | a:cria:<ch_id> | a:criw:<ch_id>:<idx> | a:crid:<ch_id>:<idx>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Channel owner.
6. **Parent Screen → Destination Screen:** `Channel Editorial Configuration Screen` → `Paginated Criteria (15/page) / Criterion Detail / Guided Input`
7. **Required Input & Validation:** Criterion name (1-60 chars) and numeric positive weight (0.1 - 100).
8. **Domain Operation Invoked:** `channels.criteria.manage_criteria`
9. **Database Entities (Read / Write):** **Read:** `channels` | **Write:** `channels, kv_cache`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Supports 15-item pagination, inspection, creation, weight adjustment, and deletion.
12. **Error / Empty / Busy / Expired Behavior:** Rejects non-numeric or <=0 weights.
13. **Destructive & Confirmation Policy:** `Destructive=True` — a:crid requires confirmation before deleting a criterion.
14. **Automated Verification Test:** `tests/ui/test_content_settings.py::test_criteria_crud_and_pagination`

### 47. `CB_ADMIN_SCHEDULING` — ⏰ Scheduling, Quiet Hours, Timezone & Publish Mode / ⏰ زمانبندی، ساعات خاموشی، منطقه زمانی و حالت انتشار

1. **Stable Action ID:** `CB_ADMIN_SCHEDULING`
2. **Visible Labels:** **FA:** `⏰ زمانبندی، ساعات خاموشی، منطقه زمانی و حالت انتشار` | **EN:** `⏰ Scheduling, Quiet Hours, Timezone & Publish Mode`
3. **Callback / Trigger Pattern:** `a:sch:<ch_id> | a:quiet:<ch_id> | a:qs:<ch_id>:<h> | a:qoff:<ch_id> | a:tz:<ch_id> | a:tzs:<ch_id>:<tz> | a:mode:<ch_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Channel owner; daily post cap constrained <= active plan daily_post_limit.
6. **Parent Screen → Destination Screen:** `Channel Home Menu` → `Scheduling Config / Quiet Hours Picker / IANA Timezone Picker`
7. **Required Input & Validation:** Cycle interval (mins), posts/cycle, lookback (hours), daily cap (<= plan entitlement), undated toggle, quiet start/end (0-23), valid IANA timezone (e.g. Asia/Tehran, UTC).
8. **Domain Operation Invoked:** `scheduling.configure_channel_schedule`
9. **Database Entities (Read / Write):** **Read:** `channels, plans` | **Write:** `channels, kv_cache`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Persists DST-aware schedule & mode (auto vs review/manual) and updates next run calculation.
12. **Error / Empty / Busy / Expired Behavior:** Rejects daily cap above subscription entitlement or unknown timezone.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_scheduling_callbacks.py::test_scheduling_quiet_hours_and_dst_timezone`

### 48. `CB_ADMIN_QUEUE_AND_ARTICLES` — 📥 Ready Queue, Rejected Items, Inspect, Edit, Publish & Reset / 📥 صف انتشار، مقالات ردشده، بازبینی، ویرایش و انتشار دستی

1. **Stable Action ID:** `CB_ADMIN_QUEUE_AND_ARTICLES`
2. **Visible Labels:** **FA:** `📥 صف انتشار، مقالات ردشده، بازبینی، ویرایش و انتشار دستی` | **EN:** `📥 Ready Queue, Rejected Items, Inspect, Edit, Publish & Reset`
3. **Callback / Trigger Pattern:** `a:que:<ch_id>[:<sort>:<p>] | a:rej:<ch_id>[:<p>] | a:art:<id> | a:arte:<id> | a:artf:<id> | a:pub:<id> | a:artr:<id> | a:artd:<id> | a:qreset:<ch_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Channel owner; manual publish checks daily post quota.
6. **Parent Screen → Destination Screen:** `Channel Home Menu` → `Ready Queue / Rejected List / Article Detail / Full Preview / Edit Prompt`
7. **Required Input & Validation:** Sort by time or score; edited post validated for safe markup and length.
8. **Domain Operation Invoked:** `queue.manage_articles_and_manual_publish`
9. **Database Entities (Read / Write):** **Read:** `channels, articles, usage, plans, pub_journal` | **Write:** `articles, usage, pub_journal, posted, deeplinks`
10. **External Side Effects:** Manual publish (`a:pub`) executes full crash-safe Telegram publication + deep-link creation.
11. **Success Feedback & Next Screen:** Clearly distinguishes ready, editorial-rejected, AI-retryable, exhausted, and delivery-unknown states; provides Telegram post URL once published.
12. **Error / Empty / Busy / Expired Behavior:** Blocks duplicate manual publish if item is already in-flight or delivery-unknown.
13. **Destructive & Confirmation Policy:** `Destructive=True` — a:artd (delete article) and a:qreset (reset ready queue) require explicit confirmation.
14. **Automated Verification Test:** `tests/ui/test_queue_and_manual_publish.py::test_queue_review_edit_publish_restore_reset`

### 49. `CB_ADMIN_PERSONAL_AI` — 🤖 Manage Personal AI Models / 🤖 مدیریت مدلهای هوش مصنوعی شخصی

1. **Stable Action ID:** `CB_ADMIN_PERSONAL_AI`
2. **Visible Labels:** **FA:** `🤖 مدیریت مدلهای هوش مصنوعی شخصی` | **EN:** `🤖 Manage Personal AI Models`
3. **Callback / Trigger Pattern:** `a:myai:<ch_id> | a:myai_add:<ch_id> | a:myai_v:<m_id> | a:myai_t:<m_id> | a:myai_o:<m_id> | a:myai_e:<m_id> | a:myai_d:<m_id>`
4. **Roles Allowed:** `admin, superadmin`
5. **Ownership & Entitlement Requirements:** Strict owner check: ai_models.owner_id == user_id.
6. **Parent Screen → Destination Screen:** `Channel Home Menu` → `Personal AI List / Model Detail / Guided Add & Edit`
7. **Required Input & Validation:** Base URL (HTTPS or explicitly allowlisted local host), API key (or explicit unauthenticated mode), model ID, temperature (0.0-2.0), max_tokens.
8. **Domain Operation Invoked:** `ai.models.manage_personal_models`
9. **Database Entities (Read / Write):** **Read:** `ai_models, channels` | **Write:** `ai_models, kv_cache`
10. **External Side Effects:** a:myai_t executes live test probe against provider; keys encrypted at rest and never echoed.
11. **Success Feedback & Next Screen:** Shows probe latency/status, toggles active state (`a:myai_o`), edits parameters, or deletes model.
12. **Error / Empty / Busy / Expired Behavior:** Masks secret keys in all error outputs if provider probe fails.
13. **Destructive & Confirmation Policy:** `Destructive=True` — a:myai_d requires confirmation before deleting personal AI model.
14. **Automated Verification Test:** `tests/ui/test_personal_ai_callbacks.py::test_myai_crud_probe_and_isolation`

### 50. `CB_SUPER_REPORT` — 📈 Platform Overview Report / 📈 گزارش جامع پلتفرم

1. **Stable Action ID:** `CB_SUPER_REPORT`
2. **Visible Labels:** **FA:** `📈 گزارش جامع پلتفرم` | **EN:** `📈 Platform Overview Report`
3. **Callback / Trigger Pattern:** `s:report`
4. **Roles Allowed:** `superadmin`
5. **Ownership & Entitlement Requirements:** Server-side role == 'superadmin' enforced on handler entry.
6. **Parent Screen → Destination Screen:** `Superadmin Dashboard` → `Platform Report Screen`
7. **Required Input & Validation:** Exact callback 's:report' (8 bytes).
8. **Domain Operation Invoked:** `superadmin.reports.get_platform_summary`
9. **Database Entities (Read / Write):** **Read:** `users, channels, plans, pay_requests, articles, posted, wallet, ai_usage` | **Write:** `None`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Displays total users, active admins, channels, revenue by currency (TMN/USD), published posts, and AI usage.
12. **Error / Empty / Busy / Expired Behavior:** Non-superadmin receives hard authorization denial.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_superadmin_callbacks.py::test_s_report_and_rbac`

### 51. `CB_SUPER_HEALTH` — 🩺 System Health Dashboard / 🩺 وضعیت سلامت سیستم (Health)

1. **Stable Action ID:** `CB_SUPER_HEALTH`
2. **Visible Labels:** **FA:** `🩺 وضعیت سلامت سیستم (Health)` | **EN:** `🩺 System Health Dashboard`
3. **Callback / Trigger Pattern:** `s:health`
4. **Roles Allowed:** `superadmin`
5. **Ownership & Entitlement Requirements:** Server-side role == 'superadmin'.
6. **Parent Screen → Destination Screen:** `Superadmin Dashboard` → `3-State System Health Screen (ok / degraded / unknown)`
7. **Required Input & Validation:** Exact callback 's:health' (8 bytes).
8. **Domain Operation Invoked:** `superadmin.health.collect_system_health`
9. **Database Entities (Read / Write):** **Read:** `settings, channels, articles, pub_journal, pay_requests, notifications, ai_models` | **Write:** `None`
10. **External Side Effects:** Runs DB integrity check, reads process RSS memory, and renders actionable health view.
11. **Success Feedback & Next Screen:** Surfaces all 16 required health metrics and highlights any degraded subsystem.
12. **Error / Empty / Busy / Expired Behavior:** Distinguishes missing telemetry ('unknown') from healthy zero ('0').
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_superadmin_callbacks.py::test_s_health_metrics_and_states`

### 52. `CB_SUPER_PLANS` — 💎 Manage Plans & Tariffs / 💎 مدیریت پلنها و تعرفهها

1. **Stable Action ID:** `CB_SUPER_PLANS`
2. **Visible Labels:** **FA:** `💎 مدیریت پلنها و تعرفهها` | **EN:** `💎 Manage Plans & Tariffs`
3. **Callback / Trigger Pattern:** `s:plans | s:plan:<id> | s:plan_new | s:plan_e:<id>:<field> | s:plan_t:<id> | s:plan_free:<id> | s:plan_d:<id>`
4. **Roles Allowed:** `superadmin`
5. **Ownership & Entitlement Requirements:** Server-side role == 'superadmin'.
6. **Parent Screen → Destination Screen:** `Superadmin Dashboard` → `Plans Admin List / Plan Editor / Guided Input`
7. **Required Input & Validation:** Bilingual names/descriptions, duration_days, daily_post_limit, max_sources, max_channels, daily_test_limit, price_tmn, price_usd, sort_order.
8. **Domain Operation Invoked:** `superadmin.plans.manage_plans`
9. **Database Entities (Read / Write):** **Read:** `plans, users, pay_requests` | **Write:** `plans, kv_cache`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Creates, edits, toggles, marks free plan, or safely deletes/deactivates plans.
12. **Error / Empty / Busy / Expired Behavior:** s:plan_d deactivates instead of physically deleting when users or payments reference the plan.
13. **Destructive & Confirmation Policy:** `Destructive=True` — s:plan_d requires confirmation and enforces referential safety.
14. **Automated Verification Test:** `tests/ui/test_superadmin_callbacks.py::test_s_plans_crud_and_safe_delete`

### 53. `CB_SUPER_DISCOUNTS` — 🏷 Manage Discount Codes / 🏷 مدیریت کدهای تخفیف

1. **Stable Action ID:** `CB_SUPER_DISCOUNTS`
2. **Visible Labels:** **FA:** `🏷 مدیریت کدهای تخفیف` | **EN:** `🏷 Manage Discount Codes`
3. **Callback / Trigger Pattern:** `s:discs | s:disc:<code> | s:disc_new | s:disc_e:<code>:<field> | s:disc_t:<code> | s:disc_d:<code>`
4. **Roles Allowed:** `superadmin`
5. **Ownership & Entitlement Requirements:** Server-side role == 'superadmin'.
6. **Parent Screen → Destination Screen:** `Superadmin Dashboard` → `Discounts List / Discount Detail / Guided Input`
7. **Required Input & Validation:** Code (3-32 chars), percentage (1-100), expiry date/time, max_uses (0=unlimited).
8. **Domain Operation Invoked:** `superadmin.discounts.manage_discounts`
9. **Database Entities (Read / Write):** **Read:** `discounts` | **Write:** `discounts, kv_cache`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Creates, edits, toggles, or deletes discount codes.
12. **Error / Empty / Busy / Expired Behavior:** Duplicate codes or invalid percentages rejected.
13. **Destructive & Confirmation Policy:** `Destructive=True` — s:disc_d requires confirmation before deleting discount code.
14. **Automated Verification Test:** `tests/ui/test_superadmin_callbacks.py::test_s_discounts_crud`

### 54. `CB_SUPER_USERS_AND_ADMINS` — 👥 Manage Users & Channel Admins / 👥 مدیریت کاربران و ادمینها

1. **Stable Action ID:** `CB_SUPER_USERS_AND_ADMINS`
2. **Visible Labels:** **FA:** `👥 مدیریت کاربران و ادمینها` | **EN:** `👥 Manage Users & Channel Admins`
3. **Callback / Trigger Pattern:** `s:users:<p> | s:admins:<p> | s:user:<uid>:<p> | s:uplan:<uid> | s:urevoke:<uid> | s:uban:<uid> | s:umsg:<uid> | s:ureport:<uid> | s:ufree:<uid> | s:utests:<uid> | s:uposts:<uid>`
4. **Roles Allowed:** `superadmin`
5. **Ownership & Entitlement Requirements:** Server-side role == 'superadmin'; cannot ban/lock out self or other superadmins.
6. **Parent Screen → Destination Screen:** `Superadmin Dashboard` → `Paginated Users/Admins / User Inspector / Action Confirmation`
7. **Required Input & Validation:** Valid user_id, plan selection for s:uplan, message content for s:umsg.
8. **Domain Operation Invoked:** `superadmin.users.manage_user_account`
9. **Database Entities (Read / Write):** **Read:** `users, plans, channels, usage, wallet, pay_requests` | **Write:** `users, usage, plan_changes, notifications`
10. **External Side Effects:** Outbox notification to target user on plan assignment/revocation or direct message.
11. **Success Feedback & Next Screen:** Executes audited admin action (assign/revoke plan, ban/unban, direct msg, reset free eligibility, reset test/post usage).
12. **Error / Empty / Busy / Expired Behavior:** Blocks self-ban or peer-superadmin lockout.
13. **Destructive & Confirmation Policy:** `Destructive=True` — s:urevoke and s:uban require explicit confirmation and write audit records.
14. **Automated Verification Test:** `tests/ui/test_superadmin_callbacks.py::test_s_users_management_and_superadmin_protection`

### 55. `CB_SUPER_MODELS` — 🧠 Manage Global AI Models / 🧠 مدیریت مدلهای هوش مصنوعی سراسری

1. **Stable Action ID:** `CB_SUPER_MODELS`
2. **Visible Labels:** **FA:** `🧠 مدیریت مدلهای هوش مصنوعی سراسری` | **EN:** `🧠 Manage Global AI Models`
3. **Callback / Trigger Pattern:** `s:models | s:model:<id> | s:model_add | s:models_test | s:model_test:<id> | s:model_t:<id> | s:model_e:<id> | s:model_d:<id> | s:model_test_pick:<id>`
4. **Roles Allowed:** `superadmin`
5. **Ownership & Entitlement Requirements:** Server-side role == 'superadmin'.
6. **Parent Screen → Destination Screen:** `Superadmin Dashboard` → `Global Models List / Model Inspector / Probe Report`
7. **Required Input & Validation:** Provider type, base URL, API key (encrypted at rest), model ID, priority, temperature, max_tokens.
8. **Domain Operation Invoked:** `superadmin.models.manage_global_models`
9. **Database Entities (Read / Write):** **Read:** `ai_models` | **Write:** `ai_models, kv_cache`
10. **External Side Effects:** Live HTTP health probes to AI providers on s:model_test / s:models_test.
11. **Success Feedback & Next Screen:** Manages routing priority, default test model (`s:model_test_pick`), active state, and health status.
12. **Error / Empty / Busy / Expired Behavior:** Redacts API keys in all probe failure diagnostics.
13. **Destructive & Confirmation Policy:** `Destructive=True` — s:model_d requires confirmation before deleting global model.
14. **Automated Verification Test:** `tests/ui/test_superadmin_callbacks.py::test_s_models_crud_and_probes`

### 56. `CB_SUPER_PAYMENTS` — 💰 Manage Payments, Approve, Reject & Refund / 💰 مدیریت پرداختها، تایید، رد و استرداد وجه (Refund)

1. **Stable Action ID:** `CB_SUPER_PAYMENTS`
2. **Visible Labels:** **FA:** `💰 مدیریت پرداختها، تایید، رد و استرداد وجه (Refund)` | **EN:** `💰 Manage Payments, Approve, Reject & Refund`
3. **Callback / Trigger Pattern:** `s:pays | s:pay_rc:<id> | s:pay_ok:<id> | s:pay_no:<id> | s:pay_refund:<id>`
4. **Roles Allowed:** `superadmin`
5. **Ownership & Entitlement Requirements:** Server-side role == 'superadmin'.
6. **Parent Screen → Destination Screen:** `Superadmin Dashboard` → `Pending/Recent Payments List -> Receipt Inspector -> Approval/Rejection/Refund Confirmation`
7. **Required Input & Validation:** Callback ^s:pay_(rc|ok|no|refund):\d+$; optional rejection/refund reason.
8. **Domain Operation Invoked:** `payments.review_or_refund_payment`
9. **Database Entities (Read / Write):** **Read:** `pay_requests, users, plans, discounts, wallet, referrals` | **Write:** `pay_requests, users, discounts, wallet, credit_log, plan_changes, referrals, notifications`
10. **External Side Effects:** Durable Outbox notifications to customer, referrer, and superadmin audit log.
11. **Success Feedback & Next Screen:** Atomically claims payment, revalidates quote/discount/wallet, updates ledger & plan, grants idempotent referral reward, or executes policy-compliant refund.
12. **Error / Empty / Busy / Expired Behavior:** Concurrent second click on s:pay_ok or repeated s:pay_refund is safely rejected as already processed.
13. **Destructive & Confirmation Policy:** `Destructive=True` — s:pay_ok, s:pay_no, and s:pay_refund require confirmation and write immutable ledger/audit entries.
14. **Automated Verification Test:** `tests/money/test_payment_approval_and_refund_concurrency.py::test_atomic_approval_and_refund`

### 57. `CB_SUPER_MANDATORY_CHANNEL` — 🔐 Mandatory Membership Configuration (FA & EN) / 🔐 تنظیمات عضویت اجباری (فارسی و انگلیسی)

1. **Stable Action ID:** `CB_SUPER_MANDATORY_CHANNEL`
2. **Visible Labels:** **FA:** `🔐 تنظیمات عضویت اجباری (فارسی و انگلیسی)` | **EN:** `🔐 Mandatory Membership Configuration (FA & EN)`
3. **Callback / Trigger Pattern:** `s:mand | s:mand_t:<lang> | s:mand_e:<lang>:id | s:mand_e:<lang>:user`
4. **Roles Allowed:** `superadmin`
5. **Ownership & Entitlement Requirements:** Server-side role == 'superadmin'.
6. **Parent Screen → Destination Screen:** `Superadmin Dashboard` → `Mandatory Membership Config Screen`
7. **Required Input & Validation:** Language in {'fa', 'en'}; valid channel negative ID or @username.
8. **Domain Operation Invoked:** `superadmin.membership.configure_mandatory_channels`
9. **Database Entities (Read / Write):** **Read:** `settings` | **Write:** `settings, kv_cache`
10. **External Side Effects:** Verifies bot can query target channel via Telegram getChat.
11. **Success Feedback & Next Screen:** Updates per-language mandatory channel ID, username, and enforcement toggle.
12. **Error / Empty / Busy / Expired Behavior:** Warns if bot is not an admin in the configured mandatory channel.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_superadmin_callbacks.py::test_s_mand_configuration`

### 58. `CB_SUPER_TEXTS` — 📝 System Text & Localization Editor / 📝 ویرایشگر متون سیستم و بومیسازی

1. **Stable Action ID:** `CB_SUPER_TEXTS`
2. **Visible Labels:** **FA:** `📝 ویرایشگر متون سیستم و بومیسازی` | **EN:** `📝 System Text & Localization Editor`
3. **Callback / Trigger Pattern:** `s:texts | s:txt:<key>:<lang> | s:txtr:<key>:<lang>`
4. **Roles Allowed:** `superadmin`
5. **Ownership & Entitlement Requirements:** Server-side role == 'superadmin'.
6. **Parent Screen → Destination Screen:** `Superadmin Dashboard` → `Localization Key List / Key Editor / Reset Confirmation`
7. **Required Input & Validation:** Valid localization key, lang in {'fa', 'en'}, HTML-validated replacement string.
8. **Domain Operation Invoked:** `superadmin.i18n.edit_or_reset_system_text`
9. **Database Entities (Read / Write):** **Read:** `settings` | **Write:** `settings, kv_cache`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Saves validated custom localization string or resets key (`s:txtr`) to built-in default.
12. **Error / Empty / Busy / Expired Behavior:** Unbalanced/unsafe HTML tags rejected before saving.
13. **Destructive & Confirmation Policy:** `Destructive=False` — s:txtr confirms before resetting customized text to default.
14. **Automated Verification Test:** `tests/ui/test_superadmin_callbacks.py::test_s_texts_edit_html_validation_and_reset`

### 59. `CB_SUPER_BROADCAST` — 📣 Broadcast Messaging / 📣 ارسال پیام همگانی (Broadcast)

1. **Stable Action ID:** `CB_SUPER_BROADCAST`
2. **Visible Labels:** **FA:** `📣 ارسال پیام همگانی (Broadcast)` | **EN:** `📣 Broadcast Messaging`
3. **Callback / Trigger Pattern:** `s:bc | s:bc:confirm`
4. **Roles Allowed:** `superadmin`
5. **Ownership & Entitlement Requirements:** Server-side role == 'superadmin'.
6. **Parent Screen → Destination Screen:** `Superadmin Dashboard` → `Broadcast Compose -> Preview & Confirmation -> Progress Tracker`
7. **Required Input & Validation:** Target segment (all/fa/en/admins), message text or media.
8. **Domain Operation Invoked:** `superadmin.broadcast.enqueue_broadcast_job`
9. **Database Entities (Read / Write):** **Read:** `users, settings` | **Write:** `notifications, settings`
10. **External Side Effects:** Background rate-limited batch delivery via Outbox without blocking bot event loop.
11. **Success Feedback & Next Screen:** Shows preview, requires explicit confirmation, and reports sent/failed/blocked progress.
12. **Error / Empty / Busy / Expired Behavior:** Prevents overlapping concurrent broadcast jobs.
13. **Destructive & Confirmation Policy:** `Destructive=True` — Mandatory preview and explicit confirmation before dispatching broadcast.
14. **Automated Verification Test:** `tests/ui/test_superadmin_callbacks.py::test_s_bc_preview_confirm_and_nonblocking_rate_limit`

### 60. `CB_SUPER_GLOBAL_AUTOMATION` — 🛑 Global Platform Automation Switch / 🛑 کلید اتوماسیون سراسری پلتفرم

1. **Stable Action ID:** `CB_SUPER_GLOBAL_AUTOMATION`
2. **Visible Labels:** **FA:** `🛑 کلید اتوماسیون سراسری پلتفرم` | **EN:** `🛑 Global Platform Automation Switch`
3. **Callback / Trigger Pattern:** `s:auto | s:auto:confirm`
4. **Roles Allowed:** `superadmin`
5. **Ownership & Entitlement Requirements:** Server-side role == 'superadmin'.
6. **Parent Screen → Destination Screen:** `Superadmin Dashboard` → `Global Automation Confirmation -> Superadmin Dashboard`
7. **Required Input & Validation:** Exact callback 's:auto' / 's:auto:confirm'.
8. **Domain Operation Invoked:** `superadmin.settings.toggle_global_automation`
9. **Database Entities (Read / Write):** **Read:** `settings` | **Write:** `settings`
10. **External Side Effects:** Telegram editMessageText; pauses/resumes scheduler ticks globally without altering per-channel enabled flags.
11. **Success Feedback & Next Screen:** Updates global automation state and logs operator audit event.
12. **Error / Empty / Busy / Expired Behavior:** Requires explicit confirmation before pausing global automation.
13. **Destructive & Confirmation Policy:** `Destructive=True` — Explicit confirmation required before shutting down global automation.
14. **Automated Verification Test:** `tests/ui/test_superadmin_callbacks.py::test_s_auto_distinct_from_channel_toggle`

### 61. `CB_SUPER_REFERRAL_ECONOMICS` — 🤝 Referral Economics Configuration / 🤝 تنظیمات اقتصادی رفرال (درصد و سقف پاداش)

1. **Stable Action ID:** `CB_SUPER_REFERRAL_ECONOMICS`
2. **Visible Labels:** **FA:** `🤝 تنظیمات اقتصادی رفرال (درصد و سقف پاداش)` | **EN:** `🤝 Referral Economics Configuration`
3. **Callback / Trigger Pattern:** `s:ref | s:ref:pct | s:ref:cap_tmn | s:ref:cap_usd`
4. **Roles Allowed:** `superadmin`
5. **Ownership & Entitlement Requirements:** Server-side role == 'superadmin'.
6. **Parent Screen → Destination Screen:** `Superadmin Dashboard` → `Referral Economics Config Screen`
7. **Required Input & Validation:** Reward percentage bounded 0..50 (default 20%); cap >= 0 per currency (0 = unlimited).
8. **Domain Operation Invoked:** `superadmin.referrals.update_referral_economics`
9. **Database Entities (Read / Write):** **Read:** `settings` | **Write:** `settings`
10. **External Side Effects:** Telegram editMessageText.
11. **Success Feedback & Next Screen:** Saves updated referral reward % and currency-specific caps (TMN & USD).
12. **Error / Empty / Busy / Expired Behavior:** Rejects percentage < 0 or > 50 or negative cap values.
13. **Destructive & Confirmation Policy:** `Destructive=False` — None
14. **Automated Verification Test:** `tests/ui/test_superadmin_callbacks.py::test_s_ref_bounds_and_currency_caps`
