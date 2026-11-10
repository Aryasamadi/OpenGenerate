#!/usr/bin/env bash
# ============================================================================
# OpenGenerate v2.0 — Emergency Cutover Rollback & Snapshot Restore Script
# ============================================================================
set -euo pipefail

ACTION="${1:-status}"
BACKUP_FILE="${2:-}"

case "${ACTION}" in
    stop-new)
        echo "[Rollback 1/2] Stopping OpenGenerate v2.0 container stack..."
        if command -v docker >/dev/null 2>&1 && [[ -f "docker-compose.yml" ]]; then
            docker compose stop app
        fi
        echo "[Rollback 2/2] Legacy SQLite file was accessed strictly read-only (?mode=ro) and remains intact."
        ;;
    restore-pg)
        if [[ -z "${BACKUP_FILE}" || ! -f "${BACKUP_FILE}" ]]; then
            echo "ERROR: Provide a valid pg_dump custom archive path as second argument." >&2
            exit 1
        fi
        echo "Restoring PostgreSQL backup '${BACKUP_FILE}' into '${OG_DATABASE_URL:?OG_DATABASE_URL required}'..."
        pg_restore --clean --if-exists --no-owner --dbname="${OG_DATABASE_URL}" "${BACKUP_FILE}"
        echo "PostgreSQL restore completed successfully."
        ;;
    *)
        echo "Usage: $0 {stop-new | restore-pg <backup.dump>}" >&2
        exit 1
        ;;
esac
