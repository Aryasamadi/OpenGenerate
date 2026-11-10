"""PostgreSQL persistence & Orchestration for Multi-Provider AI Models, Failover, Caching & Editorial Scoring."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opengenerate.application.policy import require_active, require_owner, require_superadmin
from opengenerate.application.ports import LlmRequest, LlmResponse
from opengenerate.domain.channels import ChannelSettings
from opengenerate.domain.editorial import (
    VALID_PRESENTATION_FORMATS,
    PresentationFormat,
    RichBlock,
    ScientificDomain,
    StructuredArticleOutput,
    compute_ai_cache_key,
    compute_weighted_score,
    detect_scientific_domain_and_format,
    format_channel_post,
    parse_rich_markdown_to_blocks,
    render_blocks_to_telegram_html,
)
from opengenerate.domain.errors import (
    AccessDenied,
    NotFound,
    PermanentError,
    TransientError,
    ValidationFailed,
)
from opengenerate.domain.roles import Actor, Role
from opengenerate.infrastructure.crypto import FieldCipher

ProviderType = Literal["openai", "anthropic", "gemini"]
VALID_PROVIDERS: frozenset[str] = frozenset({"openai", "anthropic", "gemini"})
QUARANTINE_THRESHOLD = 3
QUARANTINE_DURATION = timedelta(minutes=15)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class LlmClientPort(Protocol):
    async def complete(self, request: LlmRequest) -> LlmResponse: ...


@dataclass(frozen=True, slots=True)
class AiModelRecord:
    model_id: int
    owner_id: int | None
    provider_type: ProviderType
    name: str
    base_url: str
    has_api_key: bool
    unauthenticated: bool
    model_identifier: str
    priority: int
    temperature: float
    max_tokens: int
    is_active: bool
    is_default_test: bool
    health_status: Literal["ok", "degraded", "unknown"]
    failure_count: int
    quarantined_until: datetime | None


def _row_to_model(row: Any) -> AiModelRecord:
    m = row._mapping
    return AiModelRecord(
        model_id=int(m["model_id"]),
        owner_id=int(m["owner_id"]) if m["owner_id"] is not None else None,
        provider_type=m["provider_type"],
        name=str(m["name"]),
        base_url=str(m["base_url"]),
        has_api_key=bool(m["api_key_enc"]),
        unauthenticated=bool(m["unauthenticated"]),
        model_identifier=str(m["model_identifier"]),
        priority=int(m["priority"]),
        temperature=float(m["temperature"]),
        max_tokens=int(m["max_tokens"]),
        is_active=bool(m["is_active"]),
        is_default_test=bool(m["is_default_test"]),
        health_status=m["health_status"],
        failure_count=int(m["failure_count"]),
        quarantined_until=m["quarantined_until"],
    )


async def register_ai_model(
    conn: AsyncConnection,
    *,
    actor: Actor,
    provider_type: ProviderType,
    name: str,
    base_url: str,
    model_identifier: str,
    api_key: str | None = None,
    unauthenticated: bool = False,
    is_global: bool = False,
    priority: int = 100,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    is_default_test: bool = False,
    keyring: FieldCipher | None = None,
) -> AiModelRecord:
    """Register a global (superadmin) or personal (admin) AI model with encrypted API key."""
    require_active(actor)
    if is_global:
        require_superadmin(actor)
        owner_id: int | None = None
    else:
        if actor.role not in (Role.ADMIN, Role.SUPERADMIN):
            raise AccessDenied("admin or superadmin required to register personal AI models")
        owner_id = actor.user_id

    if provider_type not in VALID_PROVIDERS:
        raise ValidationFailed(f"invalid provider_type: {provider_type}")
    if not name.strip() or not base_url.strip() or not model_identifier.strip():
        raise ValidationFailed("name, base_url, and model_identifier are required")
    if not 0.0 <= temperature <= 2.0:
        raise ValidationFailed("temperature must be between 0.0 and 2.0")
    if max_tokens <= 0:
        raise ValidationFailed("max_tokens must be positive")

    if unauthenticated and not actor.is_superadmin:
        raise AccessDenied("only superadmin can register unauthenticated AI endpoints")
    if not unauthenticated and not api_key:
        raise ValidationFailed("api_key is required for authenticated AI models")

    enc_key: str | None = None
    if api_key:
        if keyring is None:
            raise ValidationFailed("keyring is required to encrypt api_key")
        enc_key = keyring.encrypt(api_key)

    if is_default_test and is_global:
        await conn.execute(
            text("UPDATE ai_models SET is_default_test = FALSE WHERE owner_id IS NULL")
        )

    row = (
        await conn.execute(
            text(
                "INSERT INTO ai_models("
                "owner_id, provider_type, name, base_url, api_key_enc, unauthenticated, "
                "model_identifier, priority, temperature, max_tokens, is_active, is_default_test) "
                "VALUES (:o, :pt, :n, :bu, :enc, :unauth, :mid, :prio, :temp, :mt, TRUE, :def) "
                "RETURNING *"
            ),
            {
                "o": owner_id,
                "pt": provider_type,
                "n": name.strip(),
                "bu": base_url.strip(),
                "enc": enc_key,
                "unauth": unauthenticated,
                "mid": model_identifier.strip(),
                "prio": priority,
                "temp": temperature,
                "mt": max_tokens,
                "def": is_default_test,
            },
        )
    ).one()
    return _row_to_model(row)


async def list_ai_models(
    conn: AsyncConnection,
    *,
    actor: Actor,
    include_global: bool = True,
) -> list[AiModelRecord]:
    require_active(actor)
    if include_global:
        rows = await conn.execute(
            text(
                "SELECT * FROM ai_models WHERE owner_id = :u OR owner_id IS NULL "
                "ORDER BY (owner_id IS NULL) ASC, priority ASC, model_id ASC"
            ),
            {"u": actor.user_id},
        )
    else:
        rows = await conn.execute(
            text("SELECT * FROM ai_models WHERE owner_id = :u ORDER BY priority ASC, model_id ASC"),
            {"u": actor.user_id},
        )
    return [_row_to_model(r) for r in rows]


async def delete_ai_model(
    conn: AsyncConnection,
    *,
    actor: Actor,
    model_id: int,
) -> None:
    require_active(actor)
    row = (
        await conn.execute(text("SELECT owner_id FROM ai_models WHERE model_id = :m"), {"m": model_id})
    ).first()
    if row is None:
        raise NotFound("ai model not found")
    owner_id = row._mapping["owner_id"]
    if owner_id is None:
        require_superadmin(actor)
    else:
        require_owner(actor, int(owner_id), allow_superadmin=True)
    await conn.execute(text("DELETE FROM ai_models WHERE model_id = :m"), {"m": model_id})


async def record_model_probe_result(
    conn: AsyncConnection,
    *,
    model_id: int,
    succeeded: bool,
    now: datetime | None = None,
) -> AiModelRecord:
    ts = now or datetime.now(UTC)
    if succeeded:
        row = (
            await conn.execute(
                text(
                    "UPDATE ai_models SET health_status = 'ok', failure_count = 0, "
                    "quarantined_until = NULL, last_probe_at = :now, updated_at = :now "
                    "WHERE model_id = :m RETURNING *"
                ),
                {"now": ts, "m": model_id},
            )
        ).one()
        return _row_to_model(row)

    cur = (
        await conn.execute(
            text("SELECT failure_count FROM ai_models WHERE model_id = :m FOR UPDATE"),
            {"m": model_id},
        )
    ).one()
    fc = int(cur._mapping["failure_count"]) + 1
    status = "degraded" if fc >= QUARANTINE_THRESHOLD else "unknown"
    quarantined = ts + QUARANTINE_DURATION if fc >= QUARANTINE_THRESHOLD else None
    row = (
        await conn.execute(
            text(
                "UPDATE ai_models SET health_status = :st, failure_count = :fc, "
                "quarantined_until = :q, last_probe_at = :now, updated_at = :now "
                "WHERE model_id = :m RETURNING *"
            ),
            {"st": status, "fc": fc, "q": quarantined, "now": ts, "m": model_id},
        )
    ).one()
    return _row_to_model(row)


async def get_prioritized_models(
    conn: AsyncConnection,
    *,
    user_id: int,
    now: datetime | None = None,
) -> list[Any]:
    """Return active non-quarantined personal models followed by global models in priority order."""
    ts = now or datetime.now(UTC)
    rows = await conn.execute(
        text(
            "SELECT * FROM ai_models "
            "WHERE is_active = TRUE AND (owner_id = :u OR owner_id IS NULL) "
            "AND (quarantined_until IS NULL OR quarantined_until <= :now) "
            "ORDER BY (owner_id IS NULL) ASC, priority ASC, model_id ASC"
        ),
        {"u": user_id, "now": ts},
    )
    return list(rows)


def _build_editorial_prompts(
    *,
    title: str,
    raw_body: str,
    settings: ChannelSettings,
    inferred_domain: ScientificDomain,
    inferred_format: PresentationFormat,
) -> tuple[str, str]:
    system_prompt = (
        "You are OpenGenerate's senior scientific and editorial AI engine. "
        "Analyze the article, detect whether it is an advertisement, classify its scientific discipline "
        f"(default hint: '{inferred_domain}'), select the optimal channel presentation_format "
        f"from ['tabular','itemized','sequential','statistical','analytical','narrative'] (default hint: '{inferred_format}'), "
        "score it 0..100 on 6 criteria (relevance, novelty, depth, clarity, credibility, engagement), "
        f"write the channel post in language '{settings.language}' with style '{settings.writing_style}' "
        f"and length '{settings.post_length}', and provide a comprehensive Rich Markdown continuation "
        "(`continuation_markdown`) for the bot reader using tables, expandable details (`:::details`), "
        "quotes, code blocks, or lists as appropriate. Respond strictly with valid JSON."
    )
    user_prompt = json.dumps(
        {
            "title": title,
            "body": raw_body[:12_000],
            "custom_categories": list(settings.custom_categories),
            "required_json_keys": [
                "is_advertisement",
                "ad_reason",
                "scientific_domain",
                "presentation_format",
                "category_name",
                "criteria_scores",
                "generated_title",
                "generated_post_body",
                "comparison_table_headers",
                "comparison_table_rows",
                "key_points",
                "hashtags",
                "continuation_markdown",
                "continuation_blocks",
            ],
        },
        ensure_ascii=False,
    )
    return system_prompt, user_prompt


def _parse_structured_ai_json(
    raw_text: str,
    *,
    fallback_title: str,
    fallback_body: str,
    settings: ChannelSettings,
) -> StructuredArticleOutput:
    cleaned = raw_text.strip()
    m = _JSON_BLOCK_RE.search(cleaned)
    if m:
        cleaned = m.group(1)
    try:
        data = json.loads(cleaned)
    except ValueError as exc:
        raise PermanentError("AI model did not return valid JSON") from exc
    if not isinstance(data, dict):
        raise PermanentError("AI JSON root must be an object")

    inf_domain, inf_fmt = detect_scientific_domain_and_format(fallback_title, fallback_body)
    raw_domain = str(data.get("scientific_domain") or inf_domain)
    domain: ScientificDomain = (
        raw_domain  # type: ignore[assignment]
        if raw_domain
        in {
            "medicine_clinical",
            "computer_science_ai",
            "physics_engineering",
            "chemistry_materials",
            "economics_finance",
            "mathematics_statistics",
            "humanities_law",
            "tutorial_howto",
            "general",
        }
        else inf_domain
    )
    raw_fmt = str(data.get("presentation_format") or inf_fmt)
    fmt: PresentationFormat = (
        raw_fmt  # type: ignore[assignment]
        if raw_fmt in VALID_PRESENTATION_FORMATS
        else inf_fmt
    )

    raw_scores = data.get("criteria_scores")
    scores_map: dict[str, float] = {}
    if isinstance(raw_scores, dict):
        for k in ("relevance", "novelty", "depth", "clarity", "credibility", "engagement"):
            scores_map[k] = float(raw_scores.get(k, 75.0))
    else:
        for k in ("relevance", "novelty", "depth", "clarity", "credibility", "engagement"):
            scores_map[k] = 75.0

    weighted = compute_weighted_score(scores_map, settings.scoring_weights)

    cont_blocks: list[RichBlock] = []
    if isinstance(data.get("continuation_blocks"), list):
        for b_raw in data["continuation_blocks"]:
            if isinstance(b_raw, dict):
                cont_blocks.append(RichBlock.from_dict(b_raw))
    if not cont_blocks and isinstance(data.get("continuation_markdown"), str):
        cont_blocks = parse_rich_markdown_to_blocks(data["continuation_markdown"])
    if not cont_blocks:
        cont_blocks = parse_rich_markdown_to_blocks(
            f"# {data.get('generated_title') or fallback_title}\n\n{fallback_body}"
        )

    headers = tuple(str(h) for h in (data.get("comparison_table_headers") or ()))
    rows = tuple(
        tuple(str(c) for c in r)
        for r in (data.get("comparison_table_rows") or ())
        if isinstance(r, list)
    )
    key_points = tuple(str(p) for p in (data.get("key_points") or ()))
    hashtags = tuple(str(t) for t in (data.get("hashtags") or ()))

    return StructuredArticleOutput(
        is_advertisement=bool(data.get("is_advertisement", False)),
        ad_reason=str(data["ad_reason"]) if data.get("ad_reason") else None,
        scientific_domain=domain,
        presentation_format=fmt,
        category_name=str(data.get("category_name") or domain),
        criteria_scores=scores_map,
        weighted_score=weighted,
        generated_title=str(data.get("generated_title") or fallback_title),
        generated_post_body=str(data.get("generated_post_body") or fallback_body[:400]),
        comparison_table_headers=headers,
        comparison_table_rows=rows,
        key_points=key_points,
        hashtags=hashtags,
        continuation_blocks=tuple(cont_blocks),
    )


async def process_article_with_ai(
    conn: AsyncConnection,
    *,
    article_id: int,
    llm_client: LlmClientPort,
    keyring: FieldCipher | None = None,
    now: datetime | None = None,
) -> StructuredArticleOutput:
    """Run multi-provider AI orchestration with `ai_cache`, failover, quarantine, and domain-adaptive formatting."""
    ts = now or datetime.now(UTC)
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

    cache_key = compute_ai_cache_key(
        canonical_url=str(am["canonical_url"]),
        content_fingerprint=am["content_fingerprint"],
        settings=settings,
    )

    # 1) Check `ai_cache` for a valid cached response
    cached_row = (
        await conn.execute(
            text(
                "SELECT response_json, model_identifier FROM ai_cache "
                "WHERE cache_key = :k AND expires_at > :now"
            ),
            {"k": cache_key, "now": ts},
        )
    ).first()

    raw_response_text: str | None = None
    model_used_label = "cache"
    if cached_row is not None:
        r_val = cached_row._mapping["response_json"]
        raw_response_text = r_val if isinstance(r_val, str) else json.dumps(r_val)
        model_used_label = f"cache:{cached_row._mapping['model_identifier']}"
    else:
        # 2) Iterate through prioritized personal + global models with failover
        candidates = await get_prioritized_models(conn, user_id=owner_id, now=ts)
        if not candidates:
            await conn.execute(
                text(
                    "UPDATE articles SET status = 'ai_retryable', failure_class = 'no_active_models', "
                    "retry_count = retry_count + 1, updated_at = now() WHERE article_id = :aid"
                ),
                {"aid": article_id},
            )
            raise TransientError("no active non-quarantined AI models available")

        inf_domain, inf_fmt = detect_scientific_domain_and_format(
            str(am["title"]), str(am["raw_body"])
        )
        sys_prompt, usr_prompt = _build_editorial_prompts(
            title=str(am["title"]),
            raw_body=str(am["raw_body"]),
            settings=settings,
            inferred_domain=inf_domain,
            inferred_format=inf_fmt,
        )

        last_err: Exception | None = None
        for m_row in candidates:
            mm = m_row._mapping
            mid = int(mm["model_id"])
            api_key: str | None = None
            if mm["api_key_enc"] and not bool(mm["unauthenticated"]):
                if keyring is None:
                    continue
                api_key = keyring.decrypt(str(mm["api_key_enc"]))

            req = LlmRequest(
                provider_type=mm["provider_type"],
                base_url=str(mm["base_url"]),
                api_key=api_key,
                model=str(mm["model_identifier"]),
                system_prompt=sys_prompt,
                user_prompt=usr_prompt,
                temperature=float(mm["temperature"]),
                max_tokens=int(mm["max_tokens"]),
            )
            t0 = time.perf_counter()
            try:
                resp = await llm_client.complete(req)
                latency_ms = int((time.perf_counter() - t0) * 1000)
                await record_model_probe_result(conn, model_id=mid, succeeded=True, now=ts)
                total_tok = resp.prompt_tokens + resp.completion_tokens
                await conn.execute(
                    text(
                        "INSERT INTO ai_usage("
                        "user_id, channel_id, model_id, article_id, prompt_tokens, "
                        "completion_tokens, estimated_cost_usd, latency_ms, status) "
                        "VALUES (:u, :c, :m, :a, :pt, :ct, 0, :lat, 'ok')"
                    ),
                    {
                        "u": owner_id,
                        "c": channel_id,
                        "m": mid,
                        "a": article_id,
                        "pt": resp.prompt_tokens,
                        "ct": resp.completion_tokens,
                        "lat": latency_ms,
                    },
                )
                await conn.execute(
                    text(
                        "INSERT INTO usage(user_id, usage_date, ai_calls_count, ai_tokens_count) "
                        "VALUES (:u, CURRENT_DATE, 1, :tok) "
                        "ON CONFLICT (user_id, usage_date) DO UPDATE SET "
                        "ai_calls_count = usage.ai_calls_count + 1, "
                        "ai_tokens_count = usage.ai_tokens_count + :tok"
                    ),
                    {"u": owner_id, "tok": total_tok},
                )
                raw_response_text = resp.text
                model_used_label = resp.model
                # Store in `ai_cache` for 7 days
                with_json = json.loads(_JSON_BLOCK_RE.sub(r"\1", resp.text.strip()))
                await conn.execute(
                    text(
                        "INSERT INTO ai_cache(cache_key, response_json, model_identifier, expires_at) "
                        "VALUES (:k, CAST(:rj AS jsonb), :mid, :exp) "
                        "ON CONFLICT (cache_key) DO UPDATE SET "
                        "response_json = EXCLUDED.response_json, expires_at = EXCLUDED.expires_at"
                    ),
                    {
                        "k": cache_key,
                        "rj": json.dumps(with_json),
                        "mid": resp.model,
                        "exp": ts + timedelta(days=7),
                    },
                )
                break
            except Exception as exc:
                last_err = exc
                latency_ms = int((time.perf_counter() - t0) * 1000)
                await record_model_probe_result(conn, model_id=mid, succeeded=False, now=ts)
                await conn.execute(
                    text(
                        "INSERT INTO ai_usage("
                        "user_id, channel_id, model_id, article_id, prompt_tokens, "
                        "completion_tokens, estimated_cost_usd, latency_ms, status) "
                        "VALUES (:u, :c, :m, :a, 0, 0, 0, :lat, 'error')"
                    ),
                    {"u": owner_id, "c": channel_id, "m": mid, "a": article_id, "lat": latency_ms},
                )
                continue

        if raw_response_text is None:
            new_retry = int(am["retry_count"]) + 1
            new_status = "ai_exhausted" if new_retry >= 5 else "ai_retryable"
            await conn.execute(
                text(
                    "UPDATE articles SET status = :st, failure_class = :fc, "
                    "retry_count = :rc, updated_at = now() WHERE article_id = :aid"
                ),
                {
                    "st": new_status,
                    "fc": type(last_err).__name__ if last_err else "ai_error",
                    "rc": new_retry,
                    "aid": article_id,
                },
            )
            raise TransientError("all candidate AI models failed") from last_err

    structured = _parse_structured_ai_json(
        raw_response_text,
        fallback_title=str(am["title"]),
        fallback_body=str(am["raw_body"]),
        settings=settings,
    )

    if settings.ad_detection_enabled and structured.is_advertisement:
        await conn.execute(
            text(
                "UPDATE articles SET status = 'rejected_ai_ad', rejection_reason = :rr, "
                "scientific_domain = :sd, presentation_format = :pf, ai_model_used = :mu, "
                "updated_at = now() WHERE article_id = :aid"
            ),
            {
                "rr": structured.ad_reason or "ai_detected_advertisement",
                "sd": structured.scientific_domain,
                "pf": structured.presentation_format,
                "mu": model_used_label,
                "aid": article_id,
            },
        )
        return structured

    if structured.weighted_score < settings.min_score_threshold:
        await conn.execute(
            text(
                "UPDATE articles SET status = 'rejected_low_score', "
                "rejection_reason = :rr, score = :sc, "
                "criteria_scores_json = CAST(:cs AS jsonb), "
                "scientific_domain = :sd, presentation_format = :pf, ai_model_used = :mu, "
                "updated_at = now() WHERE article_id = :aid"
            ),
            {
                "rr": f"score_{structured.weighted_score}_below_{settings.min_score_threshold}",
                "sc": structured.weighted_score,
                "cs": json.dumps(structured.criteria_scores),
                "sd": structured.scientific_domain,
                "pf": structured.presentation_format,
                "mu": model_used_label,
                "aid": article_id,
            },
        )
        return structured

    channel_post_html, _channel_blocks = format_channel_post(
        structured, settings=settings, source_url=str(am["canonical_url"])
    )
    full_continuation_html = render_blocks_to_telegram_html(structured.continuation_blocks)
    rich_blocks_payload = [b.to_dict() for b in structured.continuation_blocks]

    await conn.execute(
        text(
            "UPDATE articles SET "
            "scientific_domain = :sd, presentation_format = :pf, "
            "generated_title = :gt, generated_post = :gp, generated_full = :gf, "
            "rich_blocks_json = CAST(:rb AS jsonb), category_name = :cat, "
            "score = :sc, criteria_scores_json = CAST(:cs AS jsonb), "
            "status = 'ready', ai_model_used = :mu, updated_at = now() "
            "WHERE article_id = :aid"
        ),
        {
            "sd": structured.scientific_domain,
            "pf": structured.presentation_format,
            "gt": structured.generated_title,
            "gp": channel_post_html,
            "gf": full_continuation_html,
            "rb": json.dumps(rich_blocks_payload),
            "cat": structured.category_name,
            "sc": structured.weighted_score,
            "cs": json.dumps(structured.criteria_scores),
            "mu": model_used_label,
            "aid": article_id,
        },
    )
    return structured
