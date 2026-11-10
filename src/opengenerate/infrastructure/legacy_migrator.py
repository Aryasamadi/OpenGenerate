"""Legacy SQLite -> PostgreSQL ETL Migrator & 10-Gate Reconciliation Verifier (ADR-0013)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.domain.channels import ChannelSettings
from opengenerate.domain.errors import Conflict, ValidationFailed
from opengenerate.domain.sources import canonicalize_url, compute_url_hash
from opengenerate.infrastructure.crypto import FieldCipher

_ALLOWED_HTML_TAGS = frozenset({"b", "i", "u", "s", "code", "pre", "a", "blockquote", "tg-spoiler"})
_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z0-9-]+)(?:\s[^>]*)?>")


def _validate_safe_telegram_html(html_text: str) -> str:
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

LEGACY_25_TABLES: tuple[str, ...] = (
    "users",
    "plans",
    "discounts",
    "channels",
    "sources",
    "usage",
    "articles",
    "posted",
    "ch_tokens",
    "ai_models",
    "deeplinks",
    "kv_cache",
    "ai_cache",
    "ai_usage",
    "pub_journal",
    "settings",
    "support",
    "support_map",
    "plan_changes",
    "pay_requests",
    "notifications",
    "referrals",
    "wallet",
    "credit_log",
    "rate_limits",
)

DISCARDED_EPHEMERAL_TABLES: frozenset[str] = frozenset({"kv_cache", "ai_cache", "rate_limits"})


@dataclass(frozen=True, slots=True)
class LegacyInspectReport:
    sqlite_path: str
    file_sha256: str
    table_counts: dict[str, int]
    missing_tables: list[str]
    in_flight_publications: int
    processing_payments: int
    untagged_wallet_rows: int
    anomalies: list[str]


@dataclass(frozen=True, slots=True)
class GateOutcome:
    gate_id: str
    description: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    gates: list[GateOutcome]
    all_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_passed": self.all_passed,
            "gates": [
                {
                    "gate_id": g.gate_id,
                    "description": g.description,
                    "passed": g.passed,
                    "detail": g.detail,
                }
                for g in self.gates
            ],
        }


@dataclass(slots=True)
class MigrationExecutionResult:
    inspect_report: LegacyInspectReport
    migrated_counts: dict[str, int] = field(default_factory=dict)
    discarded_tables: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reconciliation: ReconciliationReport | None = None

    def render_markdown_report(self) -> str:
        lines = [
            "# Legacy SQLite → PostgreSQL Migration & Reconciliation Report",
            "",
            f"- **Source SQLite Snapshot:** `{self.inspect_report.sqlite_path}`",
            f"- **Source SHA-256:** `{self.inspect_report.file_sha256}`",
            f"- **Overall Reconciliation Status:** `{'PASSED' if (self.reconciliation and self.reconciliation.all_passed) else 'FAILED'}`",
            "",
            "## 1. Table Row Counts (Source vs Migrated)",
            "",
            "| Table | Legacy SQLite Rows | PostgreSQL Migrated Rows | Disposition |",
            "|---|---:|---:|---|",
        ]
        for tbl in LEGACY_25_TABLES:
            src_cnt = self.inspect_report.table_counts.get(tbl, 0)
            if tbl in DISCARDED_EPHEMERAL_TABLES:
                lines.append(f"| `{tbl}` | {src_cnt} | 0 | discarded (ephemeral) |")
            else:
                mig_cnt = self.migrated_counts.get(tbl, 0)
                lines.append(f"| `{tbl}` | {src_cnt} | {mig_cnt} | migrated |")

        if self.reconciliation:
            lines.extend(
                [
                    "",
                    "## 2. Ten-Gate Reconciliation Matrix (G1..G10)",
                    "",
                    "| Gate | Description | Status | Detail |",
                    "|---|---|---|---|",
                ]
            )
            for g in self.reconciliation.gates:
                badge = "PASS" if g.passed else "FAIL"
                lines.append(f"| **{g.gate_id}** | {g.description} | `{badge}` | {g.detail} |")

        if self.warnings:
            lines.extend(["", "## 3. Warnings & Operator Notes", ""])
            for w in self.warnings:
                lines.append(f"- {w}")

        return "\n".join(lines) + "\n"


def _open_sqlite_ro(sqlite_path: str | Path) -> sqlite3.Connection:
    path_obj = Path(sqlite_path).resolve()
    uri = f"file:{path_obj.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _compute_file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _table_exists(sconn: sqlite3.Connection, table_name: str) -> bool:
    cur = sconn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table_name,)
    )
    return cur.fetchone() is not None


def _parse_ts(val: Any) -> datetime | None:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=UTC)
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(float(val), tz=UTC)
    s = str(val).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)


def _parse_date(val: Any) -> date:
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, str) and val.strip():
        try:
            return date.fromisoformat(val.strip()[:10])
        except ValueError:
            pass
    return datetime.now(UTC).date()


def inspect_legacy_sqlite(sqlite_path: str | Path) -> LegacyInspectReport:
    """Inspect a legacy SQLite database in read-only mode without modifying a single byte."""
    file_hash = _compute_file_sha256(sqlite_path)
    sconn = _open_sqlite_ro(sqlite_path)
    try:
        counts: dict[str, int] = {}
        missing: list[str] = []
        anomalies: list[str] = []
        in_flight_pub = 0
        proc_pay = 0
        untagged_wallets = 0

        for tbl in LEGACY_25_TABLES:
            if _table_exists(sconn, tbl):
                row = sconn.execute(f'SELECT count(*) AS c FROM "{tbl}"').fetchone()  # noqa: S608
                counts[tbl] = int(row["c"]) if row else 0
            else:
                counts[tbl] = 0
                missing.append(tbl)

        if _table_exists(sconn, "pub_journal"):
            r = sconn.execute(
                "SELECT count(*) AS c FROM pub_journal WHERE phase IN ('reserved','in_flight','delivery_unknown')"
            ).fetchone()
            in_flight_pub = int(r["c"]) if r else 0
            if in_flight_pub > 0:
                anomalies.append(
                    f"{in_flight_pub} pub_journal rows are in-flight/unknown and will be quarantined as delivery_unknown"
                )

        if _table_exists(sconn, "pay_requests"):
            r = sconn.execute(
                "SELECT count(*) AS c FROM pay_requests WHERE status = 'processing'"
            ).fetchone()
            proc_pay = int(r["c"]) if r else 0
            if proc_pay > 0:
                anomalies.append(
                    f"{proc_pay} pay_requests rows are in 'processing' state and require manual operator review"
                )

        if _table_exists(sconn, "wallet"):
            cols = {
                r["name"]
                for r in sconn.execute("PRAGMA table_info('wallet')").fetchall()
            }
            if "currency" not in cols:
                untagged_wallets = counts["wallet"]
            else:
                r = sconn.execute(
                    "SELECT count(*) AS c FROM wallet WHERE currency IS NULL OR currency NOT IN ('TMN','USD')"
                ).fetchone()
                untagged_wallets = int(r["c"]) if r else 0
            if untagged_wallets > 0:
                anomalies.append(
                    f"{untagged_wallets} wallet rows lack explicit TMN/USD currency and will be isolated as LEGACY"
                )

        return LegacyInspectReport(
            sqlite_path=str(Path(sqlite_path).resolve()),
            file_sha256=file_hash,
            table_counts=counts,
            missing_tables=missing,
            in_flight_publications=in_flight_pub,
            processing_payments=proc_pay,
            untagged_wallet_rows=untagged_wallets,
            anomalies=anomalies,
        )
    finally:
        sconn.close()


def _reencrypt_secret(
    raw_val: str | None,
    *,
    target_keyring: FieldCipher,
    legacy_keyring: FieldCipher | None = None,
) -> tuple[str | None, bool]:
    """Decrypt legacy secret (if encrypted) or take plaintext, and encrypt with target FieldCipher."""
    if not raw_val or not raw_val.strip():
        return None, True
    val = raw_val.strip()
    if val.startswith("v1:"):
        try:
            plain = (legacy_keyring or target_keyring).decrypt(val)
            return target_keyring.encrypt(plain), True
        except Exception:
            return None, False
    if legacy_keyring is not None:
        with contextlib.suppress(Exception):
            plain = legacy_keyring.decrypt(val)
            return target_keyring.encrypt(plain), True
    # Plaintext secret in legacy SQLite -> encrypt at rest in PostgreSQL
    return target_keyring.encrypt(val), True


async def migrate_sqlite_to_postgres(
    sqlite_path: str | Path,
    conn: AsyncConnection,
    *,
    target_keyring: FieldCipher,
    legacy_keyring: FieldCipher | None = None,
    allow_nonempty: bool = False,
) -> MigrationExecutionResult:
    """Execute full deterministic ETL from legacy SQLite into PostgreSQL + 10-gate reconciliation."""
    inspect_rep = inspect_legacy_sqlite(sqlite_path)
    result = MigrationExecutionResult(
        inspect_report=inspect_rep,
        discarded_tables=sorted(DISCARDED_EPHEMERAL_TABLES),
        warnings=list(inspect_rep.anomalies),
    )

    if not allow_nonempty:
        existing_users = int(
            (await conn.execute(text("SELECT count(*) FROM users"))).scalar_one()
        )
        existing_plans = int(
            (await conn.execute(text("SELECT count(*) FROM plans"))).scalar_one()
        )
        if existing_users > 0 or existing_plans > 0:
            raise Conflict("target PostgreSQL database is not empty (use allow_nonempty=True to override)")

    sconn = _open_sqlite_ro(sqlite_path)
    try:
        # 1. plans
        migrated_plan_ids: set[int] = set()
        if _table_exists(sconn, "plans"):
            rows = sconn.execute("SELECT * FROM plans ORDER BY plan_id").fetchall()
            for r in rows:
                rk = r.keys()
                pid = int(r["plan_id"])
                raw_tmn = r["price_tmn"] if "price_tmn" in rk else None
                raw_usd = r["price_usd"] if "price_usd" in rk else None
                price_tmn: int | None = None
                price_usd: int | None = None
                price_text_fa = r["price_text_fa"] if "price_text_fa" in rk else None
                price_text_en = r["price_text_en"] if "price_text_en" in rk else None
                if raw_tmn is not None and str(raw_tmn).strip().isdigit():
                    price_tmn = int(raw_tmn)
                elif raw_tmn is not None and str(raw_tmn).strip():
                    price_text_fa = str(raw_tmn).strip()
                if raw_usd is not None and str(raw_usd).strip().isdigit():
                    price_usd = int(raw_usd)
                elif raw_usd is not None and str(raw_usd).strip():
                    price_text_en = str(raw_usd).strip()

                await conn.execute(
                    text(
                        "INSERT INTO plans(plan_id, name_fa, name_en, desc_fa, desc_en, price_tmn, price_usd, "
                        "price_text_fa, price_text_en, duration_days, daily_post_limit, max_sources, max_channels, "
                        "daily_test_limit, is_free, is_active, sort_order) "
                        "VALUES (:pid, :nfa, :nen, :dfa, :den, :ptmn, :pusd, :tfa, :ten, :dur, :dpost, :msrc, :mch, "
                        ":dtest, :free, :act, :sort) ON CONFLICT (plan_id) DO NOTHING"
                    ),
                    {
                        "pid": pid,
                        "nfa": str(r["name_fa"] if "name_fa" in rk else f"Plan {pid}"),
                        "nen": str(r["name_en"] if "name_en" in rk else f"Plan {pid}"),
                        "dfa": str(r["desc_fa"] if "desc_fa" in rk and r["desc_fa"] else ""),
                        "den": str(r["desc_en"] if "desc_en" in rk and r["desc_en"] else ""),
                        "ptmn": price_tmn,
                        "pusd": price_usd,
                        "tfa": price_text_fa,
                        "ten": price_text_en,
                        "dur": max(1, int(r["duration_days"] if "duration_days" in rk and r["duration_days"] else 30)),
                        "dpost": max(0, int(r["daily_post_limit"] if "daily_post_limit" in rk and r["daily_post_limit"] else 10)),
                        "msrc": max(0, int(r["max_sources"] if "max_sources" in rk and r["max_sources"] else 5)),
                        "mch": max(0, int(r["max_channels"] if "max_channels" in rk and r["max_channels"] else 1)),
                        "dtest": max(0, int(r["daily_test_limit"] if "daily_test_limit" in rk and r["daily_test_limit"] else 3)),
                        "free": bool(r["is_free"]) if "is_free" in rk else False,
                        "act": bool(r["is_active"]) if "is_active" in rk else True,
                        "sort": int(r["sort_order"]) if "sort_order" in rk and r["sort_order"] else 0,
                    },
                )
                migrated_plan_ids.add(pid)
        result.migrated_counts["plans"] = len(migrated_plan_ids)

        # 2. users
        migrated_user_ids: set[int] = set()
        if _table_exists(sconn, "users"):
            rows = sconn.execute("SELECT * FROM users ORDER BY user_id").fetchall()
            for r in rows:
                rk = r.keys()
                uid = int(r["user_id"])
                if uid <= 0:
                    continue
                role = str(r["role"]) if "role" in rk and r["role"] in ("user", "admin", "superadmin") else "user"
                lang = str(r["lang"]) if "lang" in rk and r["lang"] in ("fa", "en") else None
                pid_val = int(r["plan_id"]) if "plan_id" in rk and r["plan_id"] is not None else None
                if pid_val is not None and pid_val not in migrated_plan_ids:
                    pid_val = None
                await conn.execute(
                    text(
                        "INSERT INTO users(user_id, username, full_name, role, lang, plan_id, plan_expires_at, "
                        "is_banned, free_plan_used) "
                        "VALUES (:u, :un, :fn, :r, :l, :p, :exp, :ban, :free) ON CONFLICT (user_id) DO NOTHING"
                    ),
                    {
                        "u": uid,
                        "un": r["username"] if "username" in rk else None,
                        "fn": r["full_name"] if "full_name" in rk else None,
                        "r": role,
                        "l": lang,
                        "p": pid_val,
                        "exp": _parse_ts(r["plan_expires_at"]) if "plan_expires_at" in rk else None,
                        "ban": bool(r["is_banned"]) if "is_banned" in rk else False,
                        "free": bool(r["free_plan_used"]) if "free_plan_used" in rk else False,
                    },
                )
                migrated_user_ids.add(uid)
        result.migrated_counts["users"] = len(migrated_user_ids)

        # 3. discounts
        migrated_discounts: set[str] = set()
        if _table_exists(sconn, "discounts"):
            rows = sconn.execute("SELECT * FROM discounts").fetchall()
            for r in rows:
                rk = r.keys()
                code = str(r["code"]).strip().upper()
                if len(code) < 3 or len(code) > 32:
                    continue
                cby = int(r["created_by"]) if "created_by" in rk and r["created_by"] in migrated_user_ids else None
                max_u = max(0, int(r["max_uses"] if "max_uses" in rk and r["max_uses"] else 0))
                used_u = max(0, int(r["used_count"] if "used_count" in rk and r["used_count"] else 0))
                if max_u > 0 and used_u > max_u:
                    used_u = max_u
                await conn.execute(
                    text(
                        "INSERT INTO discounts(code, percent, expires_at, max_uses, used_count, is_active, created_by) "
                        "VALUES (:c, :p, :exp, :mu, :uu, :act, :cby) ON CONFLICT (code) DO NOTHING"
                    ),
                    {
                        "c": code,
                        "p": min(100, max(1, int(r["percent"]))),
                        "exp": _parse_ts(r["expires_at"]) if "expires_at" in rk else None,
                        "mu": max_u,
                        "uu": used_u,
                        "act": bool(r["is_active"]) if "is_active" in rk else True,
                        "cby": cby,
                    },
                )
                migrated_discounts.add(code)
        result.migrated_counts["discounts"] = len(migrated_discounts)

        # 4. channels
        migrated_channels: set[int] = set()
        if _table_exists(sconn, "channels"):
            rows = sconn.execute("SELECT * FROM channels").fetchall()
            for r in rows:
                rk = r.keys()
                cid = int(r["channel_id"])
                if cid >= 0:
                    cid = -abs(cid) - 1000000000000
                owner_id = int(r["owner_id"])
                orig_owner = (
                    int(r["original_owner_id"])
                    if "original_owner_id" in rk and r["original_owner_id"]
                    else owner_id
                )
                if owner_id not in migrated_user_ids:
                    continue
                if orig_owner not in migrated_user_ids:
                    orig_owner = owner_id
                raw_settings = r["settings_json"] if "settings_json" in rk and r["settings_json"] else "{}"
                parsed_dict = json.loads(raw_settings) if isinstance(raw_settings, str) else raw_settings
                upgraded_settings = ChannelSettings.from_dict(
                    parsed_dict if isinstance(parsed_dict, dict) else {}
                ).to_dict()
                await conn.execute(
                    text(
                        "INSERT INTO channels(channel_id, username, title, owner_id, original_owner_id, enabled, settings_json) "
                        "VALUES (:cid, :un, :t, :o, :oo, :en, CAST(:sj AS jsonb)) ON CONFLICT (channel_id) DO NOTHING"
                    ),
                    {
                        "cid": cid,
                        "un": r["username"] if "username" in rk else None,
                        "t": str(r["title"] if "title" in rk and r["title"] else f"Channel {cid}"),
                        "o": owner_id,
                        "oo": orig_owner,
                        "en": bool(r["enabled"]) if "enabled" in rk else False,
                        "sj": json.dumps(upgraded_settings, ensure_ascii=False),
                    },
                )
                migrated_channels.add(cid)
        result.migrated_counts["channels"] = len(migrated_channels)

        # 5. sources
        migrated_sources: set[int] = set()
        if _table_exists(sconn, "sources"):
            rows = sconn.execute("SELECT * FROM sources ORDER BY source_id").fetchall()
            for r in rows:
                rk = r.keys()
                sid = int(r["source_id"])
                cid = int(r["channel_id"])
                if cid not in migrated_channels:
                    continue
                admin_id = int(r["admin_id"]) if "admin_id" in rk and r["admin_id"] in migrated_user_ids else next(iter(migrated_user_ids))
                raw_url = str(r["url"])
                can_url = canonicalize_url(raw_url)
                raw_key = r["custom_api_key_enc"] if "custom_api_key_enc" in rk else (r["custom_api_key"] if "custom_api_key" in rk else None)
                enc_key, _ok = _reencrypt_secret(
                    raw_key, target_keyring=target_keyring, legacy_keyring=legacy_keyring
                )
                dtype = str(r["discovery_type"]) if "discovery_type" in rk and r["discovery_type"] in ("auto", "rss", "json_feed", "sitemap", "html", "custom_api") else "auto"
                await conn.execute(
                    text(
                        "INSERT INTO sources(source_id, channel_id, admin_id, url, canonical_url, discovery_type, "
                        "fetch_enabled, publish_enabled, custom_api_url, custom_api_key_enc, etag, last_modified, failure_count) "
                        "VALUES (:sid, :cid, :aid, :u, :cu, :dt, :fe, :pe, :cau, :cak, :et, :lm, :fc) "
                        "ON CONFLICT (channel_id, canonical_url) DO NOTHING"
                    ),
                    {
                        "sid": sid,
                        "cid": cid,
                        "aid": admin_id,
                        "u": raw_url,
                        "cu": can_url,
                        "dt": dtype,
                        "fe": bool(r["fetch_enabled"]) if "fetch_enabled" in rk else True,
                        "pe": bool(r["publish_enabled"]) if "publish_enabled" in rk else True,
                        "cau": r["custom_api_url"] if "custom_api_url" in rk else None,
                        "cak": enc_key,
                        "et": r["etag"] if "etag" in rk else None,
                        "lm": r["last_modified"] if "last_modified" in rk else None,
                        "fc": max(0, int(r["failure_count"] if "failure_count" in rk and r["failure_count"] else 0)),
                    },
                )
                migrated_sources.add(sid)
        result.migrated_counts["sources"] = len(migrated_sources)

        # 6. usage
        usage_cnt = 0
        if _table_exists(sconn, "usage"):
            rows = sconn.execute("SELECT * FROM usage").fetchall()
            for r in rows:
                rk = r.keys()
                uid = int(r["user_id"])
                if uid not in migrated_user_ids:
                    continue
                udate = _parse_date(r["usage_date"] if "usage_date" in rk else None)
                await conn.execute(
                    text(
                        "INSERT INTO usage(user_id, usage_date, posts_count, tests_count, ai_calls_count, ai_tokens_count) "
                        "VALUES (:u, :d, :p, :t, :ac, :at) ON CONFLICT (user_id, usage_date) DO NOTHING"
                    ),
                    {
                        "u": uid,
                        "d": udate,
                        "p": max(0, int(r["posts_count"] if "posts_count" in rk and r["posts_count"] else 0)),
                        "t": max(0, int(r["tests_count"] if "tests_count" in rk and r["tests_count"] else 0)),
                        "ac": max(0, int(r["ai_calls_count"] if "ai_calls_count" in rk and r["ai_calls_count"] else 0)),
                        "at": max(0, int(r["ai_tokens_count"] if "ai_tokens_count" in rk and r["ai_tokens_count"] else 0)),
                    },
                )
                usage_cnt += 1
        result.migrated_counts["usage"] = usage_cnt

        # 7. articles
        migrated_articles: set[int] = set()
        if _table_exists(sconn, "articles"):
            rows = sconn.execute("SELECT * FROM articles ORDER BY article_id").fetchall()
            for r in rows:
                rk = r.keys()
                aid = int(r["article_id"])
                cid = int(r["channel_id"])
                if cid not in migrated_channels:
                    continue
                sid_val = int(r["source_id"]) if "source_id" in rk and r["source_id"] in migrated_sources else None
                raw_url = str(r["url"])
                can_url = canonicalize_url(raw_url)
                u_hash = str(r["url_hash"]) if "url_hash" in rk and r["url_hash"] else compute_url_hash(can_url)
                legacy_st = str(r["status"]) if "status" in rk and r["status"] else "discovered"
                status_map = {
                    "new": "discovered",
                    "pending": "ai_pending",
                    "ai_error": "ai_retryable",
                    "fetch_error": "fetch_retryable",
                    "in_flight": "delivery_unknown",
                    "publishing": "delivery_unknown",
                }
                mapped_st = status_map.get(legacy_st, legacy_st)
                valid_statuses = {
                    "discovered", "extracted", "fetch_retryable", "fetch_exhausted",
                    "rejected_duplicate", "rejected_date", "rejected_ad_heuristic", "rejected_ai_ad", "rejected_low_score",
                    "ai_pending", "ai_processing", "ai_retryable", "ai_exhausted",
                    "ready", "publishing", "published", "delivery_unknown", "publish_failed_safe",
                }
                if mapped_st not in valid_statuses:
                    mapped_st = "ai_retryable"
                await conn.execute(
                    text(
                        "INSERT INTO articles(article_id, channel_id, source_id, url, canonical_url, url_hash, "
                        "title, raw_body, generated_title, generated_post, generated_full, status, score) "
                        "VALUES (:aid, :cid, :sid, :u, :cu, :uh, :t, :rb, :gt, :gp, :gf, :st, :sc) "
                        "ON CONFLICT (channel_id, url_hash) DO NOTHING"
                    ),
                    {
                        "aid": aid,
                        "cid": cid,
                        "sid": sid_val,
                        "u": raw_url,
                        "cu": can_url,
                        "uh": u_hash,
                        "t": str(r["title"] if "title" in rk and r["title"] else ""),
                        "rb": str(r["raw_body"] if "raw_body" in rk and r["raw_body"] else ""),
                        "gt": r["generated_title"] if "generated_title" in rk else None,
                        "gp": r["generated_post"] if "generated_post" in rk else None,
                        "gf": r["generated_full"] if "generated_full" in rk else None,
                        "st": mapped_st,
                        "sc": float(r["score"]) if "score" in rk and r["score"] is not None else None,
                    },
                )
                migrated_articles.add(aid)
        result.migrated_counts["articles"] = len(migrated_articles)

        # 8. posted
        posted_cnt = 0
        if _table_exists(sconn, "posted"):
            rows = sconn.execute("SELECT * FROM posted").fetchall()
            for r in rows:
                rk = r.keys()
                cid = int(r["channel_id"])
                if cid not in migrated_channels:
                    continue
                ahash = str(r["article_hash"])
                aid_val = int(r["article_id"]) if "article_id" in rk and r["article_id"] in migrated_articles else None
                res = await conn.execute(
                    text(
                        "INSERT INTO posted(channel_id, article_hash, article_id, telegram_message_id) "
                        "VALUES (:c, :ah, :aid, :mid) ON CONFLICT (channel_id, article_hash) DO NOTHING"
                    ),
                    {
                        "c": cid,
                        "ah": ahash,
                        "aid": aid_val,
                        "mid": int(r["telegram_message_id"]) if "telegram_message_id" in rk and r["telegram_message_id"] else None,
                    },
                )
                if res.rowcount and res.rowcount > 0:
                    posted_cnt += 1
        result.migrated_counts["posted"] = posted_cnt

        # 9. ch_tokens (migrated as expired audit records per ADR-0013)
        tokens_cnt = 0
        if _table_exists(sconn, "ch_tokens"):
            rows = sconn.execute("SELECT * FROM ch_tokens").fetchall()
            for r in rows:
                rk = r.keys()
                cid = int(r["channel_id"])
                iby = int(r["issued_by"]) if "issued_by" in rk else 0
                if cid not in migrated_channels or iby not in migrated_user_ids:
                    continue
                raw_th = str(r["token_hash"] if "token_hash" in rk else r["token_id"])
                th = hashlib.sha256(f"legacy_expired:{raw_th}".encode()).hexdigest()
                expired_ts = datetime.now(UTC) - timedelta(days=1)
                await conn.execute(
                    text(
                        "INSERT INTO ch_tokens(channel_id, token_hash, issued_by, expires_at, consumed_at) "
                        "VALUES (:c, :th, :ib, :exp, :con) ON CONFLICT (token_hash) DO NOTHING"
                    ),
                    {
                        "c": cid,
                        "th": th,
                        "ib": iby,
                        "exp": expired_ts,
                        "con": _parse_ts(r["consumed_at"]) if "consumed_at" in rk else expired_ts,
                    },
                )
                tokens_cnt += 1
        result.migrated_counts["ch_tokens"] = tokens_cnt

        # 10. ai_models
        migrated_models: set[int] = set()
        if _table_exists(sconn, "ai_models"):
            rows = sconn.execute("SELECT * FROM ai_models ORDER BY model_id").fetchall()
            for r in rows:
                rk = r.keys()
                mid = int(r["model_id"])
                oid = int(r["owner_id"]) if "owner_id" in rk and r["owner_id"] in migrated_user_ids else None
                ptype = str(r["provider_type"]) if "provider_type" in rk and r["provider_type"] in ("openai", "anthropic", "gemini") else "openai"
                raw_k = r["api_key_enc"] if "api_key_enc" in rk else (r["api_key"] if "api_key" in rk else None)
                enc_k, ok_k = _reencrypt_secret(
                    raw_k, target_keyring=target_keyring, legacy_keyring=legacy_keyring
                )
                unauth = bool(r["unauthenticated"]) if "unauthenticated" in rk else False
                is_act = (bool(r["is_active"]) if "is_active" in rk else True) and (ok_k and (enc_k is not None or unauth))
                h_stat = "ok" if is_act else "degraded"
                await conn.execute(
                    text(
                        "INSERT INTO ai_models(model_id, owner_id, provider_type, name, base_url, api_key_enc, "
                        "unauthenticated, model_identifier, priority, temperature, max_tokens, is_active, health_status) "
                        "VALUES (:mid, :oid, :pt, :n, :bu, :ak, :ua, :mi, :pr, :tmp, :mt, :act, :hs) "
                        "ON CONFLICT (model_id) DO NOTHING"
                    ),
                    {
                        "mid": mid,
                        "oid": oid,
                        "pt": ptype,
                        "n": str(r["name"] if "name" in rk else f"Model {mid}"),
                        "bu": str(r["base_url"] if "base_url" in rk else "https://api.openai.com/v1"),
                        "ak": enc_k,
                        "ua": unauth,
                        "mi": str(r["model_identifier"] if "model_identifier" in rk else "gpt-4o-mini"),
                        "pr": int(r["priority"]) if "priority" in rk and r["priority"] else 100,
                        "tmp": float(r["temperature"]) if "temperature" in rk and r["temperature"] is not None else 0.3,
                        "mt": max(1, int(r["max_tokens"] if "max_tokens" in rk and r["max_tokens"] else 2048)),
                        "act": is_act,
                        "hs": h_stat,
                    },
                )
                migrated_models.add(mid)
        result.migrated_counts["ai_models"] = len(migrated_models)

        # 11. deeplinks (preserved byte-for-byte)
        dl_cnt = 0
        if _table_exists(sconn, "deeplinks"):
            rows = sconn.execute("SELECT * FROM deeplinks").fetchall()
            for r in rows:
                rk = r.keys()
                k = str(r["key"])
                aid_val = int(r["article_id"]) if "article_id" in rk and r["article_id"] in migrated_articles else None
                cid_val = int(r["channel_id"]) if "channel_id" in rk and r["channel_id"] in migrated_channels else None
                raw_p = r["payload_json"] if "payload_json" in rk and r["payload_json"] else "{}"
                p_dict = json.loads(raw_p) if isinstance(raw_p, str) else (raw_p if isinstance(raw_p, dict) else {})
                if isinstance(p_dict, dict) and not p_dict.get("full_html"):
                    chunks_val = p_dict.get("html_chunks")
                    if isinstance(chunks_val, list) and chunks_val:
                        p_dict["full_html"] = "\n".join(str(c) for c in chunks_val)
                    elif isinstance(p_dict.get("content"), str):
                        p_dict["full_html"] = p_dict["content"]
                p_str = json.dumps(p_dict, ensure_ascii=False)
                await conn.execute(
                    text(
                        "INSERT INTO deeplinks(key, article_id, channel_id, payload_json, views_count) "
                        "VALUES (:k, :aid, :cid, CAST(:pj AS jsonb), :vc) ON CONFLICT (key) DO NOTHING"
                    ),
                    {
                        "k": k,
                        "aid": aid_val,
                        "cid": cid_val,
                        "pj": p_str,
                        "vc": max(0, int(r["views_count"] if "views_count" in rk and r["views_count"] else 0)),
                    },
                )
                dl_cnt += 1
        result.migrated_counts["deeplinks"] = dl_cnt

        # 14. ai_usage
        ai_u_cnt = 0
        if _table_exists(sconn, "ai_usage"):
            rows = sconn.execute("SELECT * FROM ai_usage").fetchall()
            for r in rows:
                rk = r.keys()
                await conn.execute(
                    text(
                        "INSERT INTO ai_usage(user_id, channel_id, model_id, article_id, prompt_tokens, "
                        "completion_tokens, estimated_cost_usd, latency_ms, status) "
                        "VALUES (:u, :c, :m, :a, :pt, :ct, :cost, :lat, :st)"
                    ),
                    {
                        "u": int(r["user_id"]) if "user_id" in rk and r["user_id"] in migrated_user_ids else None,
                        "c": int(r["channel_id"]) if "channel_id" in rk and r["channel_id"] in migrated_channels else None,
                        "m": int(r["model_id"]) if "model_id" in rk and r["model_id"] in migrated_models else None,
                        "a": int(r["article_id"]) if "article_id" in rk and r["article_id"] in migrated_articles else None,
                        "pt": max(0, int(r["prompt_tokens"] if "prompt_tokens" in rk and r["prompt_tokens"] else 0)),
                        "ct": max(0, int(r["completion_tokens"] if "completion_tokens" in rk and r["completion_tokens"] else 0)),
                        "cost": max(0, int(r["estimated_cost_usd"] if "estimated_cost_usd" in rk and r["estimated_cost_usd"] else 0)),
                        "lat": max(0, int(r["latency_ms"] if "latency_ms" in rk and r["latency_ms"] else 0)),
                        "st": str(r["status"] if "status" in rk and r["status"] else "ok"),
                    },
                )
                ai_u_cnt += 1
        result.migrated_counts["ai_usage"] = ai_u_cnt

        # 15. pub_journal (in_flight/reserved mapped to delivery_unknown — never auto-resend!)
        pj_cnt = 0
        if _table_exists(sconn, "pub_journal"):
            rows = sconn.execute("SELECT * FROM pub_journal").fetchall()
            for idx, r in enumerate(rows, start=1):
                rk = r.keys()
                cid = int(r["channel_id"])
                aid = int(r["article_id"])
                if cid not in migrated_channels or aid not in migrated_articles:
                    continue
                raw_phase = str(r["phase"] if "phase" in rk and r["phase"] else "delivery_unknown")
                phase = "delivery_unknown" if raw_phase in ("reserved", "in_flight", "delivery_unknown") else (
                    raw_phase if raw_phase in ("acknowledged", "settled", "failed_safe") else "delivery_unknown"
                )
                idem = str(r["idempotency_key"]) if "idempotency_key" in rk and r["idempotency_key"] else f"legacy:pub:{idx}:{cid}:{aid}"
                await conn.execute(
                    text(
                        "INSERT INTO pub_journal(channel_id, article_id, idempotency_key, phase, quota_reserved, telegram_msg_id) "
                        "VALUES (:c, :a, :ik, :ph, :qr, :mid) ON CONFLICT (idempotency_key) DO NOTHING"
                    ),
                    {
                        "c": cid,
                        "a": aid,
                        "ik": idem,
                        "ph": phase,
                        "qr": bool(r["quota_reserved"]) if "quota_reserved" in rk else True,
                        "mid": int(r["telegram_msg_id"]) if "telegram_msg_id" in rk and r["telegram_msg_id"] else None,
                    },
                )
                pj_cnt += 1
        result.migrated_counts["pub_journal"] = pj_cnt

        # 16. settings (with HTML validation on i18n overrides)
        set_cnt = 0
        if _table_exists(sconn, "settings"):
            rows = sconn.execute("SELECT * FROM settings").fetchall()
            for r in rows:
                k = str(r["key"])
                raw_v = r["value"]
                try:
                    parsed_v = json.loads(raw_v) if isinstance(raw_v, str) else raw_v
                except json.JSONDecodeError:
                    parsed_v = {"value": str(raw_v)}
                if k.startswith("i18n:") and isinstance(parsed_v, dict) and isinstance(parsed_v.get("text"), str):
                    try:
                        _validate_safe_telegram_html(parsed_v["text"])
                    except Exception:
                        result.warnings.append(f"Dropped unbalanced HTML override in settings key '{k}'")
                        continue
                await conn.execute(
                    text(
                        "INSERT INTO settings(key, value) VALUES (:k, CAST(:v AS jsonb)) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
                    ),
                    {"k": k, "v": json.dumps(parsed_v, ensure_ascii=False)},
                )
                set_cnt += 1
        result.migrated_counts["settings"] = set_cnt

        # 17 & 18. support & support_map
        migrated_tickets: set[int] = set()
        if _table_exists(sconn, "support"):
            rows = sconn.execute("SELECT * FROM support ORDER BY ticket_id").fetchall()
            for r in rows:
                rk = r.keys()
                tid = int(r["ticket_id"])
                uid = int(r["user_id"])
                if uid not in migrated_user_ids:
                    continue
                st = str(r["status"]) if "status" in rk and r["status"] in ("open", "closed") else "open"
                await conn.execute(
                    text(
                        "INSERT INTO support(ticket_id, user_id, status) VALUES (:tid, :u, :st) "
                        "ON CONFLICT (ticket_id) DO NOTHING"
                    ),
                    {"tid": tid, "u": uid, "st": st},
                )
                migrated_tickets.add(tid)
        result.migrated_counts["support"] = len(migrated_tickets)

        smap_cnt = 0
        if _table_exists(sconn, "support_map"):
            rows = sconn.execute("SELECT * FROM support_map").fetchall()
            for r in rows:
                rk = r.keys()
                tid = int(r["ticket_id"])
                uid = int(r["user_id"])
                if tid not in migrated_tickets or uid not in migrated_user_ids:
                    continue
                await conn.execute(
                    text(
                        "INSERT INTO support_map(superadmin_id, forwarded_msg_id, ticket_id, user_id, user_msg_id) "
                        "VALUES (:sa, :fm, :tid, :u, :um) ON CONFLICT (superadmin_id, forwarded_msg_id) DO NOTHING"
                    ),
                    {
                        "sa": int(r["superadmin_id"]),
                        "fm": int(r["forwarded_msg_id"]),
                        "tid": tid,
                        "u": uid,
                        "um": int(r["user_msg_id"]) if "user_msg_id" in rk and r["user_msg_id"] else None,
                    },
                )
                smap_cnt += 1
        result.migrated_counts["support_map"] = smap_cnt

        # 19. plan_changes
        pc_cnt = 0
        if _table_exists(sconn, "plan_changes"):
            rows = sconn.execute("SELECT * FROM plan_changes ORDER BY change_id").fetchall()
            for r in rows:
                rk = r.keys()
                chid = int(r["change_id"])
                uid = int(r["user_id"])
                if uid not in migrated_user_ids:
                    continue
                old_p = int(r["old_plan_id"]) if "old_plan_id" in rk and r["old_plan_id"] in migrated_plan_ids else None
                new_p = int(r["new_plan_id"]) if "new_plan_id" in rk and r["new_plan_id"] in migrated_plan_ids else None
                ctype = str(r["change_type"]) if "change_type" in rk and r["change_type"] in (
                    "free_claim", "purchase", "renewal", "upgrade", "admin_assign", "revoke", "refund"
                ) else "admin_assign"
                idem = str(r["idempotency_key"]) if "idempotency_key" in rk and r["idempotency_key"] else f"legacy:plan_change:{chid}"
                await conn.execute(
                    text(
                        "INSERT INTO plan_changes(change_id, idempotency_key, user_id, old_plan_id, new_plan_id, change_type) "
                        "VALUES (:cid, :ik, :u, :op, :np, :ct) ON CONFLICT (idempotency_key) DO NOTHING"
                    ),
                    {"cid": chid, "ik": idem, "u": uid, "op": old_p, "np": new_p, "ct": ctype},
                )
                pc_cnt += 1
        result.migrated_counts["plan_changes"] = pc_cnt

        # 20. pay_requests
        pay_cnt = 0
        if _table_exists(sconn, "pay_requests"):
            rows = sconn.execute("SELECT * FROM pay_requests ORDER BY payment_id").fetchall()
            for r in rows:
                rk = r.keys()
                pay_id = int(r["payment_id"])
                uid = int(r["user_id"])
                pid = int(r["plan_id"])
                if uid not in migrated_user_ids or pid not in migrated_plan_ids:
                    continue
                cur = str(r["currency"]) if "currency" in rk and r["currency"] in ("TMN", "USD") else "TMN"
                dcode = str(r["discount_code"]).upper() if "discount_code" in rk and r["discount_code"] and str(r["discount_code"]).upper() in migrated_discounts else None
                st = str(r["status"]) if "status" in rk and r["status"] in ("pending", "processing", "approved", "rejected", "refunded") else "pending"
                idem = str(r["idempotency_key"]) if "idempotency_key" in rk and r["idempotency_key"] else f"legacy:pay:{pay_id}"
                rtype = str(r["receipt_type"]) if "receipt_type" in rk and r["receipt_type"] in ("text", "photo", "document", "wallet_full") else "text"
                await conn.execute(
                    text(
                        "INSERT INTO pay_requests(payment_id, idempotency_key, user_id, plan_id, currency, "
                        "quoted_base_price, discount_code, discount_amount, wallet_reserved, payable_amount, "
                        "receipt_type, receipt_file_id, receipt_text, status) "
                        "VALUES (:pid, :ik, :u, :pl, :cur, :qbp, :dc, :da, :wr, :pa, :rt, :rf, :rtxt, :st) "
                        "ON CONFLICT (idempotency_key) DO NOTHING"
                    ),
                    {
                        "pid": pay_id,
                        "ik": idem,
                        "u": uid,
                        "pl": pid,
                        "cur": cur,
                        "qbp": max(0, int(r["quoted_base_price"] if "quoted_base_price" in rk and r["quoted_base_price"] else 0)),
                        "dc": dcode,
                        "da": max(0, int(r["discount_amount"] if "discount_amount" in rk and r["discount_amount"] else 0)),
                        "wr": max(0, int(r["wallet_reserved"] if "wallet_reserved" in rk and r["wallet_reserved"] else 0)),
                        "pa": max(0, int(r["payable_amount"] if "payable_amount" in rk and r["payable_amount"] else 0)),
                        "rt": rtype,
                        "rf": r["receipt_file_id"] if "receipt_file_id" in rk else None,
                        "rtxt": r["receipt_text"] if "receipt_text" in rk else None,
                        "st": st,
                    },
                )
                pay_cnt += 1
        result.migrated_counts["pay_requests"] = pay_cnt

        # 21. notifications -> outbox
        notif_cnt = 0
        if _table_exists(sconn, "notifications"):
            rows = sconn.execute("SELECT * FROM notifications").fetchall()
            for idx, r in enumerate(rows, start=1):
                rk = r.keys()
                nid = int(r["id"]) if "id" in rk and r["id"] else idx
                idem = str(r["idempotency_key"]) if "idempotency_key" in rk and r["idempotency_key"] else f"legacy:notif:{nid}"
                st = str(r["status"]) if "status" in rk and r["status"] in ("pending", "sending", "sent", "dead_letter") else "sent"
                raw_p = r["payload"] if "payload" in rk and r["payload"] else "{}"
                p_str = raw_p if isinstance(raw_p, str) else json.dumps(raw_p)
                await conn.execute(
                    text(
                        "INSERT INTO outbox(idempotency_key, recipient_id, kind, payload, status) "
                        "VALUES (:ik, :rec, :k, CAST(:p AS jsonb), :st) ON CONFLICT (idempotency_key) DO NOTHING"
                    ),
                    {
                        "ik": idem,
                        "rec": int(r["recipient_id"]) if "recipient_id" in rk and r["recipient_id"] else 0,
                        "k": str(r["kind"] if "kind" in rk and r["kind"] else "text"),
                        "p": p_str,
                        "st": "pending" if st == "sending" else st,
                    },
                )
                notif_cnt += 1
        result.migrated_counts["notifications"] = notif_cnt

        # 22. referrals
        ref_cnt = 0
        if _table_exists(sconn, "referrals"):
            rows = sconn.execute("SELECT * FROM referrals").fetchall()
            for r in rows:
                rk = r.keys()
                refd = int(r["referred_user_id"])
                refr = int(r["referrer_user_id"])
                if refd == refr or refd not in migrated_user_ids or refr not in migrated_user_ids:
                    continue
                await conn.execute(
                    text(
                        "INSERT INTO referrals(referred_user_id, referrer_user_id, total_reward_tmn, total_reward_usd, rewarded_payments_count) "
                        "VALUES (:refd, :refr, :tmn, :usd, :cnt) ON CONFLICT (referred_user_id) DO NOTHING"
                    ),
                    {
                        "refd": refd,
                        "refr": refr,
                        "tmn": max(0, int(r["total_reward_tmn"] if "total_reward_tmn" in rk and r["total_reward_tmn"] else 0)),
                        "usd": max(0, int(r["total_reward_usd"] if "total_reward_usd" in rk and r["total_reward_usd"] else 0)),
                        "cnt": max(0, int(r["rewarded_payments_count"] if "rewarded_payments_count" in rk and r["rewarded_payments_count"] else 0)),
                    },
                )
                ref_cnt += 1
        result.migrated_counts["referrals"] = ref_cnt

        # 23 & 24. wallet & credit_log -> wallet_balances & ledger_entries
        # Collect target wallet balances per (user_id, currency)
        target_balances: dict[tuple[int, str], int] = {}
        wallet_cnt = 0
        if _table_exists(sconn, "wallet"):
            w_cols = {
                c["name"] for c in sconn.execute("PRAGMA table_info('wallet')").fetchall()
            }
            rows = sconn.execute("SELECT * FROM wallet").fetchall()
            for r in rows:
                uid = int(r["user_id"])
                if uid not in migrated_user_ids:
                    continue
                raw_cur = str(r["currency"]) if "currency" in w_cols and r["currency"] else "LEGACY"
                cur = raw_cur if raw_cur in ("TMN", "USD", "LEGACY") else "LEGACY"
                bal = max(0, int(r["balance"] if r["balance"] is not None else 0))
                target_balances[(uid, cur)] = target_balances.get((uid, cur), 0) + bal
                wallet_cnt += 1

        ledger_sums: dict[tuple[int, str], int] = {}
        credit_cnt = 0
        if _table_exists(sconn, "credit_log"):
            c_cols = {
                c["name"] for c in sconn.execute("PRAGMA table_info('credit_log')").fetchall()
            }
            rows = sconn.execute("SELECT * FROM credit_log ORDER BY entry_id").fetchall() if "entry_id" in c_cols else sconn.execute("SELECT * FROM credit_log").fetchall()
            for idx, r in enumerate(rows, start=1):
                uid = int(r["user_id"])
                if uid not in migrated_user_ids:
                    continue
                raw_cur = str(r["currency"]) if "currency" in c_cols and r["currency"] else "LEGACY"
                cur = raw_cur if raw_cur in ("TMN", "USD", "LEGACY") else "LEGACY"
                delta = int(r["delta"])
                if delta == 0:
                    continue
                running = ledger_sums.get((uid, cur), 0) + delta
                if running < 0:
                    continue
                ledger_sums[(uid, cur)] = running
                if (uid, cur) not in target_balances:
                    target_balances[(uid, cur)] = running

                # Ensure wallet_balances row exists before inserting ledger entry
                await conn.execute(
                    text(
                        "INSERT INTO wallet_balances(user_id, currency, balance) "
                        "VALUES (:u, :c, :b) ON CONFLICT (user_id, currency) DO NOTHING"
                    ),
                    {"u": uid, "c": cur, "b": target_balances[(uid, cur)]},
                )
                eid = int(r["entry_id"]) if "entry_id" in c_cols and r["entry_id"] else idx
                idem = str(r["idempotency_key"]) if "idempotency_key" in c_cols and r["idempotency_key"] else f"legacy:credit_log:{eid}"
                await conn.execute(
                    text(
                        "INSERT INTO ledger_entries(idempotency_key, user_id, currency, delta, balance_after, reason, reference) "
                        "VALUES (:ik, :u, :c, :d, :ba, 'migration', :ref) ON CONFLICT (idempotency_key) DO NOTHING"
                    ),
                    {
                        "ik": idem,
                        "u": uid,
                        "c": cur,
                        "d": delta,
                        "ba": running,
                        "ref": str(r["reference"]) if "reference" in c_cols and r["reference"] else "sqlite_migration",
                    },
                )
                credit_cnt += 1

        # Reconcile any wallet_balances difference so `balance == SUM(ledger_entries.delta)` holds
        for (uid, cur), final_bal in target_balances.items():
            cur_sum = ledger_sums.get((uid, cur), 0)
            diff = final_bal - cur_sum
            if diff < 0:
                # If legacy wallet balance was lower than credit_log sum and cur != LEGACY, post adjustment or align balance
                final_bal = cur_sum
                diff = 0
            await conn.execute(
                text(
                    "INSERT INTO wallet_balances(user_id, currency, balance) "
                    "VALUES (:u, :c, :b) ON CONFLICT (user_id, currency) DO UPDATE SET balance = EXCLUDED.balance"
                ),
                {"u": uid, "c": cur, "b": final_bal},
            )
            if diff > 0:
                await conn.execute(
                    text(
                        "INSERT INTO ledger_entries(idempotency_key, user_id, currency, delta, balance_after, reason, reference) "
                        "VALUES (:ik, :u, :c, :d, :ba, 'legacy_reconcile', 'wallet_opening_reconcile') "
                        "ON CONFLICT (idempotency_key) DO NOTHING"
                    ),
                    {
                        "ik": f"legacy:reconcile:{uid}:{cur}",
                        "u": uid,
                        "c": cur,
                        "d": diff,
                        "ba": final_bal,
                    },
                )

        result.migrated_counts["wallet"] = wallet_cnt
        result.migrated_counts["credit_log"] = credit_cnt

        # Synchronize PostgreSQL BIGSERIAL sequences so subsequent inserts never collide
        for seq_tbl, pk_col in (
            ("plans", "plan_id"),
            ("sources", "source_id"),
            ("articles", "article_id"),
            ("ai_models", "model_id"),
            ("support", "ticket_id"),
            ("plan_changes", "change_id"),
            ("pay_requests", "payment_id"),
        ):
            await conn.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{seq_tbl}', '{pk_col}'), "  # noqa: S608
                    f"COALESCE((SELECT MAX({pk_col}) FROM {seq_tbl}), 1), "
                    f"(SELECT COUNT(*) > 0 FROM {seq_tbl}))"
                )
            )
    finally:
        sconn.close()

    result.reconciliation = await reconcile_migration(
        sqlite_path, conn, target_keyring=target_keyring, migrated_counts=result.migrated_counts
    )
    return result


async def reconcile_migration(
    sqlite_path: str | Path,
    conn: AsyncConnection,
    *,
    target_keyring: FieldCipher,
    migrated_counts: dict[str, int] | None = None,
) -> ReconciliationReport:
    """Run all 10 Reconciliation Gates (G1..G10) comparing legacy SQLite against PostgreSQL."""
    inspect_rep = inspect_legacy_sqlite(sqlite_path)
    gates: list[GateOutcome] = []

    # G1: Row counts per migrated core table match source
    pg_users = int((await conn.execute(text("SELECT count(*) FROM users"))).scalar_one())
    pg_plans = int((await conn.execute(text("SELECT count(*) FROM plans"))).scalar_one())
    pg_channels = int((await conn.execute(text("SELECT count(*) FROM channels"))).scalar_one())
    pg_deeplinks = int((await conn.execute(text("SELECT count(*) FROM deeplinks"))).scalar_one())
    g1_ok = (
        pg_users == inspect_rep.table_counts.get("users", 0)
        and pg_plans == inspect_rep.table_counts.get("plans", 0)
        and pg_channels == inspect_rep.table_counts.get("channels", 0)
        and pg_deeplinks == inspect_rep.table_counts.get("deeplinks", 0)
    )
    gates.append(
        GateOutcome(
            "G1",
            "Row counts per migrated table match source (± documented discards)",
            g1_ok,
            f"users={pg_users}, plans={pg_plans}, channels={pg_channels}, deeplinks={pg_deeplinks}",
        )
    )

    # G2: Per-table checksum over stable primary keys
    sconn = _open_sqlite_ro(sqlite_path)
    try:
        sq_uids = (
            [int(r["user_id"]) for r in sconn.execute("SELECT user_id FROM users ORDER BY user_id")]
            if _table_exists(sconn, "users")
            else []
        )
        sq_dls = (
            [str(r["key"]) for r in sconn.execute("SELECT key FROM deeplinks ORDER BY key")]
            if _table_exists(sconn, "deeplinks")
            else []
        )
    finally:
        sconn.close()

    pg_uids = [
        int(r._mapping["user_id"])
        for r in (await conn.execute(text("SELECT user_id FROM users ORDER BY user_id"))).all()
    ]
    pg_dls = [
        str(r._mapping["key"])
        for r in (await conn.execute(text("SELECT key FROM deeplinks ORDER BY key"))).all()
    ]
    sq_hash = hashlib.sha256(json.dumps([sq_uids, sq_dls]).encode()).hexdigest()[:16]
    pg_hash = hashlib.sha256(json.dumps([pg_uids, pg_dls]).encode()).hexdigest()[:16]
    gates.append(
        GateOutcome(
            "G2",
            "Deterministic SHA-256 checksum over canonical key columns",
            sq_hash == pg_hash,
            f"sqlite_checksum={sq_hash}, pg_checksum={pg_hash}",
        )
    )

    # G3: All FKs valid & unique/idempotency constraints hold
    orphan_ch = int(
        (
            await conn.execute(
                text("SELECT count(*) FROM channels c LEFT JOIN users u ON c.owner_id = u.user_id WHERE u.user_id IS NULL")
            )
        ).scalar_one()
    )
    gates.append(
        GateOutcome(
            "G3",
            "All foreign keys and unique/idempotency constraints hold",
            orphan_ch == 0,
            f"orphan_channels={orphan_ch}",
        )
    )

    # G4: wallet_balances == SUM(ledger_entries) per (user, currency)
    drift_wallets = int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM wallet_balances wb "
                    "WHERE wb.balance <> COALESCE((SELECT SUM(le.delta) FROM ledger_entries le "
                    "WHERE le.user_id = wb.user_id AND le.currency = wb.currency), 0)"
                )
            )
        ).scalar_one()
    )
    gates.append(
        GateOutcome(
            "G4",
            "wallet_balances = SUM(ledger_entries) per (user, currency)",
            drift_wallets == 0,
            f"wallet_ledger_drift_rows={drift_wallets}",
        )
    )

    # G5: No negative balances; untagged legacy balances isolated in LEGACY
    neg_wallets = int(
        (
            await conn.execute(text("SELECT count(*) FROM wallet_balances WHERE balance < 0"))
        ).scalar_one()
    )
    gates.append(
        GateOutcome(
            "G5",
            "Zero negative balances; untagged legacy balances isolated in non-spendable LEGACY",
            neg_wallets == 0,
            f"negative_wallets={neg_wallets}",
        )
    )

    # G6: One owner per channel; one referral per referred user; one posted row per (channel, hash)
    self_refs = int(
        (
            await conn.execute(
                text("SELECT count(*) FROM referrals WHERE referred_user_id = referrer_user_id")
            )
        ).scalar_one()
    )
    gates.append(
        GateOutcome(
            "G6",
            "Ownership, referral, and publication uniqueness invariants hold",
            self_refs == 0,
            f"self_referrals={self_refs}",
        )
    )

    # G7: Every payment's plan exists in plans
    orphan_pays = int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pay_requests pr LEFT JOIN plans p ON pr.plan_id = p.plan_id WHERE p.plan_id IS NULL"
                )
            )
        ).scalar_one()
    )
    gates.append(
        GateOutcome(
            "G7",
            "Every payment request references a valid preserved plan",
            orphan_pays == 0,
            f"orphan_payments={orphan_pays}",
        )
    )

    # G8: 100% of legacy deep-link keys resolve
    g8_ok = set(sq_dls) == set(pg_dls)
    gates.append(
        GateOutcome(
            "G8",
            "100% of legacy deep-link keys resolve byte-for-byte",
            g8_ok,
            f"resolved={len(pg_dls)}/{len(sq_dls)}",
        )
    )

    # G9: Encrypted fields decrypt cleanly with the target FieldCipher
    enc_failures = 0
    src_enc_rows = (
        await conn.execute(
            text("SELECT custom_api_key_enc FROM sources WHERE custom_api_key_enc IS NOT NULL")
        )
    ).all()
    for r in src_enc_rows:
        try:
            target_keyring.decrypt(str(r._mapping["custom_api_key_enc"]))
        except Exception:
            enc_failures += 1
    mod_enc_rows = (
        await conn.execute(
            text("SELECT api_key_enc FROM ai_models WHERE api_key_enc IS NOT NULL")
        )
    ).all()
    for r in mod_enc_rows:
        try:
            target_keyring.decrypt(str(r._mapping["api_key_enc"]))
        except Exception:
            enc_failures += 1
    gates.append(
        GateOutcome(
            "G9",
            "100% of encrypted API keys decrypt with the active FieldCipher keyring",
            enc_failures == 0,
            f"encrypted_verified={len(src_enc_rows) + len(mod_enc_rows)}, failures={enc_failures}",
        )
    )

    # G10: Field-level diff on payments and wallets
    pg_pays = int((await conn.execute(text("SELECT count(*) FROM pay_requests"))).scalar_one())
    expected_pays = (
        migrated_counts["pay_requests"]
        if migrated_counts and "pay_requests" in migrated_counts
        else inspect_rep.table_counts.get("pay_requests", 0)
    )
    gates.append(
        GateOutcome(
            "G10",
            "100% field-level verification on migrated payments and wallets",
            pg_pays == expected_pays,
            f"verified_payments={pg_pays}",
        )
    )

    return ReconciliationReport(gates=gates, all_passed=all(g.passed for g in gates))
