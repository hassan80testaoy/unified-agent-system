#!/usr/bin/env bash
# restore.sh — Bidirectional encrypted restore (Local <-> VPS)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_FILE="${1:-${ROOT_DIR}/backups/latest.tar.gz.enc}"

if [ ! -f "${BACKUP_FILE}" ]; then
  echo "ERROR: Backup file not found: ${BACKUP_FILE}" >&2
  exit 1
fi

if [ -f "${ROOT_DIR}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

: "${BACKUP_ENC_KEY:?Set BACKUP_ENC_KEY in .env or environment before running restore.sh}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -in "${BACKUP_FILE}" -out "${TMP_DIR}/bundle.tar.gz" -pass env:BACKUP_ENC_KEY
tar -xzf "${TMP_DIR}/bundle.tar.gz" -C "${TMP_DIR}"

if [ -f "${TMP_DIR}/db.sql" ]; then
  echo "Restoring PostgreSQL dump..."
  docker compose exec -T postgres psql -U "${POSTGRES_USER:-unified_agent}" -d "${POSTGRES_DB:-unified_agent}" < "${TMP_DIR}/db.sql"
elif [ -f "${TMP_DIR}/unified_agent.db" ]; then
  echo "Restoring SQLite database..."
  mkdir -p "${ROOT_DIR}/backend/data"
  cp "${TMP_DIR}/unified_agent.db" "${ROOT_DIR}/backend/data/unified_agent.db"
fi

if [ -f "${TMP_DIR}/data.tar.gz" ]; then
  tar -xzf "${TMP_DIR}/data.tar.gz" -C "${ROOT_DIR}"
fi

echo "Restore completed from ${BACKUP_FILE}"
