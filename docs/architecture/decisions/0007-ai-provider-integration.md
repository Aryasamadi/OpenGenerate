# ADR-0007 — AI provider integration

**Status:** Accepted

## Decision
- `LlmProvider` port with three wire-format adapters on **one SSRF-safe httpx transport**: OpenAI-compatible chat completions, Anthropic messages, Gemini generateContent. Provider coverage claims are limited to these three formats; "compatible" endpoints are accepted only if they pass the model probe.
- **Routing policy (explicit and observable):** candidates = the channel owner's active personal models (by priority) **then** global active models (by priority), skipping quarantined ones. Precedence is configurable per channel (`personal_first` default, `global_only`, `personal_only`); every attempt is written to `ai_usage` with provider, outcome class and latency.
- **Error taxonomy:** `Transient` (timeout, 429, 5xx, connection), `Permanent` (401/403, 400 schema, model-not-found), `InvalidOutput` (malformed JSON/contract violation). Retries only for `Transient` and one repair attempt for `InvalidOutput`, with exponential backoff and full jitter, bounded by a per-article budget (calls, wall time, tokens) tracked in an isolated `ArticleBudget` object (no shared mutable state across concurrent articles).
- **Circuit breaker per model:** open after N transient failures in window → `quarantined_until`; half-open bounded probes; permanent failures disable until an admin re-tests.
- **Usage limits:** per-user per-day AI call/token counters reserved atomically before a call and reconciled after.
- **Cache:** key = SHA-256(canonical content hash ‖ effective settings fingerprint ‖ prompt-template version ‖ output language ‖ format policy); TTL; invalidated implicitly by key change.
- **Long articles:** map-reduce chunk summarisation; the result carries `coverage` metadata (`full | chunked | truncated`) and `truncated` is never presented as full.
- **Output contract `ContentResult v1`** (versioned, pydantic-validated): `category, title, scores{criterion→0..10}, weighted_score (recomputed server-side, never trusted), domain, format, post_blocks (RichAST), full_blocks (RichAST|null), hashtags, ad_score, language, coverage`. Server recomputes score and validates bounds, language, length and block vocabulary.
- **Prompt-injection defence:** system policy separated from data; source text wrapped in delimited untrusted-data envelope with explicit instructions never to follow embedded directives; output validated structurally; scores clamped; no tool/URL execution from model output.
- Secrets never reach logs/UI (redaction processor + typed `SecretStr`).

## Alternatives rejected
Vendor SDKs, LiteLLM (see matrix D6).

## Consequences
We own adapters (3 small modules, contract-tested with recorded fixtures and a fake server).

## Revisit trigger
A fourth wire format required by customers, or a provider changing semantics.
