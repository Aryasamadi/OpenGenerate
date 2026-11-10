"""Media Selection, Validation & Watermark Pipeline with graceful fallback."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from opengenerate.domain.channels import MediaMode, WatermarkSettings
from opengenerate.infrastructure.extractor import is_valid_article_media_url

_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".m4v")


@dataclass(frozen=True, slots=True)
class PreparedMedia:
    mode: MediaMode
    urls: tuple[str, ...]
    watermarked: bool = False
    watermark_signature: str | None = None


def is_video_url(url: str) -> bool:
    path = urlsplit(url.strip()).path.lower()
    return path.endswith(_VIDEO_EXTENSIONS)


def apply_watermark_descriptor(url: str, watermark: WatermarkSettings) -> tuple[str, str]:
    """Attach a deterministic watermark descriptor to an image asset URL/token and return `(url, signature)`."""
    sig_input = f"{url}|{watermark.text_or_asset}|{watermark.position}|{watermark.opacity}"
    sig = hashlib.sha256(sig_input.encode("utf-8")).hexdigest()[:12]
    parts = urlsplit(url)
    query = list(parse_qsl(parts.query, keep_blank_values=True))
    query.append(("og_wm", sig))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)), sig


def prepare_article_media(
    media_urls: Sequence[str],
    *,
    media_mode: MediaMode,
    watermark: WatermarkSettings,
) -> PreparedMedia:
    """Filter and prepare article media according to `media_mode` and `watermark` settings."""
    if media_mode == "none" or not media_urls:
        return PreparedMedia(mode="none", urls=())

    valid = [u for u in media_urls if is_valid_article_media_url(u)]
    if not valid:
        return PreparedMedia(mode="none", urls=())

    videos = [u for u in valid if is_video_url(u)]
    images = [u for u in valid if not is_video_url(u)]

    if media_mode == "video":
        if videos:
            return PreparedMedia(mode="video", urls=(videos[0],))
        if images:
            # Graceful fallback to single_image when no video is present in the article
            return _watermark_images((images[0],), mode="single_image", watermark=watermark)
        return PreparedMedia(mode="none", urls=())

    if media_mode == "multi_image_album":
        if len(images) >= 2:
            return _watermark_images(tuple(images[:10]), mode="multi_image_album", watermark=watermark)
        if len(images) == 1:
            return _watermark_images((images[0],), mode="single_image", watermark=watermark)
        return PreparedMedia(mode="none", urls=())

    # single_image
    if images:
        return _watermark_images((images[0],), mode="single_image", watermark=watermark)
    return PreparedMedia(mode="none", urls=())


def _watermark_images(
    image_urls: tuple[str, ...],
    *,
    mode: MediaMode,
    watermark: WatermarkSettings,
) -> PreparedMedia:
    if not watermark.enabled or not watermark.text_or_asset.strip():
        return PreparedMedia(mode=mode, urls=image_urls, watermarked=False)
    stamped: list[str] = []
    first_sig: str | None = None
    for u in image_urls:
        wm_u, sig = apply_watermark_descriptor(u, watermark)
        stamped.append(wm_u)
        if first_sig is None:
            first_sig = sig
    return PreparedMedia(
        mode=mode,
        urls=tuple(stamped),
        watermarked=True,
        watermark_signature=first_sig,
    )
