#!/usr/bin/env bash
# Monthly NRPZS refresh (the register updates on the 1st of each month).
set -euo pipefail
cd "$(dirname "$0")"
URL="$(grep -E '^NRPZS_URL=' .env | cut -d= -f2- || true)"
if [ -z "$URL" ]; then
  echo "$(date -Is) NRPZS_URL not set in deploy/.env; skipping"; exit 0
fi
echo "$(date -Is) importing $URL"
docker compose run --rm importer import "$URL"
