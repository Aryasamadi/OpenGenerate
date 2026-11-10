# ADR-0008 — Content formatting, domain-adaptive structure and rich messages

**Status:** Accepted (operator requirement: format must follow the scientific/editorial nature of the content; the bot continuation may use all Rich Message/Markdown formats where needed)

## Context
A uniform paragraph template misrepresents comparative data, procedures, statistics and scientific findings. Telegram channel posts are size-constrained; the bot continuation is not.

## Decision
1. **`RichAST`** (internal, versioned) is the single representation of generated and admin content. Blocks: `heading(level)`, `paragraph`, `list(ordered|bullet, nested)`, `quote(expandable)`, `table(header, rows, align)`, `divider`, `details(summary, children)`, `code(lang)`, `media(photo|video, url|file_id, caption)`, `callout(kind)`, `metric(label, value, delta)`; inline: bold, italic, underline, strike, code, spoiler, link. Persisted as JSON in `articles.post_ast` / `full_ast`.
2. **Domain & format selection is a two-layer policy:**
   - *Classifier output* (from the model, validated): `domain ∈ {empirical_science, comparative_technical, statistical_financial, procedural, analytical, news_brief, general}` and the *discipline label* (free text, e.g. "cardiology", "GPU hardware").
   - *Format policy (code, deterministic):* maps `(domain, content signals)` → `format ∈ {tabular, itemized, sequential, statistical, analytical, narrative}` with guards: `tabular` only if the source contains ≥2 comparable entities × ≥2 attributes; `sequential` only if ordered steps exist; `statistical` only if ≥2 numeric facts; otherwise degrade to `itemized`/`narrative`. The model **proposes**, the validator **disposes** — it rejects formats the content cannot support (no invented tables).
   - Channel owner can pin a format or leave `auto` (default); per-category style instructions are layered on top.
3. **Two renditions per article:** `post` (compact, channel; honours `max_words`, `max_caption`, signature, hashtags, source link, media) and `full` (complete, all block types) delivered in the bot via deep link. If `full` is absent, no "more" link is shown. Channel posts remain a single message per article.
4. **Renderers (ports):** `NativeRichRenderer` (→ `sendRichMessage` payload) and `HtmlRenderer` (Telegram HTML subset) and `PlainRenderer`. **Lossless downgrade rules** (tested): table → aligned `<pre>` grid when width fits else per-row itemisation; `details` → bold summary + expandable quote or spoiler-free expanded text; nested lists → indented bullets; media → separate media message only when caption can't carry it; links, quotes and all text are always preserved; nothing is silently dropped. A parser/renderer failure falls back stepwise and records the reason.
5. **Markdown subset** accepted from models/admins is explicitly defined (CommonMark core + GFM tables + `||spoiler||`/`>!` quote extensions we document) and fuzz/golden tested; unsupported constructs degrade to text and are reported, not hidden.
6. **Escaping:** all external text is escaped by renderers; the AST has no raw-HTML node.
7. **Persian/RTL:** renderers insert RLM/RLE controls only where needed for mixed-direction runs, use Persian numerals only in UI chrome (not inside data/code/URLs), and are golden-tested.

## Alternatives rejected
Free-form model HTML (injection/escaping risk, unlossy fallback impossible); fixed templates only (ignores the discipline requirement).

## Consequences
Prompt templates are versioned per domain; format policy and renderers are pure functions → easily unit/property tested.

## Revisit trigger
Primary-source verification (Phase 6) changes the native block vocabulary or limits.
