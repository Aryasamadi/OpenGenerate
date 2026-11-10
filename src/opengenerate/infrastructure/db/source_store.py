"""PostgreSQL persistence for Sources, Diagnostics, Queue Management & Article Deduplication/Ingestion."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.application.policy import require_active
from opengenerate.domain.errors import AccessDenied, Conflict, NotFound, QuotaExceeded, ValidationFailed
from opengenerate.domain.roles import Actor, Role
from opengenerate.domain.sources import (
    VALID_DISCOVERY_TYPES,
    DiscoveryType,
    canonicalize_url,
    compute_content_fingerprint,
    compute_source_cooldown,
    compute_url_hash,
    detect_ad_heuristic,
    is_article_fresh,
    is_near_duplicate,
    matches_topic_filters,
)
from opengenerate.infrastructure.crypto import FieldCipher
from opengenerate.infrastructure.db.billing_store import get_plan
from opengenerate.infrastructure.db.channel_store import get_channel
from opengenerate.infrastructure.db.identity_store import get_user
from opengenerate.infrastructure.discovery import HttpFetcher, discover_from_source
from opengenerate.infrastructure.extractor import extract_full_article


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: int
    channel_id: int
    admin_id: int
    url: str
    canonical_url: str
    discovery_type: DiscoveryType
    fetch_enabled: bool
    publish_enabled: bool
    custom_api_url: str | None
    has_custom_api_key: bool
    etag: str | None
    last_modified: str | None
    failure_count: int
    cooldown_until: datetime | None
    last_diagnostics: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ArticleRecord:
    article_id: int
    channel_id: int
    source_id: int | None
    url: str
    canonical_url: str
    url_hash: str
    content_fingerprint: str | None
    title: str
    raw_body: str
    published_at: datetime | None
    media_urls: tuple[str, ...]
    status: str
    rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class IngestionResult:
    article: ArticleRecord | None
    deduped_existing: bool
    accepted: bool
    rejection_reason: str | None = None


def _row_to_source(row: Any) -> SourceRecord:
    m = row._mapping
    diag_raw = m["last_diagnostics_json"]
    diag = json.loads(diag_raw) if isinstance(diag_raw, str) else (diag_raw or {})
    return SourceRecord(
        source_id=int(m["source_id"]),
        channel_id=int(m["channel_id"]),
        admin_id=int(m["admin_id"]),
        url=str(m["url"]),
        canonical_url=str(m["canonical_url"]),
        discovery_type=m["discovery_type"],
        fetch_enabled=bool(m["fetch_enabled"]),
        publish_enabled=bool(m["publish_enabled"]),
        custom_api_url=m["custom_api_url"],
        has_custom_api_key=bool(m["custom_api_key_enc"]),
        etag=m["etag"],
        last_modified=m["last_modified"],
        failure_count=int(m["failure_count"]),
        cooldown_until=m["cooldown_until"],
        last_diagnostics=diag,
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


def _row_to_article(row: Any) -> ArticleRecord:
    m = row._mapping
    media_raw = m["media_urls_json"]
    media_list = json.loads(media_raw) if isinstance(media_raw, str) else (media_raw or [])
    return ArticleRecord(
        article_id=int(m["article_id"]),
        channel_id=int(m["channel_id"]),
        source_id=int(m["source_id"]) if m["source_id"] is not None else None,
        url=str(m["url"]),
        canonical_url=str(m["canonical_url"]),
        url_hash=str(m["url_hash"]),
        content_fingerprint=m["content_fingerprint"],
        title=str(m["title"]),
        raw_body=str(m["raw_body"]),
        published_at=m["published_at"],
        media_urls=tuple(str(u) for u in media_list),
        status=str(m["status"]),
        rejection_reason=m["rejection_reason"],
    )


async def _enforce_max_sources_quota(
    conn: AsyncConnection,
    *,
    owner_id: int,
    channel_id: int,
    now: datetime,
    bootstrap_superadmins: Collection[int] = (),
) -> None:
    user = await get_user(conn, owner_id, bootstrap_superadmins)
    if user.is_banned:
        raise AccessDenied("user is banned")
    if user.role is Role.SUPERADMIN:
        return
    if user.plan_id is None or user.plan_expires_at is None or user.plan_expires_at <= now:
        raise QuotaExceeded("active_subscription_required")
    plan = await get_plan(conn, user.plan_id)
    count = int(
        (
            await conn.execute(
                text("SELECT count(*) FROM sources WHERE channel_id = :cid"),
                {"cid": channel_id},
            )
        ).scalar_one()
    )
    if count >= plan.max_sources:
        raise QuotaExceeded("max_sources_quota_exceeded")


async def add_source(
    conn: AsyncConnection,
    *,
    actor: Actor,
    channel_id: int,
    url: str,
    discovery_type: DiscoveryType = "auto",
    custom_api_url: str | None = None,
    custom_api_key: str | None = None,
    keyring: FieldCipher | None = None,
    now: datetime | None = None,
    bootstrap_superadmins: Collection[int] = (),
) -> SourceRecord:
    """Register a content source under `channel_id` with URL canonicalization and quota checks."""
    ch = await get_channel(conn, actor=actor, channel_id=channel_id)
    if discovery_type not in VALID_DISCOVERY_TYPES:
        raise ValidationFailed(f"invalid discovery_type: {discovery_type}")
    canon = canonicalize_url(url)
    canon_api = canonicalize_url(custom_api_url) if custom_api_url else None
    ts = now or datetime.now(UTC)

    dup = (
        await conn.execute(
            text(
                "SELECT source_id FROM sources "
                "WHERE channel_id = :cid AND canonical_url = :cu"
            ),
            {"cid": ch.channel_id, "cu": canon},
        )
    ).first()
    if dup is not None:
        raise Conflict("source_already_exists_in_channel")

    await _enforce_max_sources_quota(
        conn,
        owner_id=ch.owner_id,
        channel_id=ch.channel_id,
        now=ts,
        bootstrap_superadmins=bootstrap_superadmins,
    )

    enc_key: str | None = None
    if custom_api_key:
        if keyring is None:
            raise ValidationFailed("keyring is required to encrypt custom_api_key")
        enc_key = keyring.encrypt(custom_api_key)

    row = (
        await conn.execute(
            text(
                "INSERT INTO sources("
                "channel_id, admin_id, url, canonical_url, discovery_type, "
                "custom_api_url, custom_api_key_enc) "
                "VALUES (:cid, :aid, :u, :cu, :dt, :api, :enc) RETURNING *"
            ),
            {
                "cid": ch.channel_id,
                "aid": ch.owner_id,
                "u": url.strip(),
                "cu": canon,
                "dt": discovery_type,
                "api": canon_api,
                "enc": enc_key,
            },
        )
    ).one()
    return _row_to_source(row)


async def get_source(
    conn: AsyncConnection,
    *,
    actor: Actor,
    source_id: int,
) -> SourceRecord:
    require_active(actor)
    row = (
        await conn.execute(
            text("SELECT * FROM sources WHERE source_id = :sid"),
            {"sid": source_id},
        )
    ).first()
    if row is None:
        raise NotFound("source not found")
    src = _row_to_source(row)
    await get_channel(conn, actor=actor, channel_id=src.channel_id)
    return src


async def list_sources(
    conn: AsyncConnection,
    *,
    actor: Actor,
    channel_id: int,
) -> list[SourceRecord]:
    ch = await get_channel(conn, actor=actor, channel_id=channel_id)
    rows = await conn.execute(
        text("SELECT * FROM sources WHERE channel_id = :cid ORDER BY source_id ASC"),
        {"cid": ch.channel_id},
    )
    return [_row_to_source(r) for r in rows]


async def set_source_toggles(
    conn: AsyncConnection,
    *,
    actor: Actor,
    source_id: int,
    fetch_enabled: bool | None = None,
    publish_enabled: bool | None = None,
) -> SourceRecord:
    src = await get_source(conn, actor=actor, source_id=source_id)
    new_fetch = src.fetch_enabled if fetch_enabled is None else fetch_enabled
    new_pub = src.publish_enabled if publish_enabled is None else publish_enabled
    row = (
        await conn.execute(
            text(
                "UPDATE sources SET fetch_enabled = :f, publish_enabled = :p, updated_at = now() "
                "WHERE source_id = :sid RETURNING *"
            ),
            {"f": new_fetch, "p": new_pub, "sid": src.source_id},
        )
    ).one()
    return _row_to_source(row)


async def configure_source_discovery(
    conn: AsyncConnection,
    *,
    actor: Actor,
    source_id: int,
    discovery_type: DiscoveryType,
    custom_api_url: str | None = None,
    custom_api_key: str | None = None,
    keyring: FieldCipher | None = None,
) -> SourceRecord:
    src = await get_source(conn, actor=actor, source_id=source_id)
    if discovery_type not in VALID_DISCOVERY_TYPES:
        raise ValidationFailed(f"invalid discovery_type: {discovery_type}")
    canon_api = canonicalize_url(custom_api_url) if custom_api_url else src.custom_api_url
    enc_key: str | None = None
    if custom_api_key is not None:
        if custom_api_key == "":
            enc_key = None
        else:
            if keyring is None:
                raise ValidationFailed("keyring is required to encrypt custom_api_key")
            enc_key = keyring.encrypt(custom_api_key)
        row = (
            await conn.execute(
                text(
                    "UPDATE sources SET discovery_type = :dt, custom_api_url = :api, "
                    "custom_api_key_enc = :enc, updated_at = now() "
                    "WHERE source_id = :sid RETURNING *"
                ),
                {"dt": discovery_type, "api": canon_api, "enc": enc_key, "sid": src.source_id},
            )
        ).one()
    else:
        row = (
            await conn.execute(
                text(
                    "UPDATE sources SET discovery_type = :dt, custom_api_url = :api, "
                    "updated_at = now() WHERE source_id = :sid RETURNING *"
                ),
                {"dt": discovery_type, "api": canon_api, "sid": src.source_id},
            )
        ).one()
    return _row_to_source(row)


async def delete_source(
    conn: AsyncConnection,
    *,
    actor: Actor,
    source_id: int,
) -> None:
    src = await get_source(conn, actor=actor, source_id=source_id)
    await conn.execute(
        text("DELETE FROM sources WHERE source_id = :sid"),
        {"sid": src.source_id},
    )


async def clear_article_queue(
    conn: AsyncConnection,
    *,
    actor: Actor,
    channel_id: int,
    source_id: int | None = None,
) -> int:
    """Clear unpublished queued articles for a specific source or an entire channel."""
    ch = await get_channel(conn, actor=actor, channel_id=channel_id)
    if source_id is not None:
        res = await conn.execute(
            text(
                "DELETE FROM articles WHERE channel_id = :cid AND source_id = :sid "
                "AND status IN ('discovered', 'extracted', 'ai_pending', 'ready', 'fetch_retryable', 'ai_retryable')"
            ),
            {"cid": ch.channel_id, "sid": source_id},
        )
    else:
        res = await conn.execute(
            text(
                "DELETE FROM articles WHERE channel_id = :cid "
                "AND status IN ('discovered', 'extracted', 'ai_pending', 'ready', 'fetch_retryable', 'ai_retryable')"
            ),
            {"cid": ch.channel_id},
        )
    return int(res.rowcount or 0)


async def record_source_fetch_success(
    conn: AsyncConnection,
    *,
    source_id: int,
    etag: str | None,
    last_modified: str | None,
    diagnostics: dict[str, Any],
) -> SourceRecord:
    row = (
        await conn.execute(
            text(
                "UPDATE sources SET failure_count = 0, cooldown_until = NULL, "
                "etag = :et, last_modified = :lm, "
                "last_diagnostics_json = CAST(:diag AS jsonb), updated_at = now() "
                "WHERE source_id = :sid RETURNING *"
            ),
            {
                "et": etag,
                "lm": last_modified,
                "diag": json.dumps(diagnostics),
                "sid": source_id,
            },
        )
    ).one()
    return _row_to_source(row)


async def record_source_fetch_failure(
    conn: AsyncConnection,
    *,
    source_id: int,
    error_message: str,
    now: datetime | None = None,
) -> SourceRecord:
    ts = now or datetime.now(UTC)
    cur_row = (
        await conn.execute(
            text("SELECT failure_count FROM sources WHERE source_id = :sid FOR UPDATE"),
            {"sid": source_id},
        )
    ).one()
    new_failures = int(cur_row._mapping["failure_count"]) + 1
    cooldown = compute_source_cooldown(new_failures, now=ts)
    diag = {
        "status": "error",
        "error": error_message,
        "failure_count": new_failures,
        "cooldown_until": cooldown.isoformat() if cooldown else None,
    }
    row = (
        await conn.execute(
            text(
                "UPDATE sources SET failure_count = :fc, cooldown_until = :cd, "
                "last_diagnostics_json = CAST(:diag AS jsonb), updated_at = now() "
                "WHERE source_id = :sid RETURNING *"
            ),
            {
                "fc": new_failures,
                "cd": cooldown,
                "diag": json.dumps(diag),
                "sid": source_id,
            },
        )
    ).one()
    return _row_to_source(row)


# ---- Article Ingestion, Exact & Near-Duplicate Deduplication, and Filtering ------------------


async def ingest_discovered_article(
    conn: AsyncConnection,
    *,
    channel_id: int,
    source_id: int | None,
    url: str,
    title: str,
    raw_body: str,
    published_at: datetime | None = None,
    media_urls: tuple[str, ...] = (),
    now: datetime | None = None,
) -> IngestionResult:
    """Deduplicate by URL hash, `posted` history, and SimHash content fingerprint; apply age/topic/ad filters."""
    ts = now or datetime.now(UTC)
    canon_url = canonicalize_url(url)
    u_hash = compute_url_hash(canon_url)

    # 1) Check `posted` publication history first
    already_posted = (
        await conn.execute(
            text(
                "SELECT 1 FROM posted WHERE channel_id = :cid AND article_hash = :uh"
            ),
            {"cid": channel_id, "uh": u_hash},
        )
    ).first()
    if already_posted is not None:
        return IngestionResult(
            article=None,
            deduped_existing=True,
            accepted=False,
            rejection_reason="already_posted_url_hash",
        )

    # 2) Check `articles` table for existing `(channel_id, url_hash)`
    existing_art = (
        await conn.execute(
            text("SELECT * FROM articles WHERE channel_id = :cid AND url_hash = :uh"),
            {"cid": channel_id, "uh": u_hash},
        )
    ).first()
    if existing_art is not None:
        art = _row_to_article(existing_art)
        return IngestionResult(
            article=art,
            deduped_existing=True,
            accepted=art.status in ("discovered", "extracted", "ai_pending", "ready"),
            rejection_reason=art.rejection_reason,
        )

    # Load channel settings for age, topic keywords, and ad heuristic checks
    ch_row = (
        await conn.execute(
            text("SELECT settings_json FROM channels WHERE channel_id = :cid"),
            {"cid": channel_id},
        )
    ).one()
    raw_settings = ch_row._mapping["settings_json"]
    from opengenerate.domain.channels import ChannelSettings

    settings = ChannelSettings.from_dict(
        json.loads(raw_settings) if isinstance(raw_settings, str) else (raw_settings or {})
    )

    fingerprint = compute_content_fingerprint(title, raw_body) if (title or raw_body) else None
    status = "extracted" if raw_body.strip() else "discovered"
    rejection_reason: str | None = None

    # 3) Check near-duplicate SimHash fingerprint against recent non-rejected articles in this channel
    if fingerprint and fingerprint != "0" * 16 and len(raw_body.split()) >= 15:
        recent_fps = await conn.execute(
            text(
                "SELECT content_fingerprint FROM articles "
                "WHERE channel_id = :cid AND content_fingerprint IS NOT NULL "
                "AND status NOT IN ('rejected_duplicate', 'rejected_date') "
                "ORDER BY created_at DESC LIMIT 200"
            ),
            {"cid": channel_id},
        )
        for fp_row in recent_fps:
            other_fp = str(fp_row._mapping["content_fingerprint"])
            if is_near_duplicate(fingerprint, other_fp):
                status = "rejected_duplicate"
                rejection_reason = "near_duplicate_fingerprint"
                break

    # 4) Freshness check (`max_article_age_days`)
    if status in ("discovered", "extracted") and not is_article_fresh(
        published_at, now=ts, max_age_days=settings.max_article_age_days
    ):
        status = "rejected_date"
        rejection_reason = f"older_than_{settings.max_article_age_days}_days"

    # 5) Heuristic advertisement / sponsored content check
    if (
        status in ("discovered", "extracted")
        and settings.ad_detection_enabled
        and (title or raw_body)
    ):
        is_ad, ad_reason = detect_ad_heuristic(title, raw_body)
        if is_ad:
            status = "rejected_ad_heuristic"
            rejection_reason = ad_reason

    # 6) Topic keyword include/exclude filter
    if status in ("discovered", "extracted") and (
        settings.topic_keywords_include or settings.topic_keywords_exclude
    ):
        ok_topic, topic_reason = matches_topic_filters(
            title,
            raw_body,
            include_keywords=settings.topic_keywords_include,
            exclude_keywords=settings.topic_keywords_exclude,
        )
        if not ok_topic:
            status = "rejected_low_score"
            rejection_reason = topic_reason

    ins_row = (
        await conn.execute(
            text(
                "INSERT INTO articles("
                "channel_id, source_id, url, canonical_url, url_hash, content_fingerprint, "
                "title, raw_body, published_at, media_urls_json, status, rejection_reason) "
                "VALUES (:cid, :sid, :u, :cu, :uh, :fp, :t, :rb, :pub, CAST(:media AS jsonb), :st, :rr) "
                "ON CONFLICT (channel_id, url_hash) DO NOTHING RETURNING *"
            ),
            {
                "cid": channel_id,
                "sid": source_id,
                "u": url,
                "cu": canon_url,
                "uh": u_hash,
                "fp": fingerprint,
                "t": title,
                "rb": raw_body,
                "pub": published_at,
                "media": json.dumps(list(media_urls)),
                "st": status,
                "rr": rejection_reason,
            },
        )
    ).first()
    if ins_row is None:
        return IngestionResult(
            article=None,
            deduped_existing=True,
            accepted=False,
            rejection_reason="concurrent_duplicate",
        )

    art = _row_to_article(ins_row)
    return IngestionResult(
        article=art,
        deduped_existing=False,
        accepted=art.status in ("discovered", "extracted"),
        rejection_reason=art.rejection_reason,
    )


async def poll_and_extract_source(
    conn: AsyncConnection,
    *,
    source_id: int,
    http: HttpFetcher,
    keyring: FieldCipher | None = None,
    max_articles_per_poll: int = 10,
    now: datetime | None = None,
) -> list[IngestionResult]:
    """Run discovery + full-text extraction + deduplicated ingestion for a single source."""
    ts = now or datetime.now(UTC)
    s_row = (
        await conn.execute(
            text("SELECT * FROM sources WHERE source_id = :sid"),
            {"sid": source_id},
        )
    ).first()
    if s_row is None:
        raise NotFound("source not found")
    m = s_row._mapping
    src = _row_to_source(s_row)

    if not src.fetch_enabled:
        return []
    if src.cooldown_until is not None and src.cooldown_until > ts:
        return []

    api_key: str | None = None
    if m["custom_api_key_enc"] and keyring is not None:
        api_key = keyring.decrypt(str(m["custom_api_key_enc"]))

    try:
        outcome = await discover_from_source(
            http,
            source_url=src.canonical_url,
            discovery_type=src.discovery_type,
            custom_api_url=src.custom_api_url,
            custom_api_key=api_key,
            etag=src.etag,
            last_modified=src.last_modified,
        )
    except Exception as exc:
        await record_source_fetch_failure(
            conn, source_id=src.source_id, error_message=str(exc), now=ts
        )
        return []

    if outcome.not_modified:
        await record_source_fetch_success(
            conn,
            source_id=src.source_id,
            etag=outcome.etag,
            last_modified=outcome.last_modified,
            diagnostics={"strategy": outcome.strategy_used, "not_modified": True},
        )
        return []

    results: list[IngestionResult] = []
    for item in outcome.items[:max_articles_per_poll]:
        u_hash = compute_url_hash(item.canonical_url)
        # Skip HTTP article fetch if this URL hash is already known in `articles` or `posted`
        exists = (
            await conn.execute(
                text(
                    "SELECT 1 FROM articles WHERE channel_id = :cid AND url_hash = :uh "
                    "UNION ALL "
                    "SELECT 1 FROM posted WHERE channel_id = :cid AND article_hash = :uh "
                    "LIMIT 1"
                ),
                {"cid": src.channel_id, "uh": u_hash},
            )
        ).first()
        if exists is not None:
            results.append(
                IngestionResult(
                    article=None,
                    deduped_existing=True,
                    accepted=False,
                    rejection_reason="already_known_url_hash",
                )
            )
            continue

        title = item.title
        body = item.summary_text
        pub_at = item.published_at
        media = list(item.media_urls)

        # Fetch full multi-page article HTML when body is short or missing
        if len(body.split()) < 80:
            with contextlib.suppress(Exception):
                extracted = await extract_full_article(http, item.url)
                if extracted.title and not title:
                    title = extracted.title
                if len(extracted.body) > len(body):
                    body = extracted.body
                if extracted.published_at and pub_at is None:
                    pub_at = extracted.published_at
                for m_url in extracted.media_urls:
                    if m_url not in media:
                        media.append(m_url)

        res = await ingest_discovered_article(
            conn,
            channel_id=src.channel_id,
            source_id=src.source_id,
            url=item.url,
            title=title,
            raw_body=body,
            published_at=pub_at,
            media_urls=tuple(media),
            now=ts,
        )
        results.append(res)

    await record_source_fetch_success(
        conn,
        source_id=src.source_id,
        etag=outcome.etag,
        last_modified=outcome.last_modified,
        diagnostics={
            "strategy": outcome.strategy_used,
            "discovered_count": len(outcome.items),
            "accepted_count": sum(1 for r in results if r.accepted),
        },
    )
    return results
