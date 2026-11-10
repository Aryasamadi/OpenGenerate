"""Raw Telegram Bot API client with delivery-aware error classification (ADR-0006).

Classification matters for publishing: only failures that *provably* did not reach Telegram are
`TransientError`; anything ambiguous after request bytes may have left is `UnknownOutcomeError`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import SecretStr

from opengenerate.domain.errors import PermanentError, TransientError, UnknownOutcomeError

_BLOCKED_HINTS = (
    "bot was blocked by the user",
    "user is deactivated",
    "bot was kicked",
    "chat not found",
    "have no rights to send",
    "bot is not a member",
    "peer_id_invalid",
)


class TelegramApiError(PermanentError):
    def __init__(self, code: int, description: str) -> None:
        super().__init__(f"telegram {code}: {description}")
        self.code = code
        self.description = description


class RecipientUnreachable(TelegramApiError):
    """User blocked the bot / chat gone: permanent for that recipient."""


class RawBotApi:
    def __init__(
        self,
        token: SecretStr,
        *,
        base_url: str = "https://api.telegram.org",
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0), trust_env=False
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def __repr__(self) -> str:
        return f"RawBotApi(base={self._base})"

    async def call(
        self, method: str, payload: Mapping[str, Any] | None = None, *, idempotent: bool = False
    ) -> Any:
        """Invoke a Bot API method. `idempotent=True` allows 5xx to be treated as safe-to-retry."""
        url = f"{self._base}/bot{self._token.get_secret_value()}/{method}"
        try:
            response = await self._client.post(url, json=dict(payload or {}))
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise TransientError("could not connect to Telegram") from None
        except httpx.HTTPError as exc:
            # Request bytes may have been written: the outcome is genuinely unknown.
            raise UnknownOutcomeError(f"{type(exc).__name__} after send") from None
        return self._interpret(response, idempotent)

    @staticmethod
    def _interpret(response: httpx.Response, idempotent: bool) -> Any:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 200 and body.get("ok") is True:
            return body.get("result")
        code = int(body.get("error_code", response.status_code))
        description = str(body.get("description", response.reason_phrase or "error"))
        if code == 429:
            retry_after = float((body.get("parameters") or {}).get("retry_after", 1))
            raise TransientError("rate limited by Telegram", retry_after=retry_after)
        if code >= 500:
            if idempotent:
                raise TransientError(f"telegram server error {code}")
            raise UnknownOutcomeError(f"telegram server error {code}")
        lowered = description.lower()
        if code in {400, 403} and any(h in lowered for h in _BLOCKED_HINTS):
            raise RecipientUnreachable(code, description)
        raise TelegramApiError(code, description)

    # --- typed helpers -----------------------------------------------------------------------
    async def get_me(self) -> dict[str, Any]:
        result: dict[str, Any] = await self.call("getMe", idempotent=True)
        return result

    async def send_message(
        self, chat_id: int | str, text: str, *, parse_mode: str | None = None,
        reply_markup: Mapping[str, Any] | None = None, disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "link_preview_options": {"is_disabled": disable_web_page_preview},
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = dict(reply_markup)
        result: dict[str, Any] = await self.call("sendMessage", payload)
        return result

    async def get_chat_member(self, chat_id: int | str, user_id: int) -> dict[str, Any]:
        result: dict[str, Any] = await self.call(
            "getChatMember", {"chat_id": chat_id, "user_id": user_id}, idempotent=True
        )
        return result


class TelegramOutboxSender:
    """Adapts `RawBotApi` to the outbox `MessageSender` port."""

    def __init__(self, api: RawBotApi) -> None:
        self._api = api

    async def send(self, recipient_id: int, kind: str, payload: Mapping[str, Any]) -> None:
        if kind != "text":
            raise PermanentError(f"unsupported outbox kind '{kind}'")
        await self._api.send_message(
            recipient_id, str(payload["text"]), parse_mode=payload.get("parse_mode")
        )
