# ADR-0001 — Language and runtime

**Status:** Accepted

## Context
Workload is I/O-bound (Telegram, HTTP fetch, LLM calls, Postgres) with modest CPU in HTML extraction and formatting. Team is small. The product needs mature libraries for Telegram, feed/HTML parsing, and typed validation. The operator floated Python and Rust as non-binding ideas.

## Decision
- **Python 3.13**, `asyncio`, fully typed (`mypy --strict` on `src/`), `ruff` lint/format, `pydantic v2` for boundaries and settings.
- One language for the entire system now.
- Dependency management: `uv` with a committed lockfile and hash-pinned `requirements` export for the image build.
- CPU-bound work (HTML extraction, Markdown/AST rendering of large inputs) runs in a **bounded** `ProcessPoolExecutor`, never on the event loop.

## Alternatives rejected
- **Rust now:** best memory/CPU, but second toolchain, FFI/IPC boundary, and weak fit with the Python-centric extraction/Telegram ecosystem. No measurement shows a need.
- **Go:** excellent runtime, but thinner ecosystem for readability extraction and Telegram Bot API typing; full rewrite of domain-shaped libraries.
- **TypeScript:** adequate, weaker typing guarantees at runtime boundaries and for money logic.

## Consequences
- Memory is higher than Go/Rust (target and measurement in Phase 9; initial budget ≤ 350 MB RSS per role at the reference load).
- GIL is irrelevant for I/O; CPU hotspots are isolated by the process pool.

## Revisit trigger (Rust gate)
A Rust component is considered **only if** Phase 9 benchmarks show extraction/formatting exceeding the targets in `target-blueprint.md §10` after profiling and pure-Python optimisation, **and** the component can be isolated behind an existing port (`ArticleExtractor`, `RichRenderer`) with its own CI and wheel build. Otherwise the decision stands.
