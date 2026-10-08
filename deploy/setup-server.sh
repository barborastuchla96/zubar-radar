#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server. Run as root from the repo:
#   sudo ./deploy/setup-server.sh
set -euo pipefail
cd "$(dirname "$0")"
REPO_DIR="$(cd .. && pwd)"

echo "==> System updates, Docker, firewall"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq docker.io docker-compose-v2 ufw fail2ban unattended-upgrades openssl
systemctl enable --now docker
dpkg-reconfigure -f noninteractive unattended-upgrades

# Only SSH and web traffic in. (Postgres is never published to the host.)
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "==> Secrets"
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
  sed -i "s/^REPORT_SALT=.*/REPORT_SALT=$(openssl rand -hex 32)/" .env
  chmod 600 .env
  echo "    Created deploy/.env. Now set DOMAIN, ACME_EMAIL and NRPZS_URL in it."
else
  echo "    deploy/.env exists, leaving it alone."
fi

echo "==> Cron: daily backup 03:17, monthly import on the 2nd at 04:23"
mkdir -p "$REPO_DIR/backups" "$REPO_DIR/data" /var/log/berepacienty
cat > /etc/cron.d/berepacienty <<CRON
17 3 * * * root $REPO_DIR/deploy/backup.sh >> /var/log/berepacienty/backup.log 2>&1
23 4 2 * * root $REPO_DIR/deploy/import-monthly.sh >> /var/log/berepacienty/import.log 2>&1
CRON

echo
echo "Done. Next:"
echo "  1. nano deploy/.env          # DOMAIN, ACME_EMAIL, NRPZS_URL"
echo "  2. ./deploy/deploy.sh        # build + start"
echo "  3. copy the NRPZS CSV to data/nrpzs.csv, then:"
echo "     docker compose -f deploy/docker-compose.yml run --rm importer import /data/nrpzs.csv"
