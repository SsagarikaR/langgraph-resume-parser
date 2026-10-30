#!/usr/bin/env bash
# Backs up checkpoints.db from the running api container, encrypted unless plaintext is allowed.
set -euo pipefail
cd "$(dirname "$0")/.."

dest=${1:?Usage: scripts/backup.sh <dest-dir>}
keep_days=${KEEP_DAYS:-30}
mkdir -p "$dest"
chmod 700 "$dest"

if [ -z "${BACKUP_AGE_RECIPIENT:-}" ] && [ "${BACKUP_ALLOW_PLAINTEXT:-}" != "true" ]; then
  echo "Set BACKUP_AGE_RECIPIENT=age1... (or BACKUP_ALLOW_PLAINTEXT=true for a database with no real data)" >&2
  exit 1
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

docker compose exec -T api python -c "
import sqlite3, sys, os
src = sqlite3.connect('/var/lib/screener/checkpoints.db')
dst = sqlite3.connect('/tmp/backup.db')
src.backup(dst)
dst.close()
src.close()
with open('/tmp/backup.db', 'rb') as f:
    sys.stdout.buffer.write(f.read())
os.remove('/tmp/backup.db')
" > "$tmp/checkpoints-$timestamp.db"

if [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
  age -r "$BACKUP_AGE_RECIPIENT" -o "$dest/checkpoints-$timestamp.db.age" "$tmp/checkpoints-$timestamp.db"
  chmod 600 "$dest/checkpoints-$timestamp.db.age"
  echo "Wrote $dest/checkpoints-$timestamp.db.age"
else
  cp "$tmp/checkpoints-$timestamp.db" "$dest/checkpoints-$timestamp.db"
  chmod 600 "$dest/checkpoints-$timestamp.db"
  echo "Wrote $dest/checkpoints-$timestamp.db (plaintext)"
fi

if [ "$keep_days" -gt 0 ]; then
  find "$dest" -maxdepth 1 -name 'checkpoints-*' -mtime "+$keep_days" -print -delete
fi
