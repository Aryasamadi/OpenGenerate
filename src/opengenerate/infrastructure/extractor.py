"""Full-text multi-page article and media extraction with boilerplate & tracking-pixel stripping."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from opengenerate.domain.sources import canonicalize_url
from opengenerate.infrastructure.discovery import HttpFetcher, parse_timestamp

_IGNORED_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "iframe",
        "svg",
        "canvas",
        "form",
        "nav",
        "footer",
        "aside",
        "button",
    }
)
_BLOCK_TAGS = frozenset(
    {"p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "tr", "article", "section", "div"}
)
_JUNK_CLASS_ID_TOKENS = (
    "cookie",
    "popup",
    "modal",
    "newsletter",
    "subscribe",
    "sidebar",
    "comment",
    "related",
    "share",
    "social",
    "promo",
    "advert",
    "sponsor",
    "breadcrumb",
)
_JUNK_MEDIA_PATTERNS = (
    "1x1",
    "pixel",
    "tracker",
    "analytics",
    "doubleclick",
    "googlesyndication",
    "/ads/",
    "adserver",
    "avatar",
    "favicon",
    "sprite",
    "emoji",
    "/icon",
    "logo",
    "badge",
    "spinner",
    "loading",
)


@dataclass(frozen=True, slots=True)
class ExtractedArticle:
    url: str
    canonical_url: str
    title: str
    body: str
    published_at: datetime | None
    media_urls: tuple[str, ...]
    pages_fetched: int = 1
    next_page_url: str | None = None


def is_valid_article_media_url(
    raw_url: str,
    *,
    width: str | None = None,
    height: str | None = None,
) -> bool:
    """Return True if `raw_url` is a valid article photo/video and not a tracking pixel, icon, or ad."""
    u = raw_url.strip()
    if not u or u.startswith("data:") or not u.startswith(("http://", "https://")):
        return False
    lower = u.lower()
    if lower.endswith((".svg", ".ico")):
        return False
    if any(pat in lower for pat in _JUNK_MEDIA_PATTERNS):
        return False
    for dim in (width, height):
        if dim and dim.isdigit() and int(dim) <= 16:
            return False
    return True


class _ArticleDOMParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.base_host = (urlsplit(base_url).hostname or "").lower()
        self.meta_title: str = ""
        self.h1_title: str = ""
        self.doc_title: str = ""
        self.published_raw: str | None = None
        self.media_urls: list[str] = []
        self.next_page_url: str | None = None

        self._ignored_depth = 0
        self._junk_depth_stack: list[bool] = []
        self._in_article_depth = 0
        self._in_title = False
        self._in_h1 = False
        self._current_block: list[str] = []
        self.article_blocks: list[str] = []
        self.fallback_blocks: list[str] = []

    def _add_media(
        self, raw_url: str, *, width: str | None = None, height: str | None = None
    ) -> None:
        resolved = urljoin(self.base_url, raw_url.strip())
        if is_valid_article_media_url(resolved, width=width, height=height):
            if resolved not in self.media_urls:
                self.media_urls.append(resolved)

    def _flush_block(self) -> None:
        if not self._current_block:
            return
        text_val = " ".join("".join(self._current_block).split())
        self._current_block = []
        if len(text_val) < 25:
            return
        if self._in_article_depth > 0:
            self.article_blocks.append(text_val)
        else:
            self.fallback_blocks.append(text_val)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}

        if t == "meta":
            prop = (attr_map.get("property") or attr_map.get("name") or "").lower()
            content = attr_map.get("content", "").strip()
            if not content:
                return
            if prop in ("og:title", "twitter:title") and not self.meta_title:
                self.meta_title = content
            elif prop in (
                "article:published_time",
                "og:published_time",
                "pubdate",
                "date",
                "datepublished",
            ) and not self.published_raw:
                self.published_raw = content
            elif prop in ("og:image", "twitter:image", "og:video"):
                self._add_media(content)
            return

        if t == "link":
            rel_tokens = {r.strip().lower() for r in attr_map.get("rel", "").split()}
            href = attr_map.get("href", "").strip()
            if "next" in rel_tokens and href and not self.next_page_url:
                cand = urljoin(self.base_url, href)
                if (urlsplit(cand).hostname or "").lower() == self.base_host:
                    self.next_page_url = cand
            return

        if t in _IGNORED_TAGS:
            self._ignored_depth += 1
            return

        cls_id = f"{attr_map.get('class', '')} {attr_map.get('id', '')}".lower()
        is_junk = any(tok in cls_id for tok in _JUNK_CLASS_ID_TOKENS)
        self._junk_depth_stack.append(is_junk)
        if is_junk:
            self._ignored_depth += 1
            return

        if self._ignored_depth > 0:
            return

        if t in ("article", "main") or any(
            k in cls_id for k in ("article-body", "post-content", "entry-content", "story-body")
        ):
            self._in_article_depth += 1

        if t == "title":
            self._in_title = True
        elif t == "h1" and not self.h1_title:
            self._in_h1 = True
        elif t == "time":
            dt_attr = attr_map.get("datetime", "").strip()
            if dt_attr and not self.published_raw:
                self.published_raw = dt_attr
        elif t == "a":
            rel_tokens = {r.strip().lower() for r in attr_map.get("rel", "").split()}
            href = attr_map.get("href", "").strip()
            if ("next" in rel_tokens or "next" in cls_id) and href and not self.next_page_url:
                cand = urljoin(self.base_url, href)
                if (urlsplit(cand).hostname or "").lower() == self.base_host:
                    self.next_page_url = cand
        elif t == "img":
            src = (
                attr_map.get("src")
                or attr_map.get("data-src")
                or attr_map.get("data-original")
                or ""
            )
            if src:
                self._add_media(src, width=attr_map.get("width"), height=attr_map.get("height"))
        elif t in ("video", "source"):
            vsrc = attr_map.get("src", "")
            if vsrc:
                self._add_media(vsrc)

        if t in _BLOCK_TAGS:
            self._flush_block()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.doc_title += data
        if self._ignored_depth > 0:
            return
        if self._in_h1:
            self.h1_title += data
        self._current_block.append(data)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "title":
            self._in_title = False
        elif t == "h1":
            self._in_h1 = False

        if t in _IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return

        if self._junk_depth_stack:
            was_junk = self._junk_depth_stack.pop()
            if was_junk:
                self._ignored_depth = max(0, self._ignored_depth - 1)
                return

        if self._ignored_depth > 0:
            return

        if t in _BLOCK_TAGS:
            self._flush_block()
        if t in ("article", "main"):
            self._in_article_depth = max(0, self._in_article_depth - 1)


def extract_article_from_html(html_text: str, page_url: str) -> ExtractedArticle:
    """Extract clean article title, body text, publication timestamp, media URLs, and next-page link."""
    parser = _ArticleDOMParser(page_url)
    parser.feed(html_text)
    parser._flush_block()

    title = " ".join(
        (parser.meta_title or parser.h1_title or parser.doc_title or "").split()
    )
    blocks = parser.article_blocks if parser.article_blocks else parser.fallback_blocks
    deduped_blocks: list[str] = []
    for b in blocks:
        if b not in deduped_blocks:
            deduped_blocks.append(b)
    body = "\n\n".join(deduped_blocks)

    return ExtractedArticle(
        url=page_url,
        canonical_url=canonicalize_url(page_url),
        title=title,
        body=body,
        published_at=parse_timestamp(parser.published_raw),
        media_urls=tuple(parser.media_urls),
        pages_fetched=1,
        next_page_url=parser.next_page_url,
    )


async def extract_full_article(
    http: HttpFetcher,
    url: str,
    *,
    max_pages: int = 5,
) -> ExtractedArticle:
    """Fetch an article and follow multi-page pagination (`rel="next"`) up to `max_pages`."""
    resp = await http.get(url)
    first = extract_article_from_html(resp.text, resp.url)
    if not first.next_page_url or max_pages <= 1:
        return first

    visited = {first.canonical_url}
    body_parts = [first.body] if first.body else []
    media_list = list(first.media_urls)
    next_url: str | None = first.next_page_url
    pages = 1

    while next_url and pages < max_pages:
        try:
            canon_next = canonicalize_url(next_url)
        except Exception:
            break
        if canon_next in visited:
            break
        visited.add(canon_next)
        try:
            sub_resp = await http.get(next_url)
        except Exception:
            break
        sub = extract_article_from_html(sub_resp.text, sub_resp.url)
        pages += 1
        if sub.body and sub.body not in body_parts:
            body_parts.append(sub.body)
        for m in sub.media_urls:
            if m not in media_list:
                media_list.append(m)
        next_url = sub.next_page_url

    return ExtractedArticle(
        url=first.url,
        canonical_url=first.canonical_url,
        title=first.title,
        body="\n\n".join(body_parts),
        published_at=first.published_at,
        media_urls=tuple(media_list),
        pages_fetched=pages,
        next_page_url=next_url,
    )
