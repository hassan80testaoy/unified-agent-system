#!/bin/sh
# Runs inside the `backup` Docker container for daily encrypted pg_dump backups.
set -eu
mkdir -p /backups
while true; do
  STAMP="$(date -u +%Y%m%d_%H%M%S)"
  OUT="/backups/pg_${STAMP}.sql.gz.enc"
  pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" | gzip -c | openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt -out "${OUT}" -pass env:BACKUP_ENC_KEY
  cp "${OUT}" /backups/latest.sql.gz.enc
  # Keep last 14 daily encrypted backups
  ls -1t /backups/pg_*.sql.gz.enc 2>/dev/null | tail -n +15 | xargs -r rm -f
  sleep 86400
done
