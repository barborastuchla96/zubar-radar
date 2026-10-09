#!/usr/bin/env bash
# One-time: encrypt database backups (they contain subscribers' e-mail addresses).
# Creates an age key pair, keeps only the PUBLIC key on the server (in .env) and prints the
# PRIVATE key once: save it in your password manager. Without it the backups can't be restored.
set -euo pipefail
cd "$(dirname "$0")"
command -v age >/dev/null || apt-get install -y age
if grep -qE '^BACKUP_AGE_RECIPIENT=' .env 2>/dev/null; then
  echo "Backups are already encrypted (BACKUP_AGE_RECIPIENT is in .env). Nothing to do."; exit 0
fi
TMP="$(mktemp)"; trap 'shred -u "$TMP" 2>/dev/null || rm -f "$TMP"' EXIT
age-keygen > "$TMP" 2>/dev/null   # mktemp file is private (0600)
PUB="$(age-keygen -y "$TMP")"
printf 'BACKUP_AGE_RECIPIENT=%s\n' "$PUB" >> .env
echo
echo "=================== PRIVATE BACKUP KEY – SAVE THIS NOW ==================="
grep '^AGE-SECRET-KEY-' "$TMP"
echo "=========================================================================="
echo "Copy the line above into your password manager (e.g. as 'prijimanovepacienty backup key')."
echo "It is NOT stored on the server. Without it, backups can't be restored."
echo
read -r -p "Saved it? Type yes to continue: " ok
[ "$ok" = yes ] || { sed -i '/^BACKUP_AGE_RECIPIENT=/d' .env; echo "Cancelled, nothing changed."; exit 1; }
echo "Making a first encrypted backup and removing the unencrypted ones…"
./backup.sh
