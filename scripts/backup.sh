#!/bin/sh
set -e

DB=/data/omarket.db
BACKUPS=/backups
RETENTION_DAYS=30
TS=$(date +%Y%m%d-%H%M%S)

mkdir -p "$BACKUPS"

if [ ! -f "$DB" ]; then
  echo "[$(date -Iseconds)] backup: db not found at $DB, skipping"
  exit 0
fi

OUT="$BACKUPS/omarket-$TS.db"
# Use SQLite's online backup API — safe while app writes
sqlite3 "$DB" ".backup '$OUT'"
gzip -9 "$OUT"

# Retention: remove backups older than N days
find "$BACKUPS" -name 'omarket-*.db.gz' -mtime +$RETENTION_DAYS -delete

SIZE=$(du -h "$OUT.gz" | cut -f1)
COUNT=$(ls -1 "$BACKUPS"/omarket-*.db.gz 2>/dev/null | wc -l)
echo "[$(date -Iseconds)] backup: $OUT.gz ($SIZE) · total archives: $COUNT"
