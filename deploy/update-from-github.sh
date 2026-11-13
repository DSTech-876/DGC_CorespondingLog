#!/usr/bin/env bash
# Incremental update of the deployed app from GitHub.
# Copies ONLY the files added or modified between the deployed commit and the
# new commit. It never deletes anything and never re-copies the whole tree.
set -euo pipefail

APP_USER="${APP_USER:-correspondence}"
BASE_DIR="${BASE_DIR:-/opt/correspondence-cms}"
SRC_DIR="${SRC_DIR:-$BASE_DIR/src}"          # git clone used only to fetch changes
APP_DIR="${APP_DIR:-$BASE_DIR/app}"          # live application directory
VENV_DIR="${VENV_DIR:-$BASE_DIR/venv}"
ENV_FILE="${ENV_FILE:-/etc/correspondence-cms.env}"
BRANCH="${BRANCH:-main}"
SERVICE="${SERVICE:-correspondence}"
STATE_FILE="$BASE_DIR/.deployed_commit"

log() { printf '[update] %s\n' "$*"; }

[ -d "$SRC_DIR/.git" ] || { echo "No git clone at $SRC_DIR" >&2; exit 1; }
[ -d "$APP_DIR" ] || { echo "No live app directory at $APP_DIR (run the initial install first)" >&2; exit 1; }

cd "$SRC_DIR"
git fetch --prune origin "$BRANCH"

NEW_COMMIT="$(git rev-parse "origin/$BRANCH")"
OLD_COMMIT="$(cat "$STATE_FILE" 2>/dev/null || true)"

if [ -z "$OLD_COMMIT" ]; then
  cat >&2 <<EOF
No deployed commit recorded in $STATE_FILE.
This script only copies incremental changes. After the initial install, record
the installed commit once with:
  git -C "$SRC_DIR" rev-parse HEAD | sudo tee "$STATE_FILE"
EOF
  exit 1
fi

if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
  log "Already at $NEW_COMMIT. Nothing to do."
  exit 0
fi

git cat-file -e "${OLD_COMMIT}^{commit}" 2>/dev/null || {
  echo "Recorded commit $OLD_COMMIT is unknown to this clone." >&2; exit 1; }

git checkout -q "$BRANCH"
git merge --ff-only -q "origin/$BRANCH"

CHANGED_LIST="$(mktemp)"
trap 'rm -f "$CHANGED_LIST"' EXIT

# Added / copied / modified / renamed(new path) / type-changed files only.
git diff --name-only --diff-filter=ACMRT "$OLD_COMMIT" "$NEW_COMMIT" > "$CHANGED_LIST"

if [ ! -s "$CHANGED_LIST" ]; then
  log "No file changes between $OLD_COMMIT and $NEW_COMMIT."
  printf '%s\n' "$NEW_COMMIT" > "$STATE_FILE"
  exit 0
fi

log "Copying $(wc -l < "$CHANGED_LIST") updated file(s):"
sed 's/^/  /' "$CHANGED_LIST"

# --checksum: git checkouts rewrite mtimes, so compare content, not timestamps.
rsync -a --checksum --files-from="$CHANGED_LIST" "$SRC_DIR/" "$APP_DIR/"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

# Upstream deletions are reported but left in place for manual review.
DELETED="$(git diff --name-only --diff-filter=D "$OLD_COMMIT" "$NEW_COMMIT")"
if [ -n "$DELETED" ]; then
  log "Files deleted upstream (left untouched on the server, remove manually if intended):"
  printf '%s\n' "$DELETED" | sed 's/^/  /'
fi

NEEDS_DEPS=0; NEEDS_MIGRATE=0; NEEDS_STATIC=0
grep -qx 'requirements.txt' "$CHANGED_LIST" && NEEDS_DEPS=1
grep -qE '(^|/)(migrations/.*\.py|models\.py)$' "$CHANGED_LIST" && NEEDS_MIGRATE=1
grep -qE '^(templates|registry/static|static)/' "$CHANGED_LIST" && NEEDS_STATIC=1
grep -qx 'config/settings.py' "$CHANGED_LIST" && { NEEDS_MIGRATE=1; NEEDS_STATIC=1; }

run_manage() {
  ( set -a; . "$ENV_FILE"; set +a
    cd "$APP_DIR"
    sudo -u "$APP_USER" --preserve-env "$VENV_DIR/bin/python" manage.py "$@" )
}

if [ "$NEEDS_DEPS" = 1 ]; then
  log "requirements.txt changed - installing dependencies"
  "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"
fi
if [ "$NEEDS_MIGRATE" = 1 ]; then
  log "Applying database migrations"
  run_manage migrate --noinput
fi
if [ "$NEEDS_STATIC" = 1 ]; then
  log "Collecting static files"
  run_manage collectstatic --noinput
fi

log "Restarting $SERVICE"
systemctl restart "$SERVICE"
systemctl is-active --quiet "$SERVICE" || { echo "Service failed to start" >&2; exit 1; }

printf '%s\n' "$NEW_COMMIT" > "$STATE_FILE"
log "Updated $OLD_COMMIT -> $NEW_COMMIT"
