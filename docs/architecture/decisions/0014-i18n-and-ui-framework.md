# ADR-0014 — UI framework: typed action registry, callbacks, i18n

**Status:** Accepted

## Decision
- **`ui-action-register.json` is the contract.** Each action is declared in code as an `ActionSpec(id, namespace, pattern, roles, policy, handler, destructive, confirm)` and a **registry test** diffs code ↔ JSON register ↔ callback usages in keyboards: fails on any button without a handler, any handler unreachable from a keyboard/command, callback > 64 bytes (computed with worst-case ids), privileged action without a policy, destructive action without confirmation, or missing automated test reference.
- **Thin adapters:** handler = parse → `Actor` → call application use-case → render view. Views are pure functions `(ViewModel, Locale) → Screen(text/AST, keyboard)`; no business logic.
- **Navigation:** every screen declares its parent; Back is computed, not stored free-form. Edits use `editMessageText`; guarded against stale messages via object-version nonce; `MessageNotModified` ignored.
- **Guided input:** a persisted `input_sessions` row (actor, action, object, step, expiry, nonce) — durable across restarts, single active session per user, `/cancel` and `c:cancel` clear it, and every continuation re-authorises.
- **Pagination** generic component (default 15/page, configurable) used by sources, categories, criteria, queue, users.
- **i18n:** message catalog keyed by stable ids with `fa` and `en` (ICU-style plurals, no string concatenation of fragments), superadmin overrides stored in `text_overrides` with HTML validation and reset; a test fails if a key is missing in either language or placeholders differ. Persian: RTL marks in mixed runs, Persian digits only for display chrome, tested via golden screens.
- **Long operations** show progress by editing a status message; duplicates prevented with single-flight keys.

## Consequences
The register drives acceptance for Phase 7; parity cannot be claimed without it passing.
