#!/bin/sh
set -e

# Run once on container start to verify configuration
/scripts/backup.sh || echo "[$(date -Iseconds)] initial backup failed (db may not exist yet)"

# Install cron job: every day at 03:30 Asia/Almaty
echo "30 3 * * * /scripts/backup.sh >> /var/log/backup.log 2>&1" | crontab -

# Prepare log
touch /var/log/backup.log

# Run crond in foreground
crond -f -l 8 &
CRON_PID=$!

# Tail backup log so it shows in `docker compose logs`
tail -F /var/log/backup.log &

wait $CRON_PID
