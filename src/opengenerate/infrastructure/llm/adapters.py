"""Thin wire-format adapters for OpenAI, Anthropic, and Gemini over `SafeHttpClient` (ADR-0007)."""

from __future__ import annotations

from typing import Any

from opengenerate.application.ports import LlmRequest, LlmResponse
from opengenerate.domain.errors import PermanentError, TransientError
from opengenerate.infrastructure.http_safe import SafeHttpClient, SafeResponse
from opengenerate.infrastructure.logging import redact_text


class HttpLlmProvider:
    """Executes completions over `SafeHttpClient` and classifies transient vs permanent failures."""

    def __init__(self, http: SafeHttpClient) -> None:
        self._http = http

    async def complete(self, request: LlmRequest) -> LlmResponse:
        base = request.base_url.rstrip("/")
        if request.provider_type == "openai":
            url = base if base.endswith("/chat/completions") else f"{base}/v1/chat/completions"
            headers = {"Content-Type": "application/json"}
            if request.api_key:
                headers["Authorization"] = f"Bearer {request.api_key}"
            body: dict[str, Any] = {
                "model": request.model,
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
                "messages": [
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
            }
            resp = await self._http.post(url, headers=headers, json=body)
            self._check_status(resp)
            data = self._parse_json(resp)
            try:
                text = str(data["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError) as exc:
                raise PermanentError("malformed OpenAI response payload") from exc
            usage = data.get("usage") or {}
            return LlmResponse(
                text=text,
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                model=str(data.get("model", request.model)),
            )

        if request.provider_type == "anthropic":
            url = base if base.endswith("/messages") else f"{base}/v1/messages"
            headers = {
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            if request.api_key:
                headers["x-api-key"] = request.api_key
            body = {
                "model": request.model,
                "system": request.system_prompt,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "messages": [{"role": "user", "content": request.user_prompt}],
            }
            resp = await self._http.post(url, headers=headers, json=body)
            self._check_status(resp)
            data = self._parse_json(resp)
            try:
                blocks = data["content"]
                text = "".join(str(b.get("text", "")) for b in blocks if isinstance(b, dict))
                if not text:
                    raise KeyError("empty content")
            except (KeyError, TypeError) as exc:
                raise PermanentError("malformed Anthropic response payload") from exc
            usage = data.get("usage") or {}
            return LlmResponse(
                text=text,
                prompt_tokens=int(usage.get("input_tokens", 0)),
                completion_tokens=int(usage.get("output_tokens", 0)),
                model=str(data.get("model", request.model)),
            )

        if request.provider_type == "gemini":
            url = (
                base
                if ":generateContent" in base
                else f"{base}/v1beta/models/{request.model}:generateContent"
            )
            headers = {"Content-Type": "application/json"}
            if request.api_key:
                headers["x-goog-api-key"] = request.api_key
            body = {
                "systemInstruction": {"parts": [{"text": request.system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": request.user_prompt}]}],
                "generationConfig": {
                    "temperature": request.temperature,
                    "maxOutputTokens": request.max_tokens,
                },
            }
            resp = await self._http.post(url, headers=headers, json=body)
            self._check_status(resp)
            data = self._parse_json(resp)
            try:
                parts = data["candidates"][0]["content"]["parts"]
                text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
                if not text:
                    raise KeyError("empty parts")
            except (KeyError, IndexError, TypeError) as exc:
                raise PermanentError("malformed Gemini response payload") from exc
            usage = data.get("usageMetadata") or {}
            return LlmResponse(
                text=text,
                prompt_tokens=int(usage.get("promptTokenCount", 0)),
                completion_tokens=int(usage.get("candidatesTokenCount", 0)),
                model=request.model,
            )

        raise PermanentError(f"unsupported provider_type: {request.provider_type}")

    @staticmethod
    def _check_status(resp: SafeResponse) -> None:
        code = resp.status_code
        if 200 <= code < 300:
            return
        detail = redact_text(resp.text[:300])
        if code == 429:
            retry_hdr = resp.headers.get("retry-after", "")
            retry_after = float(retry_hdr) if retry_hdr.isdigit() else None
            raise TransientError(f"provider rate limited (429): {detail}", retry_after=retry_after)
        if code >= 500 or code == 408:
            raise TransientError(f"provider server error ({code}): {detail}")
        raise PermanentError(f"provider client error ({code}): {detail}")

    @staticmethod
    def _parse_json(resp: SafeResponse) -> dict[str, Any]:
        try:
            parsed = resp.json()
        except ValueError as exc:
            raise PermanentError("provider returned non-JSON body") from exc
        if not isinstance(parsed, dict):
            raise PermanentError("provider JSON root must be an object")
        return parsed
