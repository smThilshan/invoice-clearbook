#!/bin/bash
# Prove a backup opens: `make live-restore-test` restores the latest one into a spare database
# (never the live one) and a temporary folder, and counts what came back. The real restore,
# after a loss, is in docs/deploy.md.
set -euo pipefail
source "$(dirname "$0")/common.sh"

SNAPSHOT="${1:-latest}"
TARGET="${2:-clearbook_restore_test}"
[[ "$TARGET" != "$LIVE_DB" ]] || fail "this test never restores over the live database"
export RESTIC_REPOSITORY="$(cat "$LIVE_DIR/.backup-repo")" RESTIC_PASSWORD_FILE="$LIVE_DIR/.restic-password"

FOLDER="$(mktemp -d)"
say "Backup $SNAPSHOT into $FOLDER"
"$RESTIC" restore --quiet "$SNAPSHOT" --target "$FOLDER"

say "Database into $TARGET"
"$PG_BIN/dropdb" -h localhost -p "$LIVE_PG_PORT" --if-exists "$TARGET"
"$PG_BIN/createdb" -h localhost -p "$LIVE_PG_PORT" "$TARGET"
"$PG_BIN/pg_restore" -h localhost -p "$LIVE_PG_PORT" --no-owner -d "$TARGET" \
    "$FOLDER/backups/$LIVE_DB.dump"

count() { "$PG_BIN/psql" -h localhost -p "$LIVE_PG_PORT" -d "$1" -qtAc "SELECT count(*) FROM documents"; }
echo "Documents: live $(count "$LIVE_DB"), restored $(count "$TARGET")"
echo "Files restored: $(find "$FOLDER/data/store" -type f 2>/dev/null | wc -l | tr -d ' ')"
echo "Done. Remove the test copies: dropdb -h localhost -p $LIVE_PG_PORT $TARGET; rm -rf $FOLDER"
