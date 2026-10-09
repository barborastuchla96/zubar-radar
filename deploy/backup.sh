#!/usr/bin/env bash
# Daily database dump, kept for 14 days, then mirrored off the server when
# BACKUP_REMOTE is set in .env (set up with ./setup-offsite-backup.sh).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ../backups
OUT="../backups/radar-$(date +%F).dump"
docker compose exec -T db pg_dump -U radar -d radar -Fc > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
find ../backups -name 'radar-*.dump' -mtime +14 -delete
echo "$(date -Is) backup ok: $OUT ($(du -h "$OUT" | cut -f1))"

# Off-site copy: a server that dies takes its own backups with it.
REMOTE="$(grep -E '^BACKUP_REMOTE=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' || true)"
if [ -n "$REMOTE" ]; then
  PORT="$(grep -E '^BACKUP_REMOTE_PORT=' .env | cut -d= -f2- | tr -d '"' || true)"
  rsync -a --delete -e "ssh -p ${PORT:-23} -i /root/.ssh/backup_box -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
    ../backups/ "$REMOTE:prijimanovepacienty-backups/"
  echo "$(date -Is) off-site copy ok: $REMOTE"
fi
# Restore: docker compose exec -T db pg_restore -U radar -d radar --clean < backups/radar-YYYY-MM-DD.dump
