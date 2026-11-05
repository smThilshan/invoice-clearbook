# Shared by the live scripts (decision 77): where the live copy is and what it runs on.
# Sourced, not run. macOS ships bash 3.2: no bash 4 features here.

LIVE_DIR="${LIVE_DIR:-$HOME/clearbook-live}"
DEV_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Own ports, so the live services never meet the development ones (Docker 5432 / 6379, API 8000).
LIVE_PG_PORT=5433
LIVE_REDIS_PORT=6380
LIVE_API_PORT=8100
LIVE_DB=clearbook_live
LIVE_DB_USER=clearbook

BREW=/opt/homebrew/bin/brew
UV=/opt/homebrew/bin/uv
NPM=/opt/homebrew/bin/npm
LMS="$HOME/.lmstudio/bin/lms"
PG_BIN=/opt/homebrew/opt/postgresql@16/bin
RESTIC=/opt/homebrew/bin/restic

LABEL=com.missan.clearbook
SERVICES="api worker-reading worker-posting"   # kept running by launchd
AGENTS_DIR="$HOME/Library/LaunchAgents"

export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# A value from the live .env (KEY=value; no quotes needed for the keys read here).
# A setting of the live .env; empty when it is not there (callers then use their default).
env_value() { { grep -E "^$1=" "$LIVE_DIR/.env" || true; } | tail -1 | cut -d= -f2-; }

backend() { "$UV" run --no-sync --directory "$LIVE_DIR/backend" "$@"; }
