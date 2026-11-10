"""Ports implemented by infrastructure adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

ProviderType = Literal["openai", "anthropic", "gemini"]


@dataclass(frozen=True, slots=True)
class LlmRequest:
    provider_type: ProviderType
    base_url: str
    model: str
    system_prompt: str
    user_prompt: str
    api_key: str | None = None
    temperature: float = 0.3
    max_tokens: int = 2048


@dataclass(frozen=True, slots=True)
class LlmResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


class MessageSender(Protocol):
    """Delivers one outbound message.

    Raises `TransientError` (safe to retry), `PermanentError` (give up) or `UnknownOutcomeError`
    (delivery ambiguous).
    """

    async def send(self, recipient_id: int, kind: str, payload: Mapping[str, Any]) -> None: ...


class LlmProvider(Protocol):
    """Executes an LLM completion across OpenAI, Anthropic, or Gemini wire formats."""

    async def complete(self, request: LlmRequest) -> LlmResponse: ...
