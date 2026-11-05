#!/bin/bash
# One-time setup of the live copy on this Mac (decision 77). Safe to run again: each step
# skips what is already there. Usage:
#   infra/live/install.sh https://<mac>.<tailnet>.ts.net [/Volumes/<disk>/clearbook-backup]
set -euo pipefail
source "$(dirname "$0")/common.sh"

PUBLIC_URL="${1:-}"
BACKUP_REPO="${2:-}"
[[ "$PUBLIC_URL" =~ ^https://[^/]+$ ]] || fail "usage: install.sh https://<address> [backup folder]"

say "Homebrew packages: Postgres 16, Redis, restic"
for formula in postgresql@16 redis restic; do
    "$BREW" list --formula "$formula" >/dev/null 2>&1 || "$BREW" install "$formula"
done

say "Postgres on port $LIVE_PG_PORT (development keeps 5432)"
PG_CONF=/opt/homebrew/var/postgresql@16/postgresql.conf
[[ -f "$PG_CONF" ]] || fail "$PG_CONF missing: brew reinstall postgresql@16"
if ! grep -qE "^port = $LIVE_PG_PORT" "$PG_CONF"; then
    sed -i '' -E "s/^#?port = [0-9]+/port = $LIVE_PG_PORT/" "$PG_CONF"
    "$BREW" services restart postgresql@16
else
    "$BREW" services start postgresql@16 >/dev/null 2>&1 || true
fi
for _ in $(seq 30); do "$PG_BIN/pg_isready" -q -h localhost -p "$LIVE_PG_PORT" && break; sleep 1; done
"$PG_BIN/pg_isready" -q -h localhost -p "$LIVE_PG_PORT" || fail "Postgres did not start"

say "Redis on port $LIVE_REDIS_PORT, localhost only, kept on disk"
REDIS_CONF=/opt/homebrew/etc/redis.conf
sed -i '' -E "s/^port [0-9]+/port $LIVE_REDIS_PORT/; s/^appendonly no/appendonly yes/" "$REDIS_CONF"
grep -qE "^bind 127\.0\.0\.1" "$REDIS_CONF" || fail "$REDIS_CONF: expected 'bind 127.0.0.1'"
"$BREW" services restart redis

say "Live copy in $LIVE_DIR"
[[ -d "$LIVE_DIR/.git" ]] || git clone --quiet "$DEV_REPO" "$LIVE_DIR"
mkdir -p "$LIVE_DIR/logs" "$LIVE_DIR/backups" "$LIVE_DIR/inbox" "$LIVE_DIR/data"
[[ -d "$LIVE_DIR/data/models" ]] || cp -R "$DEV_REPO/data/models" "$LIVE_DIR/data/models"

if [[ ! -f "$LIVE_DIR/.env" ]]; then
    say "Live .env with new secrets"
    umask 077
    {
        echo "# Live settings (infra/live/install.sh). Never commit. Secrets: keep a copy in the password manager."
        echo "APP_ENV=prod"
        echo "LOG_LEVEL=INFO"
        echo "DEFAULT_CLIENT_ID=missan"
        echo "POSTGRES_USER=$LIVE_DB_USER"
        echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
        echo "POSTGRES_DB=$LIVE_DB"
        echo "POSTGRES_HOST=localhost"
        echo "POSTGRES_PORT=$LIVE_PG_PORT"
        echo "REDIS_URL=redis://localhost:$LIVE_REDIS_PORT/0"
        echo "STORE_DIR=data/store"
        echo "OCR_MODELS_DIR=data/models/paddlex"
        echo "# Runs may only use this folder: never the home folder of this Mac."
        echo "FOLDER_PLACES='[\"$LIVE_DIR/inbox\"]'"
        echo "WEB_DIST_DIR=workbench/dist"
        echo "WORKBENCH_URL=$PUBLIC_URL"
        echo "XERO_REDIRECT_URI=$PUBLIC_URL/api/xero/callback"
        echo "ZOHO_REDIRECT_URI=$PUBLIC_URL/api/zoho/callback"
        echo "SHAREPOINT_REDIRECT_URI=$PUBLIC_URL/api/sharepoint/callback"
        echo "TOKEN_KEY=$(openssl rand -base64 32 | tr '+/' '-_')"
        echo "JWT_SECRET=$(openssl rand -base64 48 | tr -d '\n')"
        echo "COOKIE_SECURE=true"
        echo "# From the development .env: the model, the accounting system apps and SharePoint"
        grep -E '^(LLM_|EXTRACT_MAX_IMAGES|OCR_DETECTION_MODEL|OCR_RECOGNITION_MODEL|XERO_CLIENT_|ZOHO_CLIENT_|SHAREPOINT_(TENANT_ID|CLIENT_))' \
            "$DEV_REPO/.env" || true
    } > "$LIVE_DIR/.env"
    umask 022
fi

say "Database $LIVE_DB"
PG_PASSWORD="$(env_value POSTGRES_PASSWORD)"
psql_admin() { "$PG_BIN/psql" -h localhost -p "$LIVE_PG_PORT" -d postgres -v ON_ERROR_STOP=1 -qtAc "$1"; }
if [[ -z "$(psql_admin "SELECT 1 FROM pg_roles WHERE rolname = '$LIVE_DB_USER'")" ]]; then
    psql_admin "CREATE ROLE $LIVE_DB_USER LOGIN PASSWORD '$PG_PASSWORD'"
fi
if [[ -z "$(psql_admin "SELECT 1 FROM pg_database WHERE datname = '$LIVE_DB'")" ]]; then
    psql_admin "CREATE DATABASE $LIVE_DB OWNER $LIVE_DB_USER"
fi
"$PG_BIN/psql" -h localhost -p "$LIVE_PG_PORT" -d "$LIVE_DB" -qc "CREATE EXTENSION IF NOT EXISTS pg_trgm"

say "Backup key"
if [[ ! -f "$LIVE_DIR/.restic-password" ]]; then
    (umask 077 && openssl rand -base64 32 > "$LIVE_DIR/.restic-password")
    echo "    New: $LIVE_DIR/.restic-password. Save it in the password manager:"
    echo "    without it the backups cannot be opened."
fi
[[ -n "$BACKUP_REPO" ]] && echo "$BACKUP_REPO" > "$LIVE_DIR/.backup-repo"

say "Services that start at login and restart if they stop"
mkdir -p "$AGENTS_DIR"
# plist <name> <keep alive: true|false> <extra keys> <program and arguments...>
plist() {
    local name="$1" keep="$2" extra="$3" args="" arg
    shift 3
    for arg in "$@"; do args="$args        <string>$arg</string>
"; done
    cat > "$AGENTS_DIR/$LABEL.$name.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL.$name</string>
    <key>ProgramArguments</key>
    <array>
$args    </array>
    <key>WorkingDirectory</key><string>$LIVE_DIR/backend</string>
    <key>EnvironmentVariables</key>
    <dict><key>PATH</key><string>$PATH</string></dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><$keep/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>StandardOutPath</key><string>$LIVE_DIR/logs/$name.log</string>
    <key>StandardErrorPath</key><string>$LIVE_DIR/logs/$name.log</string>
$extra</dict>
</plist>
EOF
}
UV_RUN="$UV run --no-sync --directory $LIVE_DIR/backend"
# shellcheck disable=SC2086  # UV_RUN is split into words on purpose
plist api true "" $UV_RUN uvicorn app.main:create_app --factory \
    --host 127.0.0.1 --port "$LIVE_API_PORT" --proxy-headers
# Reading and the AI in one worker, sending in another: a bill never waits behind a long scan.
# Solo pool: prefork fails on macOS (see app/workers/celery_app.py).
# shellcheck disable=SC2086
plist worker-reading true "" $UV_RUN celery -A app.workers.celery_app worker \
    -Q pipeline,extract --pool=solo -n reading@%h --loglevel=INFO
# shellcheck disable=SC2086
plist worker-posting true "" $UV_RUN celery -A app.workers.celery_app worker \
    -Q posting --pool=solo -n posting@%h --loglevel=INFO
plist llm false "" /bin/bash "$LIVE_DIR/infra/live/llm.sh"
plist backup false "    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>30</integer></dict>
" /bin/bash "$LIVE_DIR/infra/live/backup.sh"
# The backup runs at 02:30 only, not at every login.
sed -i '' 's|<key>RunAtLoad</key><true/>|<key>RunAtLoad</key><false/>|' "$AGENTS_DIR/$LABEL.backup.plist"

say "First deploy"
"$DEV_REPO/infra/live/deploy.sh"

for name in llm backup; do
    launchctl bootout "gui/$(id -u)/$LABEL.$name" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$AGENTS_DIR/$LABEL.$name.plist"
done

cat <<EOF

Installed. Next (docs/deploy.md):
  1. A user:     cd $LIVE_DIR && make add-user EMAIL=you@missan.ae NAME="Your Name" ROLE=admin
  2. The link:   tailscale funnel --bg $LIVE_API_PORT
  3. Xero app:   add $PUBLIC_URL/api/xero/callback
  4. Check:      make live-status
EOF
