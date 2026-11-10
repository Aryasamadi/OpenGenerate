"""Structured JSON logging with mandatory secret redaction (also for third-party/stdlib logs)."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![\w-])(?:bot)?\d{6,12}:[A-Za-z0-9_-]{30,}"), "[REDACTED:bot_token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[REDACTED:api_key]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "[REDACTED:api_key]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(?!\[REDACTED|Bearer \[REDACTED)[^\s\"',}]+"),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)(\w+://[^/\s:@]+:)[^@\s/]+(@)"), r"\1[REDACTED]\2"),
    (
        re.compile(
            r"(?i)([?&](?:api[_-]?key|key|token|access[_-]?token|secret|password|signature|sig|auth)=)"
            r"[^&\s\"']+"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}=(?![A-Za-z0-9_-])"), "[REDACTED:key]"),
    (re.compile(r"\bv1:[0-9a-f]{8}:[A-Za-z0-9_=-]{20,}"), "[REDACTED:ciphertext]"),
)

_SENSITIVE_KEY = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|apikey|authorization|cookie|receipt|private[_-]?key|"
    r"encryption[_-]?keys?|database[_-]?url)",
    re.IGNORECASE,
)


def redact_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_value(value: Any, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {k: redact_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [redact_value(v) for v in value]
    if isinstance(value, BaseException):
        return redact_text(f"{type(value).__name__}: {value}")
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact_text(str(value))


def redaction_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        event_dict[key] = redact_value(event_dict[key], key)
    return event_dict


def configure_logging(level: str = "INFO", *, json: bool = True, stream: Any = None) -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        redaction_processor,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[*shared[:3], redaction_processor],
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs full request URLs (Telegram tokens live in the path) at INFO.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def bind_correlation_id(correlation_id: str) -> None:
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_logger(name: str = "opengenerate") -> Any:
    return structlog.get_logger(name)
