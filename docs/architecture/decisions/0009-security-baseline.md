# ADR-0009 — Security baseline

**Status:** Accepted

## Decision
- **AuthZ:** every use-case takes an `Actor(user_id, role)` and calls a `Policy` (`require_superadmin`, `require_channel_owner(channel_id)`, `require_source_owner`, `require_model_owner`, …) **inside the application layer**; UI visibility is never a control. Object IDs from callbacks are re-resolved and ownership-checked on every call, including *input continuations* (a pending-input session stores `(actor, object, action, nonce, expiry)` and is re-validated on message receipt).
- **Callback integrity:** compact typed callbacks `ns:action:args` ≤ 64 bytes, parsed by a registry; stale-button defence via a short `nonce/version` where an action targets mutable objects; single-flight guard (DB advisory/row lock or idempotency key) for confirmations.
- **SSRF-safe egress:** one `SafeHttpClient` used for sources, custom APIs, media and LLM endpoints: scheme allowlist, DNS resolution → IP policy (reject loopback, private, link-local, CGNAT, multicast, reserved, IPv4-mapped IPv6, metadata), **connect to the validated IP** (pinned, SNI/Host preserved) to defeat DNS rebinding, manual redirect handling with re-validation per hop (max hops), strip `Authorization`/API headers on origin change, response size/time caps, content-type checks. Local LLM endpoints only via an explicit `LOCAL_LLM_ALLOWLIST` (host:port). Telegram API host is the only fixed exception.
- **Secrets at rest:** field-level `MultiFernet` (key ring from env/`*_FILE`), key id stored with ciphertext, rotation command; plaintext never persisted or logged; messages containing pasted keys are deleted best-effort.
- **Redaction:** structlog processor strips bot tokens, `sk-…`, `Bearer …`, URL query secrets, receipt content; unit tests assert it.
- **Rate limits:** persisted sliding windows (`rate_limits`) for global/per-user/sensitive actions (receipts, support, token attempts, tests).
- **Transfer tokens:** 10-digit random from `secrets`, stored as HMAC-SHA256, TTL, attempt cap, one-time consumption in a transaction, blocked while a publication is active, auditable.
- **Admin audit:** immutable `audit_log` for every high-risk action (who, what, object, before/after hash, reason).
- **Supply chain/runtime:** pinned lockfile + hashes, `pip-audit`/`osv-scanner`, image scan (Trivy), non-root user, read-only root FS where possible, dropped capabilities, no secrets in image/build args.
- **Data minimisation:** receipts stored as Telegram `file_id` + metadata; retention job purges receipt text after configurable period post-decision.

## Alternatives rejected
Hidden-button security; per-handler ad-hoc checks (not provably complete).

## Consequences
A coverage test walks the action registry and fails if any action lacks a declared policy.

## Revisit trigger
Introduction of a web dashboard (adds authN/CSRF/session surface).
