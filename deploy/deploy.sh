#!/usr/bin/env bash
# Build and (re)start the site. Safe to re-run after every `git pull`.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "deploy/.env missing; run setup-server.sh first"; exit 1; }
grep -q '^DOMAIN=example.cz' .env && { echo "Set DOMAIN in deploy/.env first"; exit 1; }

# Redirect old domains (REDIRECT_FROM in .env) to DOMAIN.
mkdir -p caddy.d
REDIRECT_FROM="$(grep -E '^REDIRECT_FROM=' .env | cut -d= -f2- | tr -d '"' || true)"
if [ -n "$REDIRECT_FROM" ]; then
  addrs="$(for d in $REDIRECT_FROM; do printf '%s, www.%s, ' "$d" "$d"; done)"
  printf '%s {\n\tredir https://{$DOMAIN}{uri} permanent\n}\n' "${addrs%, }" > caddy.d/redirects.caddy
  echo "redirecting to DOMAIN from: $REDIRECT_FROM"
else
  rm -f caddy.d/redirects.caddy
fi

docker compose --profile tools build   # includes the importer image
docker compose up -d db
docker compose run --rm importer initdb          # schema is idempotent
docker compose up -d --remove-orphans
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile   # pick up domain/redirect changes
docker image prune -f >/dev/null
docker compose ps
