#!/bin/bash
# Put committed code live (decision 77): `make deploy` (latest main) or `make deploy REF=<commit>`
# (any earlier commit, to go back). Uncommitted work in the development folder never goes live.
set -euo pipefail
source "$(dirname "$0")/common.sh"

REF="${1:-origin/main}"
[[ -f "$LIVE_DIR/.env" ]] || fail "no live copy yet: run infra/live/install.sh first"
cd "$LIVE_DIR"

PREVIOUS="$(git rev-parse --short HEAD)"
say "Code: $REF"
git fetch --quiet origin
git checkout --quiet --detach "$REF"
NOW="$(git rev-parse --short HEAD)"
if [[ "$PREVIOUS" != "$NOW" ]] && ! git diff --quiet "$PREVIOUS" "$NOW" -- backend/migrations; then
    echo "    The database changes between $PREVIOUS and $NOW: going back to $PREVIOUS later"
    echo "    also needs 'alembic downgrade' (docs/deploy.md)."
fi

say "Packages"
"$UV" sync --quiet --frozen --no-dev --directory backend
"$NPM" ci --silent --prefix workbench

say "Workbench"
"$NPM" run --silent --prefix workbench build >/dev/null

say "Database"
backend alembic upgrade head

say "Restart"
for name in $SERVICES; do
    if launchctl print "gui/$(id -u)/$LABEL.$name" >/dev/null 2>&1; then
        launchctl kickstart -k "gui/$(id -u)/$LABEL.$name"
    else
        launchctl bootstrap "gui/$(id -u)" "$AGENTS_DIR/$LABEL.$name.plist"
    fi
done

say "Health"
READY="http://127.0.0.1:$LIVE_API_PORT/api/health/ready"
for _ in $(seq 60); do curl -fsS "$READY" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "$READY" >/dev/null 2>&1 || {
    curl -sS "$READY" || true
    fail "not ready after 60 s: see $LIVE_DIR/logs/api.log. Back to before: make deploy REF=$PREVIOUS"
}
backend python scripts/pipeline_admin.py resume >/dev/null  # files left between steps by the restart

echo "Live: $NOW (was $PREVIOUS). Back to before: make deploy REF=$PREVIOUS"
