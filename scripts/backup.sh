#!/bin/sh
# Safe SQLite backup: uses sqlite3 .backup, never copies the live .db file.
set -eu

DATA_DIR="${DATA_DIR:-/app/data}"
DB_FILE="${DB_FILE:-$DATA_DIR/tracker.db}"
BACKUP_DIR="${BACKUP_DIR:-$DATA_DIR/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

if [ ! -f "$DB_FILE" ]; then
    echo "Database not found: $DB_FILE" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
DEST="$BACKUP_DIR/tracker_$STAMP.db"

sqlite3 "$DB_FILE" ".backup '$DEST'"
gzip -f "$DEST"
find "$BACKUP_DIR" -type f -name 'tracker_*.db.gz' -mtime "+$RETENTION_DAYS" -delete

echo "Backup created: $DEST.gz"
