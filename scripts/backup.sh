#!/usr/bin/env bash
# backup.sh — Encrypted backup for PostgreSQL (or SQLite local DB) + data/uploads tarball
# Works identically on laptop and VPS.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${ROOT_DIR}/backups"
mkdir -p "${BACKUP_DIR}"

if [ -f "${ROOT_DIR}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

: "${BACKUP_ENC_KEY:?Set BACKUP_ENC_KEY in .env or environment before running backup.sh}"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

if docker compose ps postgres >/dev/null 2>&1 && [ -n "$(docker compose ps -q postgres 2>/dev/null)" ]; then
  echo "Dumping PostgreSQL from Docker container..."
  docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-unified_agent}" "${POSTGRES_DB:-unified_agent}" > "${TMP_DIR}/db.sql"
elif [ -f "${ROOT_DIR}/backend/data/unified_agent.db" ]; then
  echo "Copying local SQLite database..."
  cp "${ROOT_DIR}/backend/data/unified_agent.db" "${TMP_DIR}/unified_agent.db"
elif [ -f "${ROOT_DIR}/data/unified_agent.db" ]; then
  cp "${ROOT_DIR}/data/unified_agent.db" "${TMP_DIR}/unified_agent.db"
else
  echo "ERROR: No running PostgreSQL container or local SQLite database found." >&2
  exit 1
fi

if [ -d "${ROOT_DIR}/data" ]; then
  tar -czf "${TMP_DIR}/data.tar.gz" -C "${ROOT_DIR}" data
fi

tar -czf "${TMP_DIR}/bundle.tar.gz" -C "${TMP_DIR}" .
OUT_FILE="${BACKUP_DIR}/backup_${STAMP}.tar.gz.enc"
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt -in "${TMP_DIR}/bundle.tar.gz" -out "${OUT_FILE}" -pass env:BACKUP_ENC_KEY
ln -sfn "$(basename "${OUT_FILE}")" "${BACKUP_DIR}/latest.tar.gz.enc" 2>/dev/null || cp "${OUT_FILE}" "${BACKUP_DIR}/latest.tar.gz.enc"
chmod 600 "${OUT_FILE}"

echo "Encrypted backup created: ${OUT_FILE}"
