#!/bin/bash
# `make live-status`: is the live copy running, and when was it last backed up.
set -uo pipefail
source "$(dirname "$0")/common.sh"

say "Services"
for name in $SERVICES llm backup; do
    state="$(launchctl print "gui/$(id -u)/$LABEL.$name" 2>/dev/null | awk '/^\tstate =/ {print $3}')"
    printf '  %-15s %s\n' "$name" "${state:-not installed}"
done

say "Health"
curl -sS --max-time 10 "http://127.0.0.1:$LIVE_API_PORT/api/health/ready" || echo "  API not answering"
echo

say "Live version"
git -C "$LIVE_DIR" log -1 --format='  %h %s (%cr)' 2>/dev/null || echo "  not installed"

say "Last backup"
echo "  $(cat "$LIVE_DIR/backups/last-ok" 2>/dev/null || echo never)"

say "Disk"
df -h "$LIVE_DIR" | awk 'NR == 2 {print "  " $4 " free of " $2}'
