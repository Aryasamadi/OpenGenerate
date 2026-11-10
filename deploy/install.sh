#!/usr/bin/env bash
# ============================================================================
# OpenGenerate v2.0 — Deterministic Fresh-Install & Cutover Script
# ============================================================================
set -euo pipefail

ENV_FILE="${1:-.env}"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "ERROR: Environment file '${ENV_FILE}' not found. Copy .env.example to .env first." >&2
    exit 1
fi

echo "[1/4] Validating environment configuration..."
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

python3 -m opengenerate.bootstrap.cli check-config

echo "[2/4] Applying Alembic PostgreSQL schema migrations..."
python3 -m opengenerate.bootstrap.cli migrate

if [[ -n "${LEGACY_SQLITE_PATH:-}" ]]; then
    echo "[3/4] Running Legacy SQLite -> PostgreSQL ETL & 10-Gate Reconciliation..."
    python3 tools/migrate_sqlite_to_pg.py \
        --sqlite-path "${LEGACY_SQLITE_PATH}" \
        --pg-dsn "${OG_DATABASE_URL}" \
        --encryption-key "${OG_ENCRYPTION_KEYS}" \
        --report-out "migration-report.md"
else
    echo "[3/4] Fresh install mode (no LEGACY_SQLITE_PATH specified)."
fi

echo "[4/4] Starting OpenGenerate service stack..."
if command -v docker >/dev/null 2>&1 && [[ -f "docker-compose.yml" ]]; then
    docker compose --env-file "${ENV_FILE}" up -d --build
    echo "OpenGenerate is running via Docker Compose."
else
    echo "Migrations complete. Start the runtime with: python3 -m opengenerate.bootstrap.cli run"
fi
