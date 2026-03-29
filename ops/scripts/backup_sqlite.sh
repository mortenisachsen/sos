#!/usr/bin/env sh
set -eu

DB_PATH="${1:-}"
BACKUP_DIR="${2:-}"

if [ -z "$DB_PATH" ] || [ -z "$BACKUP_DIR" ]; then
  echo "Usage: $0 /path/to/tomwood.db /path/to/backup-dir"
  exit 1
fi

if [ ! -f "$DB_PATH" ]; then
  echo "Database not found: $DB_PATH"
  exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "sqlite3 command is required for safe backups"
  exit 1
fi

STAMP="$(date +%Y-%m-%d_%H-%M-%S)"
mkdir -p "$BACKUP_DIR"

BACKUP_FILE="$BACKUP_DIR/tomwood_$STAMP.db"
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

gzip "$BACKUP_FILE"
find "$BACKUP_DIR" -type f -name 'tomwood_*.db.gz' -mtime +30 -delete

echo "Backup written to $BACKUP_FILE.gz"
