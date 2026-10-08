#!/usr/bin/env bash
# Install (or update) the scheduled jobs. Safe to re-run. Needs root.
set -euo pipefail
cd "$(dirname "$0")"
REPO_DIR="$(cd .. && pwd)"
mkdir -p /var/log/berepacienty
cat > /etc/cron.d/berepacienty <<CRON
# Daily database backup, monthly NRPZS import, weekly clinic website check (Prague).
17 3 * * * root $REPO_DIR/deploy/backup.sh >> /var/log/berepacienty/backup.log 2>&1
23 4 2 * * root $REPO_DIR/deploy/import-monthly.sh >> /var/log/berepacienty/import.log 2>&1
11 5 * * 0 root cd $REPO_DIR/deploy && docker compose run --rm -T importer crawl >> /var/log/berepacienty/crawl.log 2>&1
CRON
echo "cron jobs installed:"; grep -v '^#' /etc/cron.d/berepacienty
