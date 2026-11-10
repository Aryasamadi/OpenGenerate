# ADR-0012 — Deployment and release

**Status:** Accepted

## Decision
- **Compose project** with services `postgres`, `migrate` (one-shot), `bot`, `worker`; named volumes `pgdata`, `backups`, `media_tmp`; internal network; Postgres not published to the host; health checks on every long-running service; `restart: unless-stopped`; `stop_grace_period` ≥ `SHUTDOWN_GRACE`; log rotation.
- **Image:** multi-stage, `python:3.13-slim` pinned **by digest**, deps from the hash-locked file, non-root UID, read-only root FS with tmpfs/volumes for writable paths, no secrets in layers. Built in CI, tagged with the Git SHA, pushed to GHCR; deployments reference the **image digest**.
- **Release gates (CI):** ruff, mypy strict, import-linter, unit + Postgres-backed integration tests, UI action-registry coverage test, migration tests from prior schema snapshots, `pip-audit`, Trivy, image build and a container smoke test (start → `/readyz` → stop gracefully).
- **Deploy procedure (`deploy/deploy.sh`, serialized with a lock file):** 1) verify target digest exists and signature/SHA matches; 2) `pg_dump` custom-format backup + `pg_restore --list` verification + checksum; 3) run `migrate` against a **restored copy** (preflight) then real DB; 4) `up -d` new roles (drain old: stop claiming, wait grace); 5) wait for `/readyz`+`/healthz` and a Telegram `getMe` smoke; 6) on failure → stop new, restore previous digest, run **down-compatible** path: migrations follow expand/contract so previous code remains compatible with the new schema for one release; restore the pre-deploy backup only if a destructive migration was applied (flagged and requires explicit operator confirmation).
- **Backups:** nightly logical backup + WAL archiving option for PITR; retention policy; restore drill script; backup age feeds health/alerts.
- **First install:** `install.sh` (idempotent, never overwrites `.env`/volumes without confirmation) validates the environment, writes `.env` from template, generates the encryption key, runs preflight, starts the stack, verifies health.

## Alternatives rejected
Kubernetes (overkill), bind-mounted source (ambiguous runtime), systemd+venv (weak dependency rollback).

## Consequences
Rollback is real (digest + expand/contract schema) and rehearsed in Phase 8.

## Revisit trigger
Second VPS or zero-downtime requirement.
