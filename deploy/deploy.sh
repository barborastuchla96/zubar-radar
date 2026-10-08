#!/usr/bin/env bash
# Build and (re)start the site. Safe to re-run after every `git pull`.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "deploy/.env missing; run setup-server.sh first"; exit 1; }
grep -q '^DOMAIN=example.cz' .env && { echo "Set DOMAIN in deploy/.env first"; exit 1; }

docker compose --profile tools build   # includes the importer image
docker compose up -d db
docker compose run --rm importer initdb          # schema is idempotent
docker compose up -d --remove-orphans
docker image prune -f >/dev/null
docker compose ps
