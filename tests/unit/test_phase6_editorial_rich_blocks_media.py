from __future__ import annotations

import pytest

from opengenerate.domain.channels import ChannelSettings, ScoringWeights, WatermarkSettings
from opengenerate.domain.editorial import (
    StructuredArticleOutput,
    chunk_telegram_html,
    compute_weighted_score,
    detect_scientific_domain_and_format,
    format_channel_post,
    parse_rich_markdown_to_blocks,
    render_blocks_to_telegram_html,
)
from opengenerate.infrastructure.media import prepare_article_media


def test_scientific_domain_and_presentation_format_classification() -> None:
    dom_ai, fmt_ai = detect_scientific_domain_and_format(
        "مقایسه بنچمارک مدل‌های زبانی بزرگ و معماری ترنسفورمر",
        "در این مقاله سرعت استنتاج و latency مدل‌های شبکه عصبی روی GPU مقایسه شده است.",
    )
    assert dom_ai == "computer_science_ai"
    assert fmt_ai == "tabular"

    dom_med, fmt_med = detect_scientific_domain_and_format(
        "نتایج کارآزمایی بالینی واکسن و درمان ژنتیکی جدید",
        "بررسی اثر دارو روی بیماران در فاز سوم بالینی.",
    )
    assert dom_med == "medicine_clinical"
    assert fmt_med == "itemized"

    dom_how, fmt_how = detect_scientific_domain_and_format(
        "آموزش گام‌به‌گام اجرای پروتکل آزمایش",
        "گام اول آماده‌سازی نمونه؛ مراحل بعدی به ترتیب اجرا می‌شوند.",
    )
    assert dom_how == "tutorial_howto"
    assert fmt_how == "sequential"

    dom_econ, fmt_econ = detect_scientific_domain_and_format(
        "گزارش آماری نرخ تورم و تولید ناخالص داخلی",
        "میانگین رشد اقتصادی در فصل گذشته ۳.۸ درصد گزارش شده است.",
    )
    assert dom_econ == "economics_finance"
    assert fmt_econ == "statistical"

    dom_phys, fmt_phys = detect_scientific_domain_and_format(
        "تحلیل نظری درهم‌تنیدگی کوانتومی و ترمودینامیک سیاه‌چاله",
        "بررسی معادلات میدان در فیزیک نظری.",
    )
    assert dom_phys == "physics_engineering"
    assert fmt_phys == "analytical"


def test_rich_markdown_parsing_and_lossless_html_rendering() -> None:
    md = """# واکاوی معماری کوانتومی جدید

این پژوهش توسط **دانشگاه MIT** منتشر شده و کد شبیه‌سازی در `qsim.py` قرار دارد.

| مدل | دقت | زمان اجرا |
| --- | --- | --- |
| Q-Net | 98.4% | 12ms |
| Base | 91.0% | 45ms |

:::details روش‌شناسی و اثبات ریاضی
فرمولبندی ماتریس چگالی در فضای هیلبرت با تقریب مرتبه دوم انجام شده است.
:::

```python
def fidelity(rho, sigma):
    return (rho @ sigma).trace()
```

> این دستاورد نقطه عطفی در محاسبات کوانتومی خطاپذیر محسوب می‌شود.

1. کالیبراسیون کیوبیت‌ها
2. اجرای گیت‌های دوکیوبیتی
---
"""
    blocks = parse_rich_markdown_to_blocks(md)
    kinds = [b.kind for b in blocks]
    assert kinds == [
        "heading",
        "paragraph",
        "table",
        "collapsible",
        "code",
        "quote",
        "list",
        "divider",
    ]

    html_out = render_blocks_to_telegram_html(blocks)
    assert "<b>📌 واکاوی معماری کوانتومی جدید</b>" in html_out
    assert "<b>دانشگاه MIT</b>" in html_out
    assert "<code>qsim.py</code>" in html_out
    assert "<pre>┌─" in html_out and "Q-Net" in html_out
    assert "<blockquote expandable><b>▾ روش‌شناسی و اثبات ریاضی</b>" in html_out
    assert '<pre><code class="language-python">' in html_out
    assert "1. کالیبراسیون کیوبیت‌ها" in html_out


def test_all_six_presentation_formats_and_html_chunking() -> None:
    settings = ChannelSettings(
        post_prefix_header="🔬 گزارش علمی",
        channel_signature_footer="@OpenGenerateSci",
    )
    for fmt in ("tabular", "itemized", "sequential", "statistical", "analytical", "narrative"):
        out = StructuredArticleOutput(
            is_advertisement=False,
            ad_reason=None,
            scientific_domain="computer_science_ai",
            presentation_format=fmt,  # type: ignore[arg-type]
            category_name="هوش مصنوعی",
            criteria_scores={
                "relevance": 90,
                "novelty": 85,
                "depth": 88,
                "clarity": 92,
                "credibility": 90,
                "engagement": 80,
            },
            weighted_score=compute_weighted_score(
                {
                    "relevance": 90,
                    "novelty": 85,
                    "depth": 88,
                    "clarity": 92,
                    "credibility": 90,
                    "engagement": 80,
                },
                ScoringWeights(),
            ),
            generated_title=f"عنوان آزمایشی فرمت {fmt}",
            generated_post_body="متن تحلیلی اصلی مقاله علمی.",
            comparison_table_headers=("پارامتر", "مقدار"),
            comparison_table_rows=(("دقت", "۹۹٪"), ("تاخیر", "۱۰ میلی‌ثانیه")),
            key_points=("یافته اول پژوهش", "یافته دوم پژوهش"),
            hashtags=("هوش_مصنوعی", "علم"),
        )
        post_html, post_blocks = format_channel_post(
            out, settings=settings, source_url="https://sci.example.com/p1"
        )
        assert post_html and len(post_blocks) >= 4
        assert "@OpenGenerateSci" in post_html

    long_html = "\n\n".join(f"<b>بخش {i}</b>\n" + ("متن آزمایشی طولانی " * 15) for i in range(25))
    chunks = chunk_telegram_html(long_html, max_chars=1024)
    assert len(chunks) > 1
    assert all(len(c) <= 1024 for c in chunks)


def test_media_preparation_and_watermarking() -> None:
    urls = [
        "https://sci.example.com/img/fig1.jpg",
        "https://sci.example.com/img/fig2.png",
        "https://sci.example.com/vid/demo.mp4",
    ]
    wm = WatermarkSettings(
        enabled=True, text_or_asset="@SciMark", position="bottom_right", opacity=80
    )

    single = prepare_article_media(urls, media_mode="single_image", watermark=wm)
    assert single.mode == "single_image"
    assert len(single.urls) == 1
    assert single.watermarked and "og_wm=" in single.urls[0]

    album = prepare_article_media(urls, media_mode="multi_image_album", watermark=wm)
    assert album.mode == "multi_image_album"
    assert len(album.urls) == 2

    vid = prepare_article_media(urls, media_mode="video", watermark=wm)
    assert vid.mode == "video"
    assert vid.urls == ("https://sci.example.com/vid/demo.mp4",)

    assert compute_weighted_score(
        {
            "relevance": 80,
            "novelty": 80,
            "depth": 80,
            "clarity": 80,
            "credibility": 80,
            "engagement": 80,
        },
        ScoringWeights(),
    ) == pytest.approx(80.0)
