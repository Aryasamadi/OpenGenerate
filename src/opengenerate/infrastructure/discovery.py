"""5-Strategy Content Discovery Engine: Feed Autodiscovery, RSS/Atom, JSON Feed/Custom API, Sitemaps & HTML."""

from __future__ import annotations

import contextlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from opengenerate.domain.sources import DiscoveryType, canonicalize_url
from opengenerate.infrastructure.http_safe import SafeResponse

_DOCTYPES_RE = re.compile(
    r"<!DOCTYPE[^>\[]*(?:\[[^\]]*\])?\s*>|<!ENTITY[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
_FEED_MIME_TYPES = frozenset(
    {
        "application/rss+xml",
        "application/atom+xml",
        "application/rdf+xml",
        "application/feed+json",
        "application/xml",
        "text/xml",
    }
)
_NON_ARTICLE_PATH_SEGMENTS = frozenset(
    {
        "tag",
        "tags",
        "category",
        "categories",
        "author",
        "authors",
        "login",
        "signup",
        "register",
        "contact",
        "about",
        "privacy",
        "terms",
        "search",
        "cart",
        "checkout",
        "wp-admin",
        "wp-login.php",
        "feed",
        "rss",
    }
)
_SKIP_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".mp4",
    ".mp3",
    ".pdf",
    ".zip",
    ".css",
    ".js",
    ".ico",
    ".xml",
    ".json",
)


class HttpFetcher(Protocol):
    async def get(self, url: str, **kwargs: Any) -> SafeResponse: ...


@dataclass(frozen=True, slots=True)
class DiscoveredItem:
    url: str
    canonical_url: str
    title: str = ""
    summary_text: str = ""
    published_at: datetime | None = None
    media_urls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    strategy_used: str
    not_modified: bool
    items: list[DiscoveredItem]
    etag: str | None = None
    last_modified: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


def parse_timestamp(raw: str | None) -> datetime | None:
    """Parse ISO-8601 or RFC-2822 date strings into a timezone-aware UTC `datetime`."""
    if not raw:
        return None
    val = raw.strip()
    if not val:
        return None
    try:
        dt = parsedate_to_datetime(val)
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        pass
    try:
        iso = val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _safe_xml_root(xml_text: str) -> ET.Element:
    sanitized = _DOCTYPES_RE.sub("", xml_text.lstrip("\ufeff \t\r\n"))
    return ET.fromstring(sanitized)


def _try_make_item(
    raw_url: str,
    base_url: str,
    *,
    title: str = "",
    summary_text: str = "",
    published_at: datetime | None = None,
    media_urls: list[str] | None = None,
) -> DiscoveredItem | None:
    resolved = urljoin(base_url, raw_url.strip())
    try:
        canon = canonicalize_url(resolved)
    except Exception:
        return None
    clean_media: list[str] = []
    for m in media_urls or []:
        m_abs = urljoin(base_url, m.strip())
        if m_abs.startswith(("http://", "https://")) and m_abs not in clean_media:
            clean_media.append(m_abs)
    return DiscoveredItem(
        url=resolved,
        canonical_url=canon,
        title=" ".join(title.split()),
        summary_text=summary_text.strip(),
        published_at=published_at,
        media_urls=tuple(clean_media),
    )


# ---- Strategy 1: Feed Autodiscovery from HTML -------------------------------------------------


class _FeedLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.feed_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link":
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        rel_tokens = {t.strip().lower() for t in attr_map.get("rel", "").split()}
        mime = attr_map.get("type", "").split(";")[0].strip().lower()
        href = attr_map.get("href", "").strip()
        if "alternate" in rel_tokens and href and mime in _FEED_MIME_TYPES:
            resolved = urljoin(self.base_url, href)
            if resolved not in self.feed_urls:
                self.feed_urls.append(resolved)


def autodiscover_feeds(html_text: str, base_url: str) -> list[str]:
    """Strategy 1: Extract `<link rel="alternate">` feed URLs from an HTML page."""
    parser = _FeedLinkParser(base_url)
    parser.feed(html_text)
    return parser.feed_urls


# ---- Strategy 2: RSS / Atom / RDF Feed Parsing ------------------------------------------------


def parse_rss_or_atom_feed(xml_text: str, base_url: str) -> list[DiscoveredItem]:
    """Strategy 2: Parse RSS 2.0, Atom 1.0, or RDF 1.0 XML feeds."""
    root = _safe_xml_root(xml_text)
    root_name = _local_tag(root.tag)
    items: list[DiscoveredItem] = []
    seen: set[str] = set()

    if root_name == "feed":
        # Atom 1.0
        for entry in root.iter():
            if _local_tag(entry.tag) != "entry":
                continue
            link_href = ""
            title = ""
            summary = ""
            pub_raw: str | None = None
            media: list[str] = []
            for child in entry:
                cname = _local_tag(child.tag)
                if cname == "link":
                    rel = (child.attrib.get("rel") or "alternate").lower()
                    href = (child.attrib.get("href") or "").strip()
                    if rel == "alternate" and href and not link_href:
                        link_href = href
                    elif rel == "enclosure" and href:
                        media.append(href)
                elif cname == "title" and child.text:
                    title = child.text
                elif cname in ("content", "summary") and child.text and not summary:
                    summary = child.text
                elif cname in ("published", "updated") and child.text and not pub_raw:
                    pub_raw = child.text
            if link_href:
                item = _try_make_item(
                    link_href,
                    base_url,
                    title=title,
                    summary_text=summary,
                    published_at=parse_timestamp(pub_raw),
                    media_urls=media,
                )
                if item and item.canonical_url not in seen:
                    seen.add(item.canonical_url)
                    items.append(item)
        return items

    if root_name in ("rss", "rdf"):
        for node in root.iter():
            if _local_tag(node.tag) != "item":
                continue
            link_href = ""
            title = ""
            summary = ""
            pub_raw = None
            media = []
            for child in node:
                cname = _local_tag(child.tag)
                if cname == "link" and child.text and not link_href:
                    link_href = child.text.strip()
                elif cname == "title" and child.text:
                    title = child.text
                elif cname in ("encoded", "description") and child.text and not summary:
                    summary = child.text
                elif cname in ("pubdate", "date", "published") and child.text and not pub_raw:
                    pub_raw = child.text
                elif cname in ("enclosure", "content", "thumbnail"):
                    url_attr = (child.attrib.get("url") or "").strip()
                    if url_attr:
                        media.append(url_attr)
            if link_href:
                item = _try_make_item(
                    link_href,
                    base_url,
                    title=title,
                    summary_text=summary,
                    published_at=parse_timestamp(pub_raw),
                    media_urls=media,
                )
                if item and item.canonical_url not in seen:
                    seen.add(item.canonical_url)
                    items.append(item)
        return items

    raise ValueError(f"unsupported feed root element: {root_name}")


# ---- Strategy 3: JSON Feed & Custom JSON API Parsing ------------------------------------------


def parse_json_feed_or_api(raw_json: str | bytes, base_url: str) -> list[DiscoveredItem]:
    """Strategy 3: Parse JSON Feed 1.x or Custom JSON API responses into `DiscoveredItem` list."""
    payload = json.loads(raw_json)
    records: list[Any] = []
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        for key in ("items", "articles", "posts", "data", "results", "entries"):
            val = payload.get(key)
            if isinstance(val, list):
                records = val
                break

    items: list[DiscoveredItem] = []
    seen: set[str] = set()
    for entry in records:
        if not isinstance(entry, dict):
            continue
        raw_url = str(
            entry.get("url")
            or entry.get("external_url")
            or entry.get("link")
            or entry.get("canonical_url")
            or ""
        ).strip()
        if not raw_url:
            continue
        title = str(entry.get("title") or entry.get("headline") or "")
        summary = str(
            entry.get("content_html")
            or entry.get("content_text")
            or entry.get("summary")
            or entry.get("body")
            or entry.get("description")
            or ""
        )
        pub_raw = str(
            entry.get("date_published")
            or entry.get("published_at")
            or entry.get("pubDate")
            or entry.get("created_at")
            or ""
        )
        media: list[str] = []
        for img_key in ("image", "banner_image", "thumbnail", "featured_image"):
            img_val = entry.get(img_key)
            if isinstance(img_val, str) and img_val.strip():
                media.append(img_val.strip())
        attachments = entry.get("attachments")
        if isinstance(attachments, list):
            for att in attachments:
                if isinstance(att, dict) and isinstance(att.get("url"), str):
                    media.append(att["url"])

        item = _try_make_item(
            raw_url,
            base_url,
            title=title,
            summary_text=summary,
            published_at=parse_timestamp(pub_raw),
            media_urls=media,
        )
        if item and item.canonical_url not in seen:
            seen.add(item.canonical_url)
            items.append(item)
    return items


# ---- Strategy 4: XML Sitemap & Sitemap Index Parsing ------------------------------------------


def parse_xml_sitemap(xml_text: str, base_url: str) -> tuple[list[DiscoveredItem], list[str]]:
    """Strategy 4: Parse `<urlset>` or `<sitemapindex>`, returning `(items, child_sitemap_urls)`."""
    root = _safe_xml_root(xml_text)
    root_name = _local_tag(root.tag)
    items: list[DiscoveredItem] = []
    child_sitemaps: list[str] = []
    seen: set[str] = set()

    if root_name == "sitemapindex":
        for node in root.iter():
            if _local_tag(node.tag) != "sitemap":
                continue
            for child in node:
                if _local_tag(child.tag) == "loc" and child.text:
                    loc = urljoin(base_url, child.text.strip())
                    if loc not in child_sitemaps:
                        child_sitemaps.append(loc)
        return items, child_sitemaps

    if root_name == "urlset":
        for node in root.iter():
            if _local_tag(node.tag) != "url":
                continue
            loc_url = ""
            lastmod_raw: str | None = None
            for child in node:
                cname = _local_tag(child.tag)
                if cname == "loc" and child.text:
                    loc_url = child.text.strip()
                elif cname == "lastmod" and child.text:
                    lastmod_raw = child.text.strip()
            if loc_url:
                item = _try_make_item(
                    loc_url,
                    base_url,
                    published_at=parse_timestamp(lastmod_raw),
                )
                if item and item.canonical_url not in seen:
                    seen.add(item.canonical_url)
                    items.append(item)
        return items, child_sitemaps

    raise ValueError(f"unsupported sitemap root element: {root_name}")


# ---- Strategy 5: Intelligent HTML Article Listing Extraction ----------------------------------


class _HtmlListingParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.base_host = (urlsplit(base_url).hostname or "").lower()
        self._semantic_depth = 0
        self._in_ignored = 0
        self._current_href: str | None = None
        self._current_text: list[str] = []
        self._current_priority: int = 0
        self.candidates: list[tuple[int, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("nav", "footer", "header", "aside", "script", "style", "noscript"):
            self._in_ignored += 1
            return
        if t in ("article", "main", "h1", "h2", "h3"):
            self._semantic_depth += 1
        if t == "a" and self._in_ignored == 0:
            attr_map = {k.lower(): (v or "") for k, v in attrs}
            href = attr_map.get("href", "").strip()
            if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                self._current_href = urljoin(self.base_url, href)
                self._current_text = []
                self._current_priority = 2 if self._semantic_depth > 0 else 1

    def handle_data(self, data: str) -> None:
        if self._current_href is not None and self._in_ignored == 0:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("nav", "footer", "header", "aside", "script", "style", "noscript"):
            self._in_ignored = max(0, self._in_ignored - 1)
            return
        if t in ("article", "main", "h1", "h2", "h3"):
            self._semantic_depth = max(0, self._semantic_depth - 1)
        if t == "a" and self._current_href is not None:
            text_val = " ".join("".join(self._current_text).split())
            self.candidates.append((self._current_priority, self._current_href, text_val))
            self._current_href = None
            self._current_text = []


def _looks_like_article_url(url: str, base_host: str, anchor_text: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if base_host and host != base_host:
        return False
    path = parts.path.rstrip("/")
    if not path or path == "":
        return False
    lower_path = path.lower()
    if lower_path.endswith(_SKIP_EXTENSIONS):
        return False
    segments = [s for s in lower_path.split("/") if s]
    if not segments:
        return False
    if segments[0] in _NON_ARTICLE_PATH_SEGMENTS:
        return False
    # Require meaningful anchor text or multi-segment/slug path
    if len(anchor_text) < 8 and len(segments) < 2 and "-" not in segments[-1]:
        return False
    return True


def extract_html_listing_links(html_text: str, base_url: str) -> list[DiscoveredItem]:
    """Strategy 5: Extract candidate article links from an HTML listing page."""
    parser = _HtmlListingParser(base_url)
    parser.feed(html_text)
    # Sort so semantic (<article>/<h1>/<h2>/<h3>) links come first while preserving document order
    ordered = sorted(
        enumerate(parser.candidates),
        key=lambda pair: (-pair[1][0], pair[0]),
    )
    items: list[DiscoveredItem] = []
    seen: set[str] = set()
    for _, (_prio, href, title) in ordered:
        if not _looks_like_article_url(href, parser.base_host, title):
            continue
        item = _try_make_item(href, base_url, title=title)
        if item and item.canonical_url not in seen:
            seen.add(item.canonical_url)
            items.append(item)
    return items


# ---- 5-Strategy Discovery Orchestrator --------------------------------------------------------


async def discover_from_source(
    http: HttpFetcher,
    *,
    source_url: str,
    discovery_type: DiscoveryType = "auto",
    custom_api_url: str | None = None,
    custom_api_key: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
) -> DiscoveryOutcome:
    """Execute the configured or automatic 5-strategy discovery pipeline with conditional HTTP support."""
    target_url = (
        custom_api_url.strip()
        if discovery_type == "custom_api" and custom_api_url
        else source_url
    )
    req_headers: dict[str, str] = {}
    if etag:
        req_headers["If-None-Match"] = etag
    if last_modified:
        req_headers["If-Modified-Since"] = last_modified
    if discovery_type == "custom_api" and custom_api_key:
        req_headers["Authorization"] = f"Bearer {custom_api_key}"

    resp = await http.get(target_url, headers=req_headers)
    new_etag = resp.headers.get("etag") or etag
    new_lm = resp.headers.get("last-modified") or last_modified

    if resp.status_code == 304:
        return DiscoveryOutcome(
            strategy_used="conditional_304",
            not_modified=True,
            items=[],
            etag=new_etag,
            last_modified=new_lm,
            diagnostics={"status_code": 304},
        )

    body_text = resp.text
    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()

    if discovery_type == "rss":
        items = parse_rss_or_atom_feed(body_text, resp.url)
        return DiscoveryOutcome("rss", False, items, new_etag, new_lm, {"count": len(items)})

    if discovery_type in ("json_feed", "custom_api"):
        items = parse_json_feed_or_api(resp.content, resp.url)
        return DiscoveryOutcome(
            discovery_type, False, items, new_etag, new_lm, {"count": len(items)}
        )

    if discovery_type == "sitemap":
        items, child_sitemaps = parse_xml_sitemap(body_text, resp.url)
        if not items and child_sitemaps:
            sub_resp = await http.get(child_sitemaps[0])
            items, _ = parse_xml_sitemap(sub_resp.text, sub_resp.url)
        return DiscoveryOutcome("sitemap", False, items, new_etag, new_lm, {"count": len(items)})

    if discovery_type == "html":
        items = extract_html_listing_links(body_text, resp.url)
        return DiscoveryOutcome("html", False, items, new_etag, new_lm, {"count": len(items)})

    # discovery_type == "auto": run the 5 ordered strategies
    # 1) Direct XML feed or Sitemap if response is XML
    stripped = body_text.lstrip()
    if ctype in ("application/rss+xml", "application/atom+xml", "application/rdf+xml") or stripped.startswith(
        ("<?xml", "<rss", "<feed", "<rdf")
    ):
        with contextlib.suppress(Exception):
            items = parse_rss_or_atom_feed(body_text, resp.url)
            if items:
                return DiscoveryOutcome(
                    "rss", False, items, new_etag, new_lm, {"count": len(items)}
                )
        with contextlib.suppress(Exception):
            items, child_sitemaps = parse_xml_sitemap(body_text, resp.url)
            if not items and child_sitemaps:
                sub_resp = await http.get(child_sitemaps[0])
                items, _ = parse_xml_sitemap(sub_resp.text, sub_resp.url)
            if items:
                return DiscoveryOutcome(
                    "sitemap", False, items, new_etag, new_lm, {"count": len(items)}
                )

    # 2) Direct JSON Feed if response is JSON
    if ctype in ("application/feed+json", "application/json") or stripped.startswith(("{", "[")):
        with contextlib.suppress(Exception):
            items = parse_json_feed_or_api(resp.content, resp.url)
            if items:
                return DiscoveryOutcome(
                    "json_feed", False, items, new_etag, new_lm, {"count": len(items)}
                )

    # 3) Autodiscover `<link rel="alternate">` feeds from HTML page
    feed_links = autodiscover_feeds(body_text, resp.url)
    for feed_url in feed_links[:2]:
        with contextlib.suppress(Exception):
            f_resp = await http.get(feed_url)
            f_ctype = f_resp.headers.get("content-type", "").lower()
            if "json" in f_ctype or f_resp.text.lstrip().startswith("{"):
                items = parse_json_feed_or_api(f_resp.content, f_resp.url)
                if items:
                    return DiscoveryOutcome(
                        "autodiscovered_json_feed",
                        False,
                        items,
                        new_etag,
                        new_lm,
                        {"feed_url": feed_url, "count": len(items)},
                    )
            items = parse_rss_or_atom_feed(f_resp.text, f_resp.url)
            if items:
                return DiscoveryOutcome(
                    "autodiscovered_rss",
                    False,
                    items,
                    new_etag,
                    new_lm,
                    {"feed_url": feed_url, "count": len(items)},
                )

    # 4) Fallback to intelligent HTML listing extraction
    items = extract_html_listing_links(body_text, resp.url)
    return DiscoveryOutcome("html_fallback", False, items, new_etag, new_lm, {"count": len(items)})
