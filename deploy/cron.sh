#!/usr/bin/env bash
# Install (or update) the scheduled jobs. Safe to re-run. Needs root.
set -euo pipefail
cd "$(dirname "$0")"
REPO_DIR="$(cd .. && pwd)"
mkdir -p /var/log/berepacienty
cat > /etc/cron.d/berepacienty <<CRON
# Times are the server's (UTC): 18:03 UTC = 20:03 in Prague in summer, 19:03 in winter.
# Daily database backup, email alerts and the owner's evening digest, monthly NRPZS import, weekly search for unlisted practice websites + clinic website check.
17 3 * * * root $REPO_DIR/deploy/backup.sh >> /var/log/berepacienty/backup.log 2>&1
23 4 2 * * root $REPO_DIR/deploy/import-monthly.sh >> /var/log/berepacienty/import.log 2>&1
41 7 * * * root cd $REPO_DIR/deploy && grep -q '^SMTP_HOST=.' .env && docker compose run --rm -T importer alerts >> /var/log/berepacienty/alerts.log 2>&1
3 18 * * * root cd $REPO_DIR/deploy && grep -q '^SMTP_HOST=.' .env && docker compose run --rm -T importer digest >> /var/log/berepacienty/digest.log 2>&1
11 5 * * 0 root cd $REPO_DIR/deploy && (docker compose run --rm -T importer discover-web; docker compose run --rm -T importer crawl) >> /var/log/berepacienty/crawl.log 2>&1
CRON
echo "cron jobs installed:"; grep -v '^#' /etc/cron.d/berepacienty
