from __future__ import annotations

import io
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from opengenerate.bootstrap.settings import ConfigError, Settings
from opengenerate.infrastructure.crypto import DecryptionError, FieldCipher
from opengenerate.infrastructure.logging import (
    REDACTED,
    bind_correlation_id,
    clear_context,
    configure_logging,
    get_logger,
    redact_text,
)


def test_settings_load_and_secret_file_resolution(valid_env: dict[str, str], tmp_path: Path) -> None:
    token_file = tmp_path / "bot_token.txt"
    token_val = valid_env.pop("OG_BOT_TOKEN")
    token_file.write_text(token_val + "\n", encoding="utf-8")
    valid_env["OG_BOT_TOKEN_FILE"] = str(token_file)

    settings = Settings.load(valid_env)
    assert settings.superadmin_ids == [111, 222]
    assert settings.bot_token.get_secret_value() == token_val
    assert token_val not in repr(settings)


def test_settings_fail_fast_without_leaking_secret_values(valid_env: dict[str, str]) -> None:
    bad_token = "not-a-valid-bot-token-secret-value"
    valid_env["OG_BOT_TOKEN"] = bad_token
    with pytest.raises(ConfigError) as exc_info:
        Settings.load(valid_env)
    assert "OG_BOT_TOKEN" in str(exc_info.value)
    assert bad_token not in str(exc_info.value)


def test_settings_rejects_both_env_and_file(valid_env: dict[str, str], tmp_path: Path) -> None:
    f = tmp_path / "tok"
    f.write_text(valid_env["OG_BOT_TOKEN"], encoding="utf-8")
    valid_env["OG_BOT_TOKEN_FILE"] = str(f)
    with pytest.raises(ConfigError, match="both OG_BOT_TOKEN and OG_BOT_TOKEN_FILE"):
        Settings.load(valid_env)


def test_field_cipher_encrypt_decrypt_and_key_rotation() -> None:
    k_old = Fernet.generate_key().decode()
    k_new = Fernet.generate_key().decode()
    c_old = FieldCipher([k_old])
    ct = c_old.encrypt("sk-super-secret-model-key-1234567890")
    assert ct.startswith("v1:")
    assert "sk-super-secret" not in ct
    assert k_old not in repr(c_old)

    # Rotate with [k_new, k_old]
    c_rot = FieldCipher([k_new, k_old])
    assert c_rot.decrypt(ct) == "sk-super-secret-model-key-1234567890"
    assert c_rot.needs_rotation(ct)
    rotated = c_rot.rotate(ct)
    assert not c_rot.needs_rotation(rotated)
    assert FieldCipher([k_new]).decrypt(rotated) == "sk-super-secret-model-key-1234567890"

    with pytest.raises(DecryptionError):
        FieldCipher([k_new]).decrypt(ct)
    with pytest.raises(DecryptionError):
        c_rot.decrypt(rotated[:-4] + "XXXX")


def test_structured_logging_redacts_secrets_and_includes_correlation_id() -> None:
    buf = io.StringIO()
    configure_logging("INFO", json=True, stream=buf)
    clear_context()
    bind_correlation_id("corr-12345")
    log = get_logger("test")
    bot_tok = "123456789:AAH_secret_telegram_token_0123456789abcdef"
    openai_key = "sk-proj-1234567890abcdefghijklmnop"
    db_dsn = "postgresql://admin:MyS3cretPass!@db.internal:5432/og"
    log.info(
        "probe_failed",
        api_key=openai_key,
        detail=f"called https://api.telegram.org/bot{bot_tok}/getMe?api_key=xyz987 with Bearer {openai_key} and {db_dsn}",
    )
    output = buf.getvalue()
    assert "corr-12345" in output
    assert REDACTED in output
    assert bot_tok not in output
    assert openai_key not in output
    assert "MyS3cretPass!" not in output
    assert "xyz987" not in output
    assert "[REDACTED" in redact_text(bot_tok)
    clear_context()
