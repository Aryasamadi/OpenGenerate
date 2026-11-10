"""Pure domain models and validation for Channels, 24 Editorial Settings, and Transfer Tokens."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from opengenerate.domain.errors import ValidationFailed

ContentMode = Literal["summary_link", "full_post", "continuation_bot"]
MediaMode = Literal["none", "single_image", "multi_image_album", "video"]
WatermarkPosition = Literal["top_left", "top_right", "bottom_left", "bottom_right", "center"]

MIN_TOKEN_TTL_HOURS = 1
MAX_TOKEN_TTL_HOURS = 168
DEFAULT_TOKEN_TTL_HOURS = 24


@dataclass(frozen=True, slots=True)
class ScoringWeights:
    relevance: float = 0.25
    novelty: float = 0.15
    depth: float = 0.20
    clarity: float = 0.15
    credibility: float = 0.15
    engagement: float = 0.10

    def __post_init__(self) -> None:
        vals = (
            self.relevance,
            self.novelty,
            self.depth,
            self.clarity,
            self.credibility,
            self.engagement,
        )
        if any(v < 0 for v in vals):
            raise ValidationFailed("scoring weights cannot be negative")
        if sum(vals) <= 0:
            raise ValidationFailed("sum of scoring weights must be positive")

    def normalized(self) -> dict[str, float]:
        total = (
            self.relevance
            + self.novelty
            + self.depth
            + self.clarity
            + self.credibility
            + self.engagement
        )
        return {
            "relevance": self.relevance / total,
            "novelty": self.novelty / total,
            "depth": self.depth / total,
            "clarity": self.clarity / total,
            "credibility": self.credibility / total,
            "engagement": self.engagement / total,
        }


@dataclass(frozen=True, slots=True)
class WatermarkSettings:
    enabled: bool = False
    text_or_asset: str = ""
    position: WatermarkPosition = "bottom_right"
    opacity: int = 70  # 10..100

    def __post_init__(self) -> None:
        if not 10 <= self.opacity <= 100:
            raise ValidationFailed("watermark opacity must be between 10 and 100")
        if self.position not in (
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
            "center",
        ):
            raise ValidationFailed("invalid watermark position")


@dataclass(frozen=True, slots=True)
class ActiveHoursWindow:
    quiet_hours_enabled: bool = False
    start_hour: int = 8  # 0..23 inclusive
    end_hour: int = 23  # 0..23 inclusive
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        if not (0 <= self.start_hour <= 23 and 0 <= self.end_hour <= 23):
            raise ValidationFailed("start_hour and end_hour must be within 0..23")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, KeyError) as exc:
            raise ValidationFailed(f"invalid timezone: {self.timezone}") from exc

    def is_open_at(self, utc_now: datetime) -> bool:
        """Return True if publishing is allowed at `utc_now` under this active window."""
        if not self.quiet_hours_enabled:
            return True
        local_hour = utc_now.astimezone(ZoneInfo(self.timezone)).hour
        if self.start_hour <= self.end_hour:
            return self.start_hour <= local_hour <= self.end_hour
        # Overnight active window (e.g., 22..06)
        return local_hour >= self.start_hour or local_hour <= self.end_hour


@dataclass(frozen=True, slots=True)
class ChannelSettings:
    """All 24 per-channel editorial, scheduling, AI, and media settings (Section 8.8)."""

    publish_interval_minutes: int = 60
    posts_per_cycle: int = 1
    max_article_age_days: int = 7
    min_score_threshold: int = 60
    language: str = "fa"
    writing_style: str = "journalistic"
    post_length: str = "medium"
    content_mode: ContentMode = "continuation_bot"
    source_link_credit_enabled: bool = True
    continuation_in_bot_enabled: bool = True
    read_more_button_text: str = "📖 ادامه مطلب در ربات"
    channel_signature_footer: str = ""
    post_prefix_header: str = ""
    hashtags_enabled: bool = True
    auto_categorization_enabled: bool = True
    custom_categories: tuple[str, ...] = ()
    topic_keywords_include: tuple[str, ...] = ()
    topic_keywords_exclude: tuple[str, ...] = ()
    ad_detection_enabled: bool = True
    scoring_weights: ScoringWeights = field(default_factory=ScoringWeights)
    media_mode: MediaMode = "single_image"
    watermark: WatermarkSettings = field(default_factory=WatermarkSettings)
    active_hours: ActiveHoursWindow = field(default_factory=ActiveHoursWindow)
    rich_message_formatting_enabled: bool = True

    def __post_init__(self) -> None:
        if self.publish_interval_minutes < 1:
            raise ValidationFailed("publish_interval_minutes must be >= 1")
        if self.posts_per_cycle < 1:
            raise ValidationFailed("posts_per_cycle must be >= 1")
        if self.max_article_age_days < 1:
            raise ValidationFailed("max_article_age_days must be >= 1")
        if not 0 <= self.min_score_threshold <= 100:
            raise ValidationFailed("min_score_threshold must be within 0..100")
        if not self.language.strip():
            raise ValidationFailed("language cannot be empty")
        if self.content_mode not in ("summary_link", "full_post", "continuation_bot"):
            raise ValidationFailed("invalid content_mode")
        if self.media_mode not in ("none", "single_image", "multi_image_album", "video"):
            raise ValidationFailed("invalid media_mode")

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["custom_categories"] = list(self.custom_categories)
        raw["topic_keywords_include"] = list(self.topic_keywords_include)
        raw["topic_keywords_exclude"] = list(self.topic_keywords_exclude)
        return raw

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ChannelSettings:
        if not data:
            return cls()
        sw_raw = data.get("scoring_weights")
        sw = ScoringWeights(**sw_raw) if isinstance(sw_raw, dict) else ScoringWeights()
        wm_raw = data.get("watermark")
        wm = WatermarkSettings(**wm_raw) if isinstance(wm_raw, dict) else WatermarkSettings()
        ah_raw = data.get("active_hours")
        ah = ActiveHoursWindow(**ah_raw) if isinstance(ah_raw, dict) else ActiveHoursWindow()

        return cls(
            publish_interval_minutes=int(data.get("publish_interval_minutes", 60)),
            posts_per_cycle=int(data.get("posts_per_cycle", 1)),
            max_article_age_days=int(data.get("max_article_age_days", 7)),
            min_score_threshold=int(data.get("min_score_threshold", 60)),
            language=str(data.get("language", "fa")),
            writing_style=str(data.get("writing_style", "journalistic")),
            post_length=str(data.get("post_length", "medium")),
            content_mode=data.get("content_mode", "continuation_bot"),
            source_link_credit_enabled=bool(data.get("source_link_credit_enabled", True)),
            continuation_in_bot_enabled=bool(data.get("continuation_in_bot_enabled", True)),
            read_more_button_text=str(
                data.get("read_more_button_text", "📖 ادامه مطلب در ربات")
            ),
            channel_signature_footer=str(data.get("channel_signature_footer", "")),
            post_prefix_header=str(data.get("post_prefix_header", "")),
            hashtags_enabled=bool(data.get("hashtags_enabled", True)),
            auto_categorization_enabled=bool(data.get("auto_categorization_enabled", True)),
            custom_categories=tuple(str(x) for x in data.get("custom_categories", ())),
            topic_keywords_include=tuple(str(x) for x in data.get("topic_keywords_include", ())),
            topic_keywords_exclude=tuple(str(x) for x in data.get("topic_keywords_exclude", ())),
            ad_detection_enabled=bool(data.get("ad_detection_enabled", True)),
            scoring_weights=sw,
            media_mode=data.get("media_mode", "single_image"),
            watermark=wm,
            active_hours=ah,
            rich_message_formatting_enabled=bool(
                data.get("rich_message_formatting_enabled", True)
            ),
        )


def generate_transfer_token() -> tuple[str, str]:
    """Generate a high-entropy transfer token and its SHA-256 hex digest `(raw_token, token_hash)`."""
    raw = f"ogch_{secrets.token_urlsafe(24)}"
    return raw, hash_transfer_token(raw)


def hash_transfer_token(raw_token: str) -> str:
    norm = raw_token.strip()
    if not norm:
        raise ValidationFailed("transfer token cannot be empty")
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def validate_token_ttl_hours(ttl_hours: int) -> int:
    if not MIN_TOKEN_TTL_HOURS <= ttl_hours <= MAX_TOKEN_TTL_HOURS:
        raise ValidationFailed(
            f"token TTL must be between {MIN_TOKEN_TTL_HOURS} and {MAX_TOKEN_TTL_HOURS} hours"
        )
    return ttl_hours
