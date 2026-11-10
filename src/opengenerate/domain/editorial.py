"""Pure domain logic for Scientific Domain Classification, Presentation Structuring, Rich Blocks & Lossless HTML."""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from opengenerate.domain.channels import ChannelSettings, ScoringWeights
from opengenerate.domain.errors import ValidationFailed

ScientificDomain = Literal[
    "medicine_clinical",
    "computer_science_ai",
    "physics_engineering",
    "chemistry_materials",
    "economics_finance",
    "mathematics_statistics",
    "humanities_law",
    "tutorial_howto",
    "general",
]

PresentationFormat = Literal[
    "tabular",
    "itemized",
    "sequential",
    "statistical",
    "analytical",
    "narrative",
]

VALID_PRESENTATION_FORMATS: frozenset[str] = frozenset(
    {"tabular", "itemized", "sequential", "statistical", "analytical", "narrative"}
)

RichBlockKind = Literal[
    "heading",
    "paragraph",
    "table",
    "quote",
    "collapsible",
    "code",
    "list",
    "divider",
]


@dataclass(frozen=True, slots=True)
class RichBlock:
    kind: RichBlockKind
    text: str = ""
    level: int = 2  # 1..3 for heading
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    items: tuple[str, ...] = ()
    ordered: bool = False
    language: str = ""
    title: str = ""  # summary title for collapsible or attribution for quote
    expandable: bool = False

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["headers"] = list(self.headers)
        raw["rows"] = [list(r) for r in self.rows]
        raw["items"] = list(self.items)
        return raw

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RichBlock:
        kind = str(data.get("kind", "paragraph"))
        if kind not in (
            "heading",
            "paragraph",
            "table",
            "quote",
            "collapsible",
            "code",
            "list",
            "divider",
        ):
            kind = "paragraph"
        return cls(
            kind=kind,  # type: ignore[arg-type]
            text=str(data.get("text", "")),
            level=max(1, min(3, int(data.get("level", 2)))),
            headers=tuple(str(h) for h in data.get("headers", ())),
            rows=tuple(tuple(str(c) for c in r) for r in data.get("rows", ())),
            items=tuple(str(i) for i in data.get("items", ())),
            ordered=bool(data.get("ordered", False)),
            language=str(data.get("language", "")),
            title=str(data.get("title", "")),
            expandable=bool(data.get("expandable", False)),
        )


@dataclass(frozen=True, slots=True)
class StructuredArticleOutput:
    is_advertisement: bool
    ad_reason: str | None
    scientific_domain: ScientificDomain
    presentation_format: PresentationFormat
    category_name: str
    criteria_scores: dict[str, float]
    weighted_score: float
    generated_title: str
    generated_post_body: str
    comparison_table_headers: tuple[str, ...] = ()
    comparison_table_rows: tuple[tuple[str, ...], ...] = ()
    key_points: tuple[str, ...] = ()
    hashtags: tuple[str, ...] = ()
    continuation_blocks: tuple[RichBlock, ...] = field(default_factory=tuple)


_DOMAIN_KEYWORDS: tuple[tuple[ScientificDomain, PresentationFormat, tuple[str, ...]], ...] = (
    (
        "medicine_clinical",
        "itemized",
        (
            "بالینی",
            "کارآزمایی",
            "بیمار",
            "دارو",
            "واکسن",
            "ژنتیک",
            "پزشکی",
            "درمان",
            "clinical",
            "patient",
            "dosage",
            "vaccine",
            "therapy",
            "syndrome",
            "crispr",
        ),
    ),
    (
        "computer_science_ai",
        "tabular",
        (
            "ترنسفورمر",
            "مدل زبانی",
            "الگوریتم",
            "بنچمارک",
            "هوش مصنوعی",
            "شبکه عصبی",
            "benchmark",
            "transformer",
            "llm",
            "latency",
            "throughput",
            "gpu",
            "neural network",
        ),
    ),
    (
        "economics_finance",
        "statistical",
        (
            "تورم",
            "نرخ بهره",
            "تولید ناخالص",
            "بازار سهام",
            "اقتصاد",
            "آماری",
            "inflation",
            "gdp",
            "econometric",
            "fiscal",
            "monetary",
            "quarter",
        ),
    ),
    (
        "tutorial_howto",
        "sequential",
        (
            "گام‌به‌گام",
            "مراحل",
            "آموزش",
            "نحوه",
            "پروتکل آزمایش",
            "step-by-step",
            "how to",
            "tutorial",
            "procedure",
            "protocol",
        ),
    ),
    (
        "physics_engineering",
        "analytical",
        (
            "کوانتوم",
            "ترمودینامیک",
            "همجوشی",
            "فیزیک",
            "فراخورشیدی",
            "تلسکوپ",
            "quantum",
            "fusion",
            "relativity",
            "exoplanet",
            "reactor",
            "photon",
        ),
    ),
    (
        "chemistry_materials",
        "tabular",
        (
            "کاتالیزور",
            "پلیمر",
            "مولکولی",
            "شیمی",
            "نانوذرات",
            "ترکیب شیمیایی",
            "catalyst",
            "polymer",
            "molecule",
            "synthesis",
            "nanoparticle",
        ),
    ),
    (
        "mathematics_statistics",
        "analytical",
        (
            "قضیه",
            "احتمالات",
            "ماتریس",
            "توپولوژی",
            "ریاضی",
            "theorem",
            "manifold",
            "stochastic",
            "eigenvalue",
        ),
    ),
    (
        "humanities_law",
        "analytical",
        (
            "حقوقی",
            "فلسفه",
            "جامعه‌شناسی",
            "قانون‌گذاری",
            "jurisprudence",
            "constitutional",
            "sociology",
            "epistemology",
        ),
    ),
)


def detect_scientific_domain_and_format(
    title: str, body: str
) -> tuple[ScientificDomain, PresentationFormat]:
    """Deterministically classify the scientific discipline and default presentation format."""
    combined = f"{title}\n{body}".lower()
    best_domain: ScientificDomain = "general"
    best_fmt: PresentationFormat = "narrative"
    best_hits = 0

    for domain, default_fmt, keywords in _DOMAIN_KEYWORDS:
        hits = sum(1 for kw in keywords if kw in combined)
        if hits > best_hits:
            best_hits = hits
            best_domain = domain
            best_fmt = default_fmt

    # Explicit structural overrides based on content patterns
    if "|" in body and body.count("|") >= 6:
        best_fmt = "tabular"
    elif any(k in combined for k in ("مقایسه", "جدول", "بنچمارک", "comparison", "vs.", "benchmark")):
        best_fmt = "tabular"
    elif any(k in combined for k in ("گام اول", "مرحله ۱", "step 1", "پروتکل", "گام‌به‌گام")):
        best_fmt = "sequential"
    elif any(k in combined for k in ("درصد", "آمار", "میانگین", "p-value", "confidence interval")) and best_domain in (
        "economics_finance",
        "medicine_clinical",
    ):
        best_fmt = "statistical"

    return best_domain, best_fmt


def compute_weighted_score(
    criteria_scores: Mapping[str, float],
    weights: ScoringWeights,
) -> float:
    """Compute weighted `0..100` quality score across the 6 editorial criteria."""
    norm = weights.normalized()
    total = 0.0
    for key, w in norm.items():
        raw_val = float(criteria_scores.get(key, 0.0))
        clamped = max(0.0, min(100.0, raw_val))
        total += clamped * w
    return round(total, 2)


def compute_ai_cache_key(
    *,
    canonical_url: str,
    content_fingerprint: str | None,
    settings: ChannelSettings,
    schema_version: str = "v2-scientific-rich",
) -> str:
    payload = {
        "v": schema_version,
        "url": canonical_url,
        "fp": content_fingerprint or "",
        "lang": settings.language,
        "style": settings.writing_style,
        "len": settings.post_length,
        "mode": settings.content_mode,
        "cats": list(settings.custom_categories),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---- Rich Markdown -> RichBlock Parser --------------------------------------------------------


_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def format_inline_html(text: str) -> str:
    """Escape plain text for Telegram HTML while converting safe inline Markdown (`**bold**`, `*italic*`, `` `code` ``, `[text](url)`)."""
    escaped = html.escape(text, quote=False)
    escaped = _INLINE_CODE_RE.sub(r"<code>\1</code>", escaped)
    escaped = _BOLD_RE.sub(r"<b>\1</b>", escaped)
    escaped = _ITALIC_RE.sub(r"<i>\1</i>", escaped)
    escaped = _LINK_RE.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        escaped,
    )
    return escaped


def parse_rich_markdown_to_blocks(markdown_text: str) -> list[RichBlock]:
    """Parse Rich Markdown (tables, fenced code, `:::details`, blockquotes, lists, headings, paragraphs) into `RichBlock`s."""
    lines = markdown_text.replace("\r\n", "\n").split("\n")
    blocks: list[RichBlock] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Horizontal divider
        if stripped in ("---", "***", "___"):
            blocks.append(RichBlock(kind="divider"))
            i += 1
            continue

        # Fenced code block ```lang ... ```
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            i += 1
            code_lines: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1
            blocks.append(RichBlock(kind="code", text="\n".join(code_lines), language=lang))
            continue

        # Collapsible details block :::details Title ... :::
        if stripped.startswith(":::details"):
            title = stripped[len(":::details") :].strip() or "جزئیات بیشتر"
            i += 1
            detail_lines: list[str] = []
            while i < n and lines[i].strip() != ":::":
                detail_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1
            blocks.append(
                RichBlock(
                    kind="collapsible",
                    title=title,
                    text="\n".join(detail_lines).strip(),
                    expandable=True,
                )
            )
            continue

        # Markdown Table | Col1 | Col2 |
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3:
            table_lines: list[str] = []
            while i < n and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            parsed_rows: list[tuple[str, ...]] = []
            for tl in table_lines:
                cells = [c.strip() for c in tl.strip("|").split("|")]
                # Skip separator row like |---|---|
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    continue
                parsed_rows.append(tuple(cells))
            if parsed_rows:
                headers = parsed_rows[0]
                rows = tuple(parsed_rows[1:])
                blocks.append(RichBlock(kind="table", headers=headers, rows=rows))
            continue

        # Heading #, ##, ###
        if stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            heading_text = stripped[hashes:].strip()
            if heading_text:
                blocks.append(
                    RichBlock(kind="heading", text=heading_text, level=max(1, min(3, hashes)))
                )
                i += 1
                continue

        # Blockquote > ...
        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip(">").strip())
                i += 1
            q_text = "\n".join(quote_lines)
            blocks.append(
                RichBlock(kind="quote", text=q_text, expandable=len(q_text) > 220)
            )
            continue

        # Unordered or Ordered List
        if re.match(r"^([-*•]|\d+[.)])\s+", stripped):
            ordered = bool(re.match(r"^\d+[.)]\s+", stripped))
            items: list[str] = []
            while i < n and re.match(r"^([-*•]|\d+[.)])\s+", lines[i].strip()):
                item_txt = re.sub(r"^([-*•]|\d+[.)])\s+", "", lines[i].strip())
                items.append(item_txt)
                i += 1
            blocks.append(RichBlock(kind="list", items=tuple(items), ordered=ordered))
            continue

        # Standard paragraph
        para_lines = [stripped]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if (
                not nxt
                or nxt in ("---", "***", "___")
                or nxt.startswith(("```", ":::details", "|", "#", ">"))
                or re.match(r"^([-*•]|\d+[.)])\s+", nxt)
            ):
                break
            para_lines.append(nxt)
            i += 1
        blocks.append(RichBlock(kind="paragraph", text=" ".join(para_lines)))

    return blocks


# ---- Table & RichBlock Renderers (`sendRichMessage` + Lossless HTML Fallback) ----------------


def render_unicode_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Render a compact aligned Unicode table suitable for `<pre>` blocks in Telegram HTML."""
    all_rows = [list(headers), *[list(r) for r in rows]] if headers else [list(r) for r in rows]
    if not all_rows:
        return ""
    col_count = max(len(r) for r in all_rows)
    widths = [0] * col_count
    for r in all_rows:
        for idx in range(col_count):
            cell = r[idx] if idx < len(r) else ""
            widths[idx] = max(widths[idx], len(cell))

    def _fmt_row(r: Sequence[str]) -> str:
        cells = [
            (r[idx] if idx < len(r) else "").ljust(widths[idx]) for idx in range(col_count)
        ]
        return "│ " + " │ ".join(cells) + " │"

    top = "┌─" + "─┬─".join("─" * w for w in widths) + "─┐"
    mid = "├─" + "─┼─".join("─" * w for w in widths) + "─┤"
    bot = "└─" + "─┴─".join("─" * w for w in widths) + "─┘"

    out = [top]
    if headers:
        out.append(_fmt_row(headers))
        if rows:
            out.append(mid)
    for row_cells in rows:
        out.append(_fmt_row(row_cells))
    out.append(bot)
    return "\n".join(out)


def render_rich_message_payload(blocks: Sequence[RichBlock]) -> list[dict[str, Any]]:
    """Produce the structured `blocks` payload for Telegram `sendRichMessage`."""
    return [b.to_dict() for b in blocks]


def render_blocks_to_telegram_html(blocks: Sequence[RichBlock]) -> str:
    """Losslessly render `RichBlock`s into valid Telegram Bot API HTML."""
    rendered: list[str] = []
    for b in blocks:
        if b.kind == "heading":
            prefix = "📌 " if b.level == 1 else ("🔹 " if b.level == 2 else "▫️ ")
            rendered.append(f"<b>{prefix}{html.escape(b.text)}</b>")
        elif b.kind == "paragraph":
            rendered.append(format_inline_html(b.text))
        elif b.kind == "table":
            tbl = render_unicode_table(b.headers, b.rows)
            if tbl:
                rendered.append(f"<pre>{html.escape(tbl)}</pre>")
        elif b.kind == "quote":
            tag = "blockquote expandable" if b.expandable else "blockquote"
            close_tag = "blockquote"
            rendered.append(f"<{tag}>{format_inline_html(b.text)}</{close_tag}>")
        elif b.kind == "collapsible":
            summary = html.escape(b.title or "جزئیات")
            body_html = format_inline_html(b.text)
            rendered.append(
                f"<blockquote expandable><b>▾ {summary}</b>\n{body_html}</blockquote>"
            )
        elif b.kind == "code":
            code_esc = html.escape(b.text)
            if b.language:
                lang_esc = html.escape(b.language, quote=True)
                rendered.append(f'<pre><code class="language-{lang_esc}">{code_esc}</code></pre>')
            else:
                rendered.append(f"<pre><code>{code_esc}</code></pre>")
        elif b.kind == "list":
            lines: list[str] = []
            for idx, item in enumerate(b.items, start=1):
                bullet = f"{idx}." if b.ordered else "•"
                lines.append(f"{bullet} {format_inline_html(item)}")
            if lines:
                rendered.append("\n".join(lines))
        elif b.kind == "divider":
            rendered.append("───────────────")
    return "\n\n".join(part for part in rendered if part)


def format_channel_post(
    output: StructuredArticleOutput,
    *,
    settings: ChannelSettings,
    source_url: str,
) -> tuple[str, list[RichBlock]]:
    """Format the channel post according to `output.presentation_format` and `settings`.

    Returns `(telegram_html, rich_blocks)` so the publisher can either call `sendRichMessage`
    or fall back losslessly to Telegram HTML (`sendMessage` / `sendPhoto`).
    """
    blocks: list[RichBlock] = []

    if settings.post_prefix_header.strip():
        blocks.append(RichBlock(kind="paragraph", text=f"**{settings.post_prefix_header.strip()}**"))

    blocks.append(RichBlock(kind="heading", text=output.generated_title, level=1))

    if settings.auto_categorization_enabled and output.category_name.strip():
        cat_label = "دسته‌بندی" if settings.language == "fa" else "Category"
        blocks.append(
            RichBlock(
                kind="paragraph",
                text=f"🗂 **{cat_label}:** `{output.category_name.strip()}`",
            )
        )

    fmt = output.presentation_format
    if fmt == "tabular":
        if output.generated_post_body.strip():
            blocks.append(RichBlock(kind="paragraph", text=output.generated_post_body.strip()))
        if output.comparison_table_headers or output.comparison_table_rows:
            blocks.append(
                RichBlock(
                    kind="table",
                    headers=output.comparison_table_headers,
                    rows=output.comparison_table_rows,
                )
            )
        if output.key_points:
            blocks.append(RichBlock(kind="list", items=output.key_points, ordered=False))

    elif fmt == "itemized":
        if output.generated_post_body.strip():
            blocks.append(RichBlock(kind="paragraph", text=output.generated_post_body.strip()))
        if output.key_points:
            blocks.append(RichBlock(kind="list", items=output.key_points, ordered=False))

    elif fmt == "sequential":
        if output.generated_post_body.strip():
            blocks.append(RichBlock(kind="paragraph", text=output.generated_post_body.strip()))
        if output.key_points:
            blocks.append(RichBlock(kind="list", items=output.key_points, ordered=True))

    elif fmt == "statistical":
        if output.comparison_table_headers or output.comparison_table_rows:
            blocks.append(
                RichBlock(
                    kind="table",
                    headers=output.comparison_table_headers,
                    rows=output.comparison_table_rows,
                )
            )
        if output.key_points:
            blocks.append(RichBlock(kind="list", items=output.key_points, ordered=False))
        if output.generated_post_body.strip():
            blocks.append(RichBlock(kind="quote", text=output.generated_post_body.strip()))

    elif fmt == "analytical":
        if output.generated_post_body.strip():
            blocks.append(RichBlock(kind="paragraph", text=output.generated_post_body.strip()))
        if output.key_points:
            summary_title = (
                "نکات تحلیلی و روش‌شناسی پژوهش"
                if settings.language == "fa"
                else "Analytical & Methodological Takeaways"
            )
            blocks.append(
                RichBlock(
                    kind="collapsible",
                    title=summary_title,
                    text="\n".join(f"• {pt}" for pt in output.key_points),
                    expandable=True,
                )
            )

    else:  # narrative
        if output.generated_post_body.strip():
            blocks.append(RichBlock(kind="paragraph", text=output.generated_post_body.strip()))
        if output.key_points:
            blocks.append(RichBlock(kind="list", items=output.key_points, ordered=False))

    if settings.source_link_credit_enabled and source_url:
        src_label = "منبع اصلی مقاله" if settings.language == "fa" else "Original Source"
        blocks.append(RichBlock(kind="paragraph", text=f"🔗 [{src_label}]({source_url})"))

    if settings.hashtags_enabled and output.hashtags:
        cleaned_tags = [
            "#" + re.sub(r"[^\w\u0600-\u06FF]+", "_", t.lstrip("#")).strip("_")
            for t in output.hashtags
            if t.strip()
        ]
        if cleaned_tags:
            blocks.append(RichBlock(kind="paragraph", text=" ".join(cleaned_tags)))

    if settings.channel_signature_footer.strip():
        blocks.append(RichBlock(kind="paragraph", text=settings.channel_signature_footer.strip()))

    return render_blocks_to_telegram_html(blocks), blocks


def chunk_telegram_html(html_text: str, *, max_chars: int = 4096) -> list[str]:
    """Split rendered Telegram HTML on paragraph boundaries (`\\n\\n`) without breaking HTML tags."""
    if max_chars < 128:
        raise ValidationFailed("max_chars must be >= 128")
    if len(html_text) <= max_chars:
        return [html_text]

    paragraphs = html_text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        sep_len = 2 if current else 0
        if current_len + sep_len + len(para) <= max_chars:
            current.append(para)
            current_len += sep_len + len(para)
            continue

        if current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0

        if len(para) <= max_chars:
            current.append(para)
            current_len = len(para)
        else:
            # Split oversize paragraph by lines while keeping tags intact
            lines = para.split("\n")
            line_buf: list[str] = []
            line_len = 0
            for ln in lines:
                l_sep = 1 if line_buf else 0
                if line_len + l_sep + len(ln) <= max_chars:
                    line_buf.append(ln)
                    line_len += l_sep + len(ln)
                else:
                    if line_buf:
                        chunks.append("\n".join(line_buf))
                    line_buf = [ln[:max_chars]]
                    line_len = len(line_buf[0])
            if line_buf:
                current.append("\n".join(line_buf))
                current_len = len(current[0])

    if current:
        chunks.append("\n\n".join(current))
    return chunks
