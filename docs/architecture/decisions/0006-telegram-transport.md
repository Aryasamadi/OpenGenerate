# ADR-0006 — Telegram transport

**Status:** Accepted

## Context
Need reliable update handling, per-chat rate-limit awareness, and access to Bot API methods newer than the wrapper library. Web research (secondary sources only; **not yet verified against the primary Bot API reference**) indicates `sendRichMessage` arrived with Bot API 10.1, accepts structured `InputRichMessage` blocks (paragraphs, headings, tables, lists, quotes, code, collapsible sections, inline media), allows up to ~32 768 characters, and has companion edit/draft methods.

## Decision
- **python-telegram-bot 22.x** for update ingestion and standard methods, used **only** inside `interfaces/telegram` and `infrastructure/telegram`.
- **`TelegramGateway` port** in `application` (send text/media/rich, edit, answer callback, get member/chat, etc.) with two adapters: `PtbGateway` and `RawBotApi` (httpx, same SSRF-irrelevant fixed host `api.telegram.org`) for methods PTB lacks. Application code never sees PTB objects.
- **Long polling** by default (no public domain/TLS required); webhook mode supported behind config with secret-token header validation. Exactly one polling instance.
- **Capability detection for rich messages:** on startup and periodically, a probe records whether `sendRichMessage` is accepted (`RICH_MESSAGES=auto|on|off`). A circuit breaker opens after N consecutive *definite* API rejections (not network timeouts); every fallback records a reason metric. **Phase 6 must verify the exact request/response contract against the primary reference before coding the renderer's native path; the contract in this ADR is a working assumption until then.**
- **Ambiguous delivery:** timeouts/connection resets after request bytes were sent produce `UnknownOutcome`; never blind-retried (see ADR-0005).
- Per-chat and global token buckets respecting Telegram limits; `retry_after` honoured.

## Alternatives rejected
aiogram (comparable; no decisive gain, would discard PTB familiarity in the baseline); raw-only (rebuilds rate limiting, update parsing).

## Consequences
Two adapters to keep behaviourally aligned (contract-tested with a fake Bot API server).

## Revisit trigger
PTB exposes rich messages natively and the contract is stable.
