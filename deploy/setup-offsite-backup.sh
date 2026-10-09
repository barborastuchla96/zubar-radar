#!/usr/bin/env bash
# One-time setup of off-site backups to a Hetzner Storage Box (or any SSH/rsync host).
# Usage:  ./setup-offsite-backup.sh u123456@u123456.your-storagebox.de
# Creates a key used ONLY for backups (/root/.ssh/backup_box; never touches other keys),
# installs it on the box (asks for the box password once), saves the target in .env
# and runs a first backup.
set -euo pipefail
cd "$(dirname "$0")"
TARGET="${1:?usage: $0 user@host}"
PORT="${2:-23}"                                   # Hetzner Storage Box: SSH on port 23
KEY=/root/.ssh/backup_box

command -v rsync >/dev/null || apt-get install -y rsync
mkdir -p /root/.ssh && chmod 700 /root/.ssh
if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -N '' -C "backup@$(hostname)" -f "$KEY" >/dev/null
  echo "created backup key $KEY"
fi
echo "Installing the key on $TARGET. Enter the Storage Box password when asked:"
if ! ssh -p "$PORT" -o StrictHostKeyChecking=accept-new "$TARGET" install-ssh-key < "$KEY.pub"; then
  echo "(install-ssh-key not available there; trying ssh-copy-id)"
  ssh-copy-id -p "$PORT" -i "$KEY.pub" "$TARGET"
fi

sed -i '/^BACKUP_REMOTE=/d; /^BACKUP_REMOTE_PORT=/d' .env
printf 'BACKUP_REMOTE=%s\nBACKUP_REMOTE_PORT=%s\n' "$TARGET" "$PORT" >> .env
echo "saved BACKUP_REMOTE in .env; running a first backup…"
./backup.sh
