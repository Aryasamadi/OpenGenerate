from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from opengenerate.domain.channels import (
    ActiveHoursWindow,
    ChannelSettings,
    ScoringWeights,
    WatermarkSettings,
    generate_transfer_token,
    hash_transfer_token,
    validate_token_ttl_hours,
)
from opengenerate.domain.errors import ValidationFailed
from opengenerate.domain.sources import (
    canonicalize_url,
    compute_content_fingerprint,
    compute_source_cooldown,
    compute_url_hash,
    detect_ad_heuristic,
    is_article_fresh,
    is_near_duplicate,
    matches_topic_filters,
)
from opengenerate.infrastructure.discovery import (
    autodiscover_feeds,
    discover_from_source,
    extract_html_listing_links,
    parse_json_feed_or_api,
    parse_rss_or_atom_feed,
    parse_xml_sitemap,
)
from opengenerate.infrastructure.extractor import (
    extract_article_from_html,
    extract_full_article,
    is_valid_article_media_url,
)
from opengenerate.infrastructure.http_safe import SafeResponse


class _FakeHttp:
    def __init__(self, routes: dict[str, SafeResponse]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, **kwargs: Any) -> SafeResponse:
        hdrs = dict(kwargs.get("headers") or {})
        self.calls.append((url, hdrs))
        if url not in self.routes:
            raise RuntimeError(f"404 not found: {url}")
        return self.routes[url]


def _resp(
    url: str,
    body: str,
    *,
    status_code: int = 200,
    content_type: str = "text/html; charset=utf-8",
    extra_headers: dict[str, str] | None = None,
) -> SafeResponse:
    h = {"content-type": content_type}
    if extra_headers:
        h.update(extra_headers)
    return SafeResponse(
        status_code=status_code,
        headers=httpx.Headers(h),
        content=body.encode("utf-8"),
        url=url,
    )


def test_channel_settings_24_fields_roundtrip_and_active_hours() -> None:
    cfg = ChannelSettings(
        publish_interval_minutes=30,
        posts_per_cycle=2,
        max_article_age_days=5,
        min_score_threshold=75,
        language="fa",
        writing_style="scientific_analytical",
        post_length="long",
        content_mode="continuation_bot",
        source_link_credit_enabled=True,
        continuation_in_bot_enabled=True,
        read_more_button_text="ادامه مقاله علمی",
        channel_signature_footer="@SciChannel",
        post_prefix_header="🔬 گزارش ویژه",
        hashtags_enabled=True,
        auto_categorization_enabled=True,
        custom_categories=("فیزیک کوانتوم", "هوش مصنوعی"),
        topic_keywords_include=("کوانتوم", "مدل زبانی"),
        topic_keywords_exclude=("تبلیغات", "ارز دیجیتال"),
        ad_detection_enabled=True,
        scoring_weights=ScoringWeights(
            relevance=0.3, novelty=0.2, depth=0.2, clarity=0.1, credibility=0.1, engagement=0.1
        ),
        media_mode="single_image",
        watermark=WatermarkSettings(
            enabled=True, text_or_asset="@SciChannel", position="bottom_right", opacity=80
        ),
        active_hours=ActiveHoursWindow(
            quiet_hours_enabled=True, start_hour=8, end_hour=22, timezone="UTC"
        ),
        rich_message_formatting_enabled=True,
    )
    restored = ChannelSettings.from_dict(cfg.to_dict())
    assert restored == cfg
    assert sum(restored.scoring_weights.normalized().values()) == pytest.approx(1.0)

    assert restored.active_hours.is_open_at(datetime(2026, 6, 1, 12, 0, tzinfo=UTC))
    assert not restored.active_hours.is_open_at(datetime(2026, 6, 1, 3, 0, tzinfo=UTC))

    raw_tok, tok_hash = generate_transfer_token()
    assert raw_tok.startswith("ogch_")
    assert hash_transfer_token(raw_tok) == tok_hash
    assert validate_token_ttl_hours(24) == 24
    with pytest.raises(ValidationFailed):
        validate_token_ttl_hours(200)


def test_url_canonicalization_simhash_ad_heuristic_and_backoff() -> None:
    u1 = "HTTPS://Example.COM:443/news//ai-breakthrough/?utm_source=tg&b=2&a=1&fbclid=xyz#section"
    u2 = "https://example.com/news/ai-breakthrough?a=1&b=2"
    assert canonicalize_url(u1) == u2
    assert compute_url_hash(u1) == compute_url_hash(u2)

    # Near-duplicate detection via 64-bit SimHash
    title_a = "پژوهشگران دانشگاه صنعتی شریف مدل زبانی جدیدی برای تحلیل متون علمی معرفی کردند"
    body_a = (
        "تیم پژوهشی هوش مصنوعی دانشگاه صنعتی شریف امروز از یک معماری نوین پردازش زبان طبیعی "
        "رونمایی کرد که دقت استخراج جداول و فرمول‌های علمی را در مقالات فارسی به شکل چشمگیری "
        "افزایش می‌دهد و هزینه محاسباتی را تا چهل درصد کاهش می‌دهد."
    )
    body_b = (
        "تیم پژوهشی هوش مصنوعی دانشگاه صنعتی شریف امروز از یک معماری نوین پردازش زبان طبیعی "
        "رونمایی کرد که دقت استخراج جداول و فرمول‌های علمی را در مقالات فارسی به شکل چشمگیری "
        "افزایش می‌دهد و هزینه محاسباتی را تا ۴۰ درصد کاهش می‌دهد."
    )
    fp_a = compute_content_fingerprint(title_a, body_a)
    fp_b = compute_content_fingerprint(title_a, body_b)
    fp_diff = compute_content_fingerprint(
        "گزارش وضعیت آب و هوای استان‌های شمالی",
        "بارش باران پاییزی از فردا در سواحل دریای خزر آغاز می‌شود و دمای هوا کاهش می‌یابد.",
    )
    assert is_near_duplicate(fp_a, fp_b)
    assert not is_near_duplicate(fp_a, fp_diff)

    is_ad, reason = detect_ad_heuristic(
        "رپورتاژ آگهی: بهترین صرافی خرید ارز", "همین حالا بخرید با کد تخفیف ویژه"
    )
    assert is_ad and reason is not None

    ok, _ = matches_topic_filters(
        title_a, body_a, include_keywords=["هوش مصنوعی"], exclude_keywords=["تبلیغ"]
    )
    assert ok

    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    assert is_article_fresh(now - timedelta(days=2), now=now, max_age_days=7)
    assert not is_article_fresh(now - timedelta(days=10), now=now, max_age_days=7)
    cd = compute_source_cooldown(3, now=now)
    assert cd == now + timedelta(seconds=240)


@pytest.mark.asyncio
async def test_five_strategy_discovery_engine_and_conditional_304() -> None:
    base = "https://sci.example.com"
    html_home = """
    <html>
      <head>
        <link rel="alternate" type="application/rss+xml" href="/feed.xml" />
      </head>
      <body>
        <nav><a href="/login">ورود</a><a href="/tag/physics">فیزیک</a></nav>
        <main>
          <article><h2><a href="/articles/quantum-computing-2026">پیشرفت بزرگ در محاسبات کوانتومی سال ۲۰۲۶</a></h2></article>
        </main>
      </body>
    </html>
    """
    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE rss [<!ENTITY xxe "ignored">]>
    <rss version="2.0">
      <channel>
        <title>Science Feed</title>
        <item>
          <title>کشف سیاره فراخورشیدی جدید</title>
          <link>https://sci.example.com/articles/exoplanet-2026?utm_campaign=rss</link>
          <description>جزئیات رصد تلسکوپ جیمز وب از سیاره سنگی جدید.</description>
          <pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate>
          <enclosure url="https://sci.example.com/media/exoplanet.jpg" type="image/jpeg" />
        </item>
      </channel>
    </rss>
    """
    json_feed = """
    {
      "version": "https://jsonfeed.org/version/1.1",
      "items": [
        {
          "id": "1",
          "url": "https://sci.example.com/articles/crispr-therapy",
          "title": "درمان ژنتیکی نوین با CRISPR",
          "content_text": "نتایج کارآزمایی بالینی فاز سوم منتشر شد.",
          "date_published": "2026-06-01T09:30:00Z",
          "image": "https://sci.example.com/media/crispr.png"
        }
      ]
    }
    """
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://sci.example.com/articles/fusion-energy</loc>
        <lastmod>2026-06-01T08:00:00Z</lastmod>
      </url>
    </urlset>
    """

    # Strategy 1: Autodiscovery
    assert autodiscover_feeds(html_home, base) == ["https://sci.example.com/feed.xml"]

    # Strategy 2: RSS/Atom
    rss_items = parse_rss_or_atom_feed(rss_xml, base)
    assert len(rss_items) == 1
    assert rss_items[0].canonical_url == "https://sci.example.com/articles/exoplanet-2026"
    assert rss_items[0].media_urls == ("https://sci.example.com/media/exoplanet.jpg",)

    # Strategy 3: JSON Feed
    jf_items = parse_json_feed_or_api(json_feed, base)
    assert len(jf_items) == 1
    assert jf_items[0].title == "درمان ژنتیکی نوین با CRISPR"

    # Strategy 4: XML Sitemap
    sm_items, children = parse_xml_sitemap(sitemap_xml, base)
    assert len(sm_items) == 1 and not children
    assert sm_items[0].canonical_url == "https://sci.example.com/articles/fusion-energy"

    # Strategy 5: Intelligent HTML listing
    html_items = extract_html_listing_links(html_home, base)
    assert len(html_items) == 1
    assert html_items[0].canonical_url == "https://sci.example.com/articles/quantum-computing-2026"

    # Orchestrator auto-discovery + conditional 304
    fake_http = _FakeHttp(
        {
            base: _resp(base, html_home, extra_headers={"etag": '"v1"'}),
            f"{base}/feed.xml": _resp(
                f"{base}/feed.xml", rss_xml, content_type="application/rss+xml"
            ),
            f"{base}/not-modified": _resp(f"{base}/not-modified", "", status_code=304),
        }
    )
    out = await discover_from_source(fake_http, source_url=base, discovery_type="auto")
    assert out.strategy_used == "autodiscovered_rss"
    assert len(out.items) == 1

    out_304 = await discover_from_source(
        fake_http, source_url=f"{base}/not-modified", etag='"v1"'
    )
    assert out_304.not_modified and out_304.items == []


@pytest.mark.asyncio
async def test_multipage_article_extraction_and_junk_media_filtering() -> None:
    p1_url = "https://sci.example.com/articles/deep-study"
    p2_url = "https://sci.example.com/articles/deep-study?page=2"
    p1_html = """
    <html>
      <head>
        <meta property="og:title" content="بررسی جامع شبکه‌های عصبی گراف" />
        <meta property="article:published_time" content="2026-06-01T11:00:00Z" />
        <meta property="og:image" content="https://sci.example.com/images/gnn-architecture.png" />
        <link rel="next" href="/articles/deep-study?page=2" />
      </head>
      <body>
        <div class="cookie-banner">این سایت از کوکی استفاده می‌کند برای تجربه بهتر شما کاربران گرامی.</div>
        <article>
          <p>بخش اول این پژوهش به بررسی ساختار ریاضی شبکه‌های عصبی گراف و کاربردهای آن در شیمی محاسباتی می‌پردازد.</p>
          <img src="https://sci.example.com/pixel.gif" width="1" height="1" />
          <img src="https://sci.example.com/assets/logo.png" />
          <img src="https://sci.example.com/images/molecule-graph.jpg" width="800" height="600" />
        </article>
      </body>
    </html>
    """
    p2_html = """
    <html>
      <body>
        <article>
          <p>در بخش دوم، نتایج آزمایش‌های تجربی روی داده‌های مولکولی نشان می‌دهد خطای پیش‌بینی انرژی پیوند تا سی درصد کاهش یافته است.</p>
          <video src="https://sci.example.com/videos/simulation.mp4"></video>
        </article>
      </body>
    </html>
    """
    assert not is_valid_article_media_url("https://sci.example.com/pixel.gif", width="1", height="1")
    assert not is_valid_article_media_url("https://sci.example.com/assets/logo.png")
    assert is_valid_article_media_url("https://sci.example.com/images/gnn-architecture.png")

    single = extract_article_from_html(p1_html, p1_url)
    assert single.title == "بررسی جامع شبکه‌های عصبی گراف"
    assert "کوکی" not in single.body
    assert single.next_page_url == p2_url

    fake_http = _FakeHttp({p1_url: _resp(p1_url, p1_html), p2_url: _resp(p2_url, p2_html)})
    full = await extract_full_article(fake_http, p1_url)
    assert full.pages_fetched == 2
    assert "بخش اول" in full.body and "بخش دوم" in full.body
    assert full.media_urls == (
        "https://sci.example.com/images/gnn-architecture.png",
        "https://sci.example.com/images/molecule-graph.jpg",
        "https://sci.example.com/videos/simulation.mp4",
    )
