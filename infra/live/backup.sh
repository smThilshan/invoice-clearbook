#!/bin/bash
# Nightly at 02:30 (launchd), or `make live-backup`: the live database and invoice files, encrypted
# (restic), to the backup disk; 14 daily and 8 weekly copies kept. Also turns over large logs and
# removes uploaded folders older than 30 days (decision 78: their invoices are in the store).
set -euo pipefail
source "$(dirname "$0")/common.sh"

REPO="$(cat "$LIVE_DIR/.backup-repo" 2>/dev/null || true)"
[[ -n "$REPO" ]] || fail "no backup folder: echo /Volumes/<disk>/clearbook-backup > $LIVE_DIR/.backup-repo"
[[ -d "$(dirname "$REPO")" ]] || fail "backup disk not connected: $(dirname "$REPO")"
export RESTIC_REPOSITORY="$REPO" RESTIC_PASSWORD_FILE="$LIVE_DIR/.restic-password"
cd "$LIVE_DIR"

echo "$(date '+%F %T') backup start"
PGPASSWORD="$(env_value POSTGRES_PASSWORD)" "$PG_BIN/pg_dump" -h localhost -p "$LIVE_PG_PORT" \
    -U "$LIVE_DB_USER" -Fc -f backups/$LIVE_DB.dump "$LIVE_DB"
"$RESTIC" cat config >/dev/null 2>&1 || "$RESTIC" init
"$RESTIC" backup --quiet --tag nightly "backups/$LIVE_DB.dump" data/store
"$RESTIC" forget --quiet --keep-daily 14 --keep-weekly 8 --prune
date '+%F %T' > backups/last-ok
echo "$(date '+%F %T') backup done"

# Logs over 20 MB: kept as .1.gz to .5.gz, then emptied in place (the services keep writing).
for log in logs/*.log; do
    [[ -f "$log" && $(stat -f %z "$log") -gt 20000000 ]] || continue
    for n in 4 3 2 1; do
        if [[ -f "$log.$n.gz" ]]; then mv "$log.$n.gz" "$log.$((n + 1)).gz"; fi
    done
    gzip -c "$log" > "$log.1.gz" && : > "$log"
done

# Uploaded folders (decision 78): each invoice was copied into the store when its run took it in.
if [[ -d data/uploads ]]; then
    find data/uploads -mindepth 2 -maxdepth 2 -type d -mtime +30 -exec rm -rf {} +
fi
