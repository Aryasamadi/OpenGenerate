"""Crash-Safe 6-Phase Publication Journal (`pub_journal`), Deep-Link Continuation Reader & Manual Test Post."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.domain.channels import ChannelSettings
from opengenerate.domain.editorial import (
    RichBlock,
    chunk_telegram_html,
    parse_rich_markdown_to_blocks,
    render_blocks_to_telegram_html,
    render_rich_message_payload,
)
from opengenerate.domain.errors import Conflict, NotFound, PermanentError, TransientError, UnknownOutcomeError
from opengenerate.infrastructure.db.billing_store import release_daily_quota, reserve_daily_quota
from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.media import prepare_article_media


class BotPublishPort(Protocol):
    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: Mapping[str, Any] | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]: ...

    async def send_rich_message(
        self,
        chat_id: int | str,
        *,
        blocks: list[dict[str, Any]],
        fallback_html: str,
        reply_markup: Mapping[str, Any] | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]: ...

    async def send_photo(
        self,
        chat_id: int | str,
        photo: str,
        *,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def send_video(
        self,
        chat_id: int | str,
        video: str,
        *,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def send_media_group(
        self,
        chat_id: int | str,
        media: list[dict[str, Any]],
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class PublicationResult:
    journal_id: int
    article_id: int
    channel_id: int
    phase: str
    telegram_msg_id: int | None
    deeplink_key: str | None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class DeepLinkContinuation:
    key: str
    article_id: int
    channel_id: int
    title: str
    scientific_domain: str
    presentation_format: str
    rich_blocks: list[dict[str, Any]]
    html_chunks: list[str]
    views_count: int


# ---- Deep Links (`deeplinks`) ----------------------------------------------------------------


async def create_or_get_deeplink(
    conn: AsyncConnection,
    *,
    article_id: int,
    channel_id: int,
    title: str,
    scientific_domain: str,
    presentation_format: str,
    rich_blocks: list[dict[str, Any]],
    full_html: str,
    now: datetime | None = None,
) -> str:
    """Create or retrieve a deterministic deep-link key (`dl_<16-hex>`) for bot continuation reading."""
    ts = now or datetime.now(UTC)
    digest = hashlib.sha256(f"dl:{channel_id}:{article_id}".encode()).hexdigest()[:16]
    key = f"dl_{digest}"
    payload = {
        "title": title,
        "scientific_domain": scientific_domain,
        "presentation_format": presentation_format,
        "rich_blocks": rich_blocks,
        "full_html": full_html,
    }
    await conn.execute(
        text(
            "INSERT INTO deeplinks(key, article_id, channel_id, payload_json, expires_at) "
            "VALUES (:k, :aid, :cid, CAST(:p AS jsonb), :exp) "
            "ON CONFLICT (key) DO UPDATE SET payload_json = EXCLUDED.payload_json"
        ),
        {
            "k": key,
            "aid": article_id,
            "cid": channel_id,
            "p": json.dumps(payload),
            "exp": ts + timedelta(days=90),
        },
    )
    return key


async def resolve_deeplink_continuation(
    conn: AsyncConnection,
    *,
    key: str,
    now: datetime | None = None,
) -> DeepLinkContinuation:
    """Resolve a `dl_<...>` deep-link parameter, increment `views_count`, and return rich blocks + HTML chunks."""
    ts = now or datetime.now(UTC)
    norm_key = key.strip()
    row = (
        await conn.execute(
            text(
                "UPDATE deeplinks SET views_count = views_count + 1 "
                "WHERE key = :k AND (expires_at IS NULL OR expires_at > :now) "
                "RETURNING *"
            ),
            {"k": norm_key, "now": ts},
        )
    ).first()
    if row is None:
        raise NotFound("deeplink_not_found_or_expired")

    m = row._mapping
    raw_payload = m["payload_json"]
    payload = json.loads(raw_payload) if isinstance(raw_payload, str) else (raw_payload or {})
    rich_blocks = list(payload.get("rich_blocks") or [])
    full_html = str(payload.get("full_html") or "")
    if not full_html and rich_blocks:
        objs = [RichBlock.from_dict(b) for b in rich_blocks if isinstance(b, dict)]
        full_html = render_blocks_to_telegram_html(objs)

    chunks = chunk_telegram_html(full_html, max_chars=4096) if full_html else [""]
    return DeepLinkContinuation(
        key=norm_key,
        article_id=int(m["article_id"]),
        channel_id=int(m["channel_id"]),
        title=str(payload.get("title") or ""),
        scientific_domain=str(payload.get("scientific_domain") or "general"),
        presentation_format=str(payload.get("presentation_format") or "narrative"),
        rich_blocks=rich_blocks,
        html_chunks=chunks,
        views_count=int(m["views_count"]),
    )


# ---- Crash-Safe 6-Phase Publication Engine (`pub_journal`) -----------------------------------


def _build_read_more_markup(
    *,
    bot_username: str,
    deeplink_key: str,
    button_text: str,
) -> dict[str, Any]:
    clean_bot = bot_username.lstrip("@")
    url = f"https://t.me/{clean_bot}?start={deeplink_key}"
    return {"inline_keyboard": [[{"text": button_text, "url": url}]]}


async def _deliver_to_telegram(
    bot_api: BotPublishPort,
    *,
    chat_id: int | str,
    post_html: str,
    rich_blocks: list[dict[str, Any]],
    media_urls: Sequence[str],
    settings: ChannelSettings,
    reply_markup: Mapping[str, Any] | None,
) -> int:
    """Deliver post + media to Telegram and return `message_id`."""
    prepared = prepare_article_media(
        media_urls, media_mode=settings.media_mode, watermark=settings.watermark
    )

    if prepared.mode == "single_image" and prepared.urls:
        caption_chunks = chunk_telegram_html(post_html, max_chars=1024)
        resp = await bot_api.send_photo(
            chat_id,
            prepared.urls[0],
            caption=caption_chunks[0],
            parse_mode="HTML",
            reply_markup=reply_markup if len(caption_chunks) == 1 else None,
        )
        msg_id = int(resp.get("message_id", 1))
        for extra in caption_chunks[1:]:
            await bot_api.send_message(
                chat_id, extra, parse_mode="HTML", reply_markup=reply_markup
            )
        return msg_id

    if prepared.mode == "video" and prepared.urls:
        caption_chunks = chunk_telegram_html(post_html, max_chars=1024)
        resp = await bot_api.send_video(
            chat_id,
            prepared.urls[0],
            caption=caption_chunks[0],
            parse_mode="HTML",
            reply_markup=reply_markup if len(caption_chunks) == 1 else None,
        )
        return int(resp.get("message_id", 1))

    if prepared.mode == "multi_image_album" and len(prepared.urls) >= 2:
        caption_chunks = chunk_telegram_html(post_html, max_chars=1024)
        media_items: list[dict[str, Any]] = []
        for idx, u in enumerate(prepared.urls):
            item: dict[str, Any] = {"type": "photo", "media": u}
            if idx == 0:
                item["caption"] = caption_chunks[0]
                item["parse_mode"] = "HTML"
            media_items.append(item)
        group_resp = await bot_api.send_media_group(chat_id, media_items)
        msg_id = int(group_resp[0].get("message_id", 1)) if group_resp else 1
        if reply_markup is not None:
            await bot_api.send_message(
                chat_id,
                settings.read_more_button_text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        return msg_id

    # Text or Rich Message post
    if settings.rich_message_formatting_enabled and rich_blocks:
        resp = await bot_api.send_rich_message(
            chat_id,
            blocks=rich_blocks,
            fallback_html=post_html,
            reply_markup=reply_markup,
        )
        return int(resp.get("message_id", 1))

    chunks = chunk_telegram_html(post_html, max_chars=4096)
    first_resp = await bot_api.send_message(
        chat_id,
        chunks[0],
        parse_mode="HTML",
        reply_markup=reply_markup if len(chunks) == 1 else None,
    )
    msg_id = int(first_resp.get("message_id", 1))
    for extra in chunks[1:]:
        await bot_api.send_message(
            chat_id, extra, parse_mode="HTML", reply_markup=reply_markup
        )
    return msg_id


async def publish_ready_article(
    db: Database,
    *,
    article_id: int,
    bot_api: BotPublishPort,
    bot_username: str = "OpenGenerateBot",
    idempotency_key: str | None = None,
    now: datetime | None = None,
    bootstrap_superadmins: Sequence[int] = (),
) -> PublicationResult:
    """Execute the crash-safe 6-phase `pub_journal` state machine for `article_id`."""
    ts = now or datetime.now(UTC)
    idem_key = idempotency_key or f"pub:art:{article_id}"

    # ---- Phase 1 & 2: Reserve Quota, Create DeepLink, and Commit `in_flight` Journal ---------
    async with db.transaction() as conn:
        existing_j = (
            await conn.execute(
                text("SELECT * FROM pub_journal WHERE idempotency_key = :k FOR UPDATE"),
                {"k": idem_key},
            )
        ).first()
        if existing_j is not None:
            jm = existing_j._mapping
            phase = str(jm["phase"])
            if phase in ("settled", "acknowledged"):
                return PublicationResult(
                    journal_id=int(jm["journal_id"]),
                    article_id=int(jm["article_id"]),
                    channel_id=int(jm["channel_id"]),
                    phase=phase,
                    telegram_msg_id=int(jm["telegram_msg_id"]) if jm["telegram_msg_id"] else None,
                    deeplink_key=None,
                    replayed=True,
                )
            if phase in ("in_flight", "delivery_unknown"):
                raise Conflict(f"cannot auto-retry publication in phase '{phase}'")

        art_row = (
            await conn.execute(
                text("SELECT * FROM articles WHERE article_id = :aid FOR UPDATE"),
                {"aid": article_id},
            )
        ).first()
        if art_row is None:
            raise NotFound("article not found")
        am = art_row._mapping
        channel_id = int(am["channel_id"])
        url_hash = str(am["url_hash"])

        if str(am["status"]) == "published":
            raise Conflict("article_already_published")
        if str(am["status"]) == "delivery_unknown":
            raise Conflict("article_delivery_unknown_requires_reconciliation")

        already_posted = (
            await conn.execute(
                text("SELECT 1 FROM posted WHERE channel_id = :cid AND article_hash = :uh"),
                {"cid": channel_id, "uh": url_hash},
            )
        ).first()
        if already_posted is not None:
            raise Conflict("article_hash_already_in_posted_history")

        ch_row = (
            await conn.execute(
                text("SELECT owner_id, settings_json FROM channels WHERE channel_id = :cid"),
                {"cid": channel_id},
            )
        ).one()
        owner_id = int(ch_row._mapping["owner_id"])
        raw_settings = ch_row._mapping["settings_json"]
        settings = ChannelSettings.from_dict(
            json.loads(raw_settings) if isinstance(raw_settings, str) else (raw_settings or {})
        )

        # Reserve daily post quota before external send
        await reserve_daily_quota(
            conn,
            user_id=owner_id,
            counter="posts_count",
            now=ts,
            bootstrap_superadmins=bootstrap_superadmins,
        )

        raw_rb = am["rich_blocks_json"]
        rich_blocks: list[dict[str, Any]] = (
            json.loads(raw_rb) if isinstance(raw_rb, str) else (raw_rb or [])
        )
        post_html = str(am["generated_post"] or am["raw_body"] or am["title"])
        full_html = str(am["generated_full"] or post_html)
        raw_media = am["media_urls_json"]
        media_urls: list[str] = (
            json.loads(raw_media) if isinstance(raw_media, str) else (raw_media or [])
        )

        dl_key: str | None = None
        if settings.continuation_in_bot_enabled and settings.content_mode == "continuation_bot":
            dl_key = await create_or_get_deeplink(
                conn,
                article_id=article_id,
                channel_id=channel_id,
                title=str(am["generated_title"] or am["title"]),
                scientific_domain=str(am["scientific_domain"]),
                presentation_format=str(am["presentation_format"]),
                rich_blocks=rich_blocks,
                full_html=full_html,
                now=ts,
            )

        # Phase 1 ('reserved') -> Phase 2 ('in_flight') committed before network send
        j_row = (
            await conn.execute(
                text(
                    "INSERT INTO pub_journal(channel_id, article_id, idempotency_key, phase, quota_reserved) "
                    "VALUES (:cid, :aid, :k, 'in_flight', TRUE) "
                    "ON CONFLICT (idempotency_key) DO UPDATE SET "
                    "phase = 'in_flight', quota_reserved = TRUE, updated_at = now() "
                    "RETURNING journal_id"
                ),
                {"cid": channel_id, "aid": article_id, "k": idem_key},
            )
        ).one()
        journal_id = int(j_row._mapping["journal_id"])
        await conn.execute(
            text("UPDATE articles SET status = 'publishing', updated_at = now() WHERE article_id = :aid"),
            {"aid": article_id},
        )

    # ---- Phase 3: External Telegram Delivery -------------------------------------------------
    markup = (
        _build_read_more_markup(
            bot_username=bot_username,
            deeplink_key=dl_key,
            button_text=settings.read_more_button_text,
        )
        if dl_key
        else None
    )

    try:
        msg_id = await _deliver_to_telegram(
            bot_api,
            chat_id=channel_id,
            post_html=post_html,
            rich_blocks=rich_blocks,
            media_urls=media_urls,
            settings=settings,
            reply_markup=markup,
        )
    except UnknownOutcomeError as exc:
        # ---- Ambiguous Outcome (`delivery_unknown`): NEVER blindly resend --------------------
        async with db.transaction() as conn:
            await conn.execute(
                text(
                    "UPDATE pub_journal SET phase = 'delivery_unknown', error_detail = :err, "
                    "updated_at = now() WHERE journal_id = :jid"
                ),
                {"err": str(exc), "jid": journal_id},
            )
            await conn.execute(
                text(
                    "UPDATE articles SET status = 'delivery_unknown', failure_class = 'UnknownOutcomeError', "
                    "updated_at = now() WHERE article_id = :aid"
                ),
                {"aid": article_id},
            )
        return PublicationResult(
            journal_id=journal_id,
            article_id=article_id,
            channel_id=channel_id,
            phase="delivery_unknown",
            telegram_msg_id=None,
            deeplink_key=dl_key,
        )
    except (TransientError, PermanentError, Exception) as exc:
        # ---- Definite Pre-Delivery Failure (`failed_safe`): Release Reserved Quota -----------
        async with db.transaction() as conn:
            await release_daily_quota(conn, user_id=owner_id, counter="posts_count")
            await conn.execute(
                text(
                    "UPDATE pub_journal SET phase = 'failed_safe', quota_reserved = FALSE, "
                    "error_detail = :err, updated_at = now() WHERE journal_id = :jid"
                ),
                {"err": str(exc), "jid": journal_id},
            )
            await conn.execute(
                text(
                    "UPDATE articles SET status = 'publish_failed_safe', "
                    "failure_class = :fc, updated_at = now() WHERE article_id = :aid"
                ),
                {"fc": type(exc).__name__, "aid": article_id},
            )
        raise

    # ---- Phase 4 & 5: `acknowledged` -> `settled` + Record in `posted` -----------------------
    async with db.transaction() as conn:
        await conn.execute(
            text(
                "UPDATE pub_journal SET phase = 'settled', telegram_msg_id = :mid, "
                "updated_at = now() WHERE journal_id = :jid"
            ),
            {"mid": msg_id, "jid": journal_id},
        )
        await conn.execute(
            text(
                "INSERT INTO posted(channel_id, article_hash, article_id, telegram_message_id, published_at) "
                "VALUES (:cid, :uh, :aid, :mid, :now) ON CONFLICT (channel_id, article_hash) DO NOTHING"
            ),
            {
                "cid": channel_id,
                "uh": url_hash,
                "aid": article_id,
                "mid": msg_id,
                "now": ts,
            },
        )
        await conn.execute(
            text(
                "UPDATE articles SET status = 'published', published_msg_id = :mid, "
                "updated_at = now() WHERE article_id = :aid"
            ),
            {"mid": msg_id, "aid": article_id},
        )
        await conn.execute(
            text("UPDATE channels SET last_run_at = :now, updated_at = :now WHERE channel_id = :cid"),
            {"now": ts, "cid": channel_id},
        )

    return PublicationResult(
        journal_id=journal_id,
        article_id=article_id,
        channel_id=channel_id,
        phase="settled",
        telegram_msg_id=msg_id,
        deeplink_key=dl_key,
    )


# ---- Manual Test Post (`tests_count` Quota) --------------------------------------------------


async def send_manual_test_post(
    db: Database,
    *,
    user_id: int,
    channel_id: int,
    sample_title: str,
    sample_markdown: str,
    bot_api: BotPublishPort,
    now: datetime | None = None,
    bootstrap_superadmins: Sequence[int] = (),
) -> int:
    """Reserve `tests_count` quota and send a formatted Rich/HTML test post to `user_id`'s private chat."""
    ts = now or datetime.now(UTC)
    async with db.transaction() as conn:
        ch_row = (
            await conn.execute(
                text("SELECT settings_json FROM channels WHERE channel_id = :cid"),
                {"cid": channel_id},
            )
        ).first()
        if ch_row is None:
            raise NotFound("channel not found")
        raw_settings = ch_row._mapping["settings_json"]
        settings = ChannelSettings.from_dict(
            json.loads(raw_settings) if isinstance(raw_settings, str) else (raw_settings or {})
        )
        await reserve_daily_quota(
            conn,
            user_id=user_id,
            counter="tests_count",
            now=ts,
            bootstrap_superadmins=bootstrap_superadmins,
        )

    blocks = [
        RichBlock(kind="heading", text=sample_title, level=1),
        *parse_rich_markdown_to_blocks(sample_markdown),
    ]
    fallback_html = render_blocks_to_telegram_html(blocks)
    rich_payload = render_rich_message_payload(blocks)

    try:
        if settings.rich_message_formatting_enabled:
            resp = await bot_api.send_rich_message(
                user_id, blocks=rich_payload, fallback_html=fallback_html
            )
        else:
            resp = await bot_api.send_message(user_id, fallback_html, parse_mode="HTML")
        return int(resp.get("message_id", 1))
    except (TransientError, PermanentError):
        async with db.transaction() as conn:
            await release_daily_quota(conn, user_id=user_id, counter="tests_count")
        raise
