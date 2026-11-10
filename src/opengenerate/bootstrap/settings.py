"""Validated, fail-fast configuration. Secrets never appear in error messages or reprs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, PydanticBaseSettingsSource, SettingsConfigDict

ENV_PREFIX = "OG_"
_BOT_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
_ALLOWLIST_ENTRY_RE = re.compile(r"^[A-Za-z0-9.-]+:\d{1,5}$")


class ConfigError(Exception):
    """Configuration is invalid. The message lists fields and reasons only, never values."""


def _split_csv(value: Any) -> Any:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid", frozen=True)

    environment: Literal["production", "staging", "development", "test"] = "production"
    database_url: SecretStr
    bot_token: SecretStr
    superadmin_ids: Annotated[list[int], NoDecode] = Field(min_length=1)
    encryption_keys: SecretStr
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True
    ops_host: str = "127.0.0.1"
    ops_port: int = Field(default=8080, ge=1, le=65535)
    shutdown_grace_seconds: float = Field(default=30.0, ge=1, le=600)
    local_llm_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)
    http_max_redirects: int = Field(default=5, ge=0, le=10)
    http_max_response_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    http_total_timeout_seconds: float = Field(default=30.0, ge=1, le=300)
    rich_messages: Literal["auto", "on", "off"] = "auto"
    send_enabled: bool = True
    telegram_api_base: str = "https://api.telegram.org"
    db_pool_size: int = Field(default=5, ge=1, le=40)
    git_sha: str = "dev"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Only explicit input (see `load`) is used so behaviour never depends on ambient env.
        return (init_settings,)

    @field_validator("superadmin_ids", "local_llm_allowlist", mode="before")
    @classmethod
    def _csv(cls, v: Any) -> Any:
        return _split_csv(v)

    @field_validator("superadmin_ids")
    @classmethod
    def _positive_ids(cls, v: list[int]) -> list[int]:
        if any(i <= 0 for i in v):
            raise ValueError("superadmin ids must be positive Telegram user ids")
        return v

    @field_validator("local_llm_allowlist")
    @classmethod
    def _allowlist_shape(cls, v: list[str]) -> list[str]:
        for entry in v:
            if not _ALLOWLIST_ENTRY_RE.match(entry):
                raise ValueError("allowlist entries must look like host:port")
        return [e.lower() for e in v]

    @field_validator("database_url")
    @classmethod
    def _db_scheme(cls, v: SecretStr) -> SecretStr:
        if not re.match(r"^postgres(ql)?(\+\w+)?://", v.get_secret_value()):
            raise ValueError("must be a PostgreSQL URL")
        return v

    @field_validator("bot_token")
    @classmethod
    def _token_shape(cls, v: SecretStr) -> SecretStr:
        if not _BOT_TOKEN_RE.match(v.get_secret_value()):
            raise ValueError("does not look like a Telegram bot token")
        return v

    @field_validator("encryption_keys")
    @classmethod
    def _keys_valid(cls, v: SecretStr) -> SecretStr:
        keys = _split_csv(v.get_secret_value())
        if not keys:
            raise ValueError("at least one key is required")
        for key in keys:
            try:
                Fernet(key.encode())
            except (ValueError, TypeError):
                raise ValueError("keys must be valid Fernet keys (url-safe base64, 32 bytes)") from None
        return v

    @field_validator("telegram_api_base")
    @classmethod
    def _tg_base(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def encryption_key_list(self) -> list[str]:
        return _split_csv(self.encryption_keys.get_secret_value())  # type: ignore[no-any-return]

    def validate_cross_field(self) -> None:
        if self.environment in {"production", "staging"} and not self.telegram_api_base.startswith(
            "https://"
        ):
            raise ConfigError("telegram_api_base must use https outside development/test")

    @classmethod
    def load(cls, environ: Mapping[str, str]) -> Settings:
        """Build settings from `OG_*` variables, resolving `OG_<NAME>_FILE` secret files."""
        raw: dict[str, str] = {}
        file_refs: dict[str, str] = {}
        for name, value in environ.items():
            if not name.startswith(ENV_PREFIX):
                continue
            key = name[len(ENV_PREFIX) :].lower()
            if key.endswith("_file"):
                file_refs[key[: -len("_file")]] = value
            else:
                raw[key] = value
        for key, path in file_refs.items():
            if key in raw:
                raise ConfigError(f"both OG_{key.upper()} and OG_{key.upper()}_FILE are set")
            try:
                raw[key] = Path(path).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ConfigError(f"cannot read secret file for OG_{key.upper()}_FILE: {exc.strerror}") from None
        try:
            settings = cls(**raw)  # type: ignore[arg-type]
        except ValidationError as exc:
            problems = []
            for err in exc.errors(include_input=False, include_url=False):
                loc = ".".join(str(p) for p in err["loc"]) or "settings"
                problems.append(f"OG_{loc.upper()}: {err['msg']}")
            raise ConfigError("invalid configuration: " + "; ".join(problems)) from None
        settings.validate_cross_field()
        return settings
