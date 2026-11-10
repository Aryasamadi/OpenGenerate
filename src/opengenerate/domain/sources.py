"""Pure domain logic for Source URL canonicalization, SimHash near-duplicate detection, Ad heuristics & Backoff."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from opengenerate.domain.errors import ValidationFailed

DiscoveryType = Literal["auto", "rss", "json_feed", "sitemap", "html", "custom_api"]
VALID_DISCOVERY_TYPES: frozenset[str] = frozenset(
    {"auto", "rss", "json_feed", "sitemap", "html", "custom_api"}
)

TRACKING_QUERY_PREFIXES = ("utm_", "hsa_", "mtm_", "pk_")
TRACKING_QUERY_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "yclid",
        "twclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
        "vero_id",
        "rb_clickid",
        "spm",
        "ref_src",
        "ref_url",
    }
)

_WORD_RE = re.compile(r"[\w\u0600-\u06FF]+", re.UNICODE)

SPONSORED_MARKERS = (
    "رپورتاژ آگهی",
    "رپورتاژ تبلیغاتی",
    "مطلب تبلیغاتی",
    "محتوای تبلیغاتی",
    "پست تبلیغاتی",
    "sponsored post",
    "sponsored content",
    "paid partnership",
    "paid advertisement",
    "advertorial",
    "partner content",
)

PROMO_CALLOUTS = (
    "کد تخفیف",
    "لینک خرید",
    "خرید اقساطی",
    "همین حالا بخرید",
    "سفارش آنلاین",
    "buy now",
    "limited time offer",
    "use promo code",
    "discount code",
    "click here to buy",
    "affiliate link",
)


def canonicalize_url(raw_url: str) -> str:
    """Normalize a source or article URL and strip tracking query parameters and fragments."""
    candidate = raw_url.strip()
    if not candidate:
        raise ValidationFailed("URL cannot be empty")
    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValidationFailed("URL scheme must be http or https")
    if not parts.hostname:
        raise ValidationFailed("URL must include a hostname")

    host = parts.hostname.lower()
    port = parts.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = f"{host}:{port}" if port else host

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    filtered_query: list[tuple[str, str]] = []
    for k, v in parse_qsl(parts.query, keep_blank_values=False):
        kl = k.lower()
        if kl in TRACKING_QUERY_KEYS or any(kl.startswith(p) for p in TRACKING_QUERY_PREFIXES):
            continue
        filtered_query.append((k, v))
    filtered_query.sort(key=lambda kv: (kv[0].lower(), kv[1]))
    query_str = urlencode(filtered_query, doseq=True)

    return urlunsplit((scheme, netloc, path, query_str, ""))


def compute_url_hash(url: str) -> str:
    """Compute SHA-256 hex digest of the canonicalized URL."""
    canon = canonicalize_url(url)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def compute_content_fingerprint(title: str, body: str) -> str:
    """Compute a 64-bit SimHash hex fingerprint over unigrams and 2-word shingles of `(title + body)`."""
    tokens = [t.lower() for t in _WORD_RE.findall(f"{title} {body}") if len(t) > 1]
    if not tokens:
        return "0" * 16
    features = list(tokens)
    if len(tokens) >= 2:
        features.extend(f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1))

    weights = [0] * 64
    for feat in features:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        h = int.from_bytes(digest, "big")
        for bit in range(64):
            if (h >> bit) & 1:
                weights[bit] += 1
            else:
                weights[bit] -= 1

    simhash = 0
    for bit in range(64):
        if weights[bit] > 0:
            simhash |= 1 << bit
    return f"{simhash:016x}"


def fingerprint_hamming_distance(fp_a: str, fp_b: str) -> int:
    """Return bit-level Hamming distance between two 64-bit hex fingerprints."""
    if not fp_a or not fp_b:
        return 64
    try:
        val_a = int(fp_a, 16)
        val_b = int(fp_b, 16)
    except ValueError:
        return 64
    return (val_a ^ val_b).bit_count()


def is_near_duplicate(fp_a: str | None, fp_b: str | None, *, max_hamming_bits: int = 8) -> bool:
    """Return True if two 64-bit SimHash fingerprints differ by at most `max_hamming_bits`."""
    if not fp_a or not fp_b:
        return False
    return fingerprint_hamming_distance(fp_a, fp_b) <= max_hamming_bits


def detect_ad_heuristic(title: str, body: str) -> tuple[bool, str | None]:
    """Fast heuristic check for sponsored/advertorial content before spending AI tokens."""
    combined = f"{title}\n{body}".lower()
    for marker in SPONSORED_MARKERS:
        if marker in combined:
            return True, f"sponsored_marker:{marker}"

    promo_hits = sum(1 for phrase in PROMO_CALLOUTS if phrase in combined)
    if promo_hits >= 3:
        return True, f"excessive_promo_phrases:{promo_hits}"
    return False, None


def matches_topic_filters(
    title: str,
    body: str,
    *,
    include_keywords: Sequence[str] = (),
    exclude_keywords: Sequence[str] = (),
) -> tuple[bool, str | None]:
    """Verify whether article text satisfies per-channel include and exclude keyword rules."""
    combined = f"{title}\n{body}".lower()
    for kw in exclude_keywords:
        norm = kw.strip().lower()
        if norm and norm in combined:
            return False, f"excluded_keyword:{norm}"

    clean_inc = [k.strip().lower() for k in include_keywords if k.strip()]
    if clean_inc and not any(k in combined for k in clean_inc):
        return False, "missing_included_keyword"
    return True, None


def is_article_fresh(
    published_at: datetime | None,
    *,
    now: datetime,
    max_age_days: int,
) -> bool:
    """Return True if `published_at` is None (undated) or within `max_age_days` of `now`."""
    if published_at is None:
        return True
    cutoff = now - timedelta(days=max_age_days)
    return published_at >= cutoff


def compute_source_cooldown(
    failure_count: int,
    *,
    now: datetime,
    base_seconds: int = 60,
    max_seconds: int = 21_600,
) -> datetime | None:
    """Compute exponential cooldown timestamp for a failing source (`None` when `failure_count <= 0`)."""
    if failure_count <= 0:
        return None
    delay = min(max_seconds, base_seconds * (2 ** min(failure_count - 1, 10)))
    return now + timedelta(seconds=delay)
