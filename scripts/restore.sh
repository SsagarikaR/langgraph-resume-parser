#!/usr/bin/env bash
# Restores checkpoints.db into the running api container, refusing a backup that isn't valid SQLite.
set -euo pipefail
cd "$(dirname "$0")/.."

backup=${1:?Usage: scripts/restore.sh <backup-file>}
[ -f "$backup" ] || { echo "No such file: $backup" >&2; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
plain="$tmp/checkpoints.db"

case "$backup" in
  *.age)
    : "${AGE_IDENTITY:?Set AGE_IDENTITY to the path of the age private key}"
    age -d -i "$AGE_IDENTITY" -o "$plain" "$backup"
    ;;
  *)
    cp "$backup" "$plain"
    ;;
esac

header=$(head -c 16 "$plain" | tr -d '\0')
case "$header" in
  "SQLite format 3"*) ;;
  *) echo "Refusing to restore $backup: not a SQLite database" >&2; exit 1 ;;
esac

docker compose cp "$plain" api:/tmp/restore.db
docker compose exec -T api python -c "
import shutil, os
shutil.copyfile('/tmp/restore.db', '/var/lib/screener/checkpoints.db')
os.chmod('/var/lib/screener/checkpoints.db', 0o600)
os.remove('/tmp/restore.db')
for wal in ('/var/lib/screener/checkpoints.db-wal', '/var/lib/screener/checkpoints.db-shm'):
    if os.path.exists(wal):
        os.remove(wal)
"
docker compose restart api

for _ in $(seq 30); do
  docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/ready', timeout=5)" 2>/dev/null && break
  sleep 2
done

echo "Restored $backup"
