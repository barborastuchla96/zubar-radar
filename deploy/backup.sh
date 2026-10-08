#!/usr/bin/env bash
# Daily database dump, kept for 14 days. Copy backups/ off the server too
# (e.g. Hetzner Storage Box via rsync, or rclone to any S3 bucket).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ../backups
OUT="../backups/radar-$(date +%F).dump"
docker compose exec -T db pg_dump -U radar -d radar -Fc > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
find ../backups -name 'radar-*.dump' -mtime +14 -delete
echo "$(date -Is) backup ok: $OUT ($(du -h "$OUT" | cut -f1))"
# Restore: docker compose exec -T db pg_restore -U radar -d radar --clean < backups/radar-YYYY-MM-DD.dump
