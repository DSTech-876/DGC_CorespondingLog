#!/usr/bin/env bash
# Pull updated files from GitHub into this checkout, then (optionally) push the
# changes out to the deployed app.
#
# Usage:
#   ./update.sh                 # update this checkout only
#   sudo ./update.sh --deploy   # also run deploy/update-from-github.sh
#
# Overridable env vars: BRANCH, REMOTE, REPO_DIR, BASE_DIR
set -euo pipefail

REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
BASE_DIR="${BASE_DIR:-/opt/correspondence-cms}"
DEPLOY=0
FORCE="${FORCE:-0}"

log() { printf '[update] %s\n' "$*"; }
die() { printf '[update] ERROR: %s\n' "$*" >&2; exit 1; }

# `sudo ./update.sh` runs git as root inside a checkout owned by a regular user,
# which trips git's "dubious ownership" check. Scope the exception to this
# checkout only ("command" scope counts as protected configuration).
git() { command git -c "safe.directory=$REPO_DIR" "$@"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --deploy) DEPLOY=1 ;;
    --force) FORCE=1 ;;
    --branch) shift; BRANCH="${1:-}" ;;
    -h|--help) sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
  shift
done

if [ ! -d "$REPO_DIR/.git" ]; then
  cat >&2 <<EOF
[update] ERROR: $REPO_DIR is not a git clone (no .git directory).
This usually means the code was downloaded as a ZIP. Options:
  1. Replace it with a real clone (recommended):
       git clone https://github.com/DSTech-876/DGC_CorespondingLog.git ~/DGC_CRL
       cd ~/DGC_CRL && sudo ./update.sh --deploy
  2. Update the server straight from GitHub using the clone at $BASE_DIR/src:
       sudo $BASE_DIR/src/deploy/update-from-github.sh
EOF
  exit 1
fi
cd "$REPO_DIR"

REMOTE_URL="$(git remote get-url "$REMOTE" 2>/dev/null || true)"
[ -n "$REMOTE_URL" ] || die "Remote '$REMOTE' is not configured in $REPO_DIR."

# Fail early with actionable guidance instead of git's bare
# "Could not resolve host: github.com".
check_connectivity() {
  local host=""
  case "$REMOTE_URL" in
    file://*|/*|./*|../*) return 0 ;;                      # local path remote
    *://*) host="${REMOTE_URL#*://}"; host="${host#*@}"; host="${host%%/*}"; host="${host%%:*}" ;;
    *:*) host="${REMOTE_URL%%:*}"; host="${host#*@}" ;;     # scp-style: user@host:path
    *) return 0 ;;
  esac
  [ -n "$host" ] || return 0
  if getent hosts "$host" >/dev/null 2>&1; then
    return 0
  fi
  cat >&2 <<EOF
[update] ERROR: cannot resolve '$host' - this machine has no DNS/network route to GitHub.
Check, in order:
  1. Connectivity:   ping -c1 1.1.1.1
  2. DNS resolution: resolvectl query $host   (or: cat /etc/resolv.conf)
  3. Proxy, if your network requires one:
       git config --global http.proxy http://<proxy-host>:<port>
  4. Offline hosts: copy the repo across manually, e.g.
       rsync -a --exclude .git /path/to/DGC_CRL/ $BASE_DIR/app/
EOF
  exit 1
}
check_connectivity

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  if [ "$FORCE" != 1 ]; then
    git --no-pager status --short
    die "Local modifications present. Commit/stash them, or rerun with --force to discard."
  fi
  log "Discarding local modifications (--force)"
fi

log "Fetching $REMOTE/$BRANCH"
git fetch --prune "$REMOTE" "$BRANCH"

OLD_COMMIT="$(git rev-parse HEAD)"
NEW_COMMIT="$(git rev-parse "$REMOTE/$BRANCH")"

# Local commits that are not on the remote branch would be destroyed by the
# hard reset below, so require an explicit --force.
if [ "$OLD_COMMIT" != "$NEW_COMMIT" ] && ! git merge-base --is-ancestor "$OLD_COMMIT" "$NEW_COMMIT"; then
  if [ "$FORCE" != 1 ]; then
    log "Local commits not present on $REMOTE/$BRANCH:"
    git --no-pager log --oneline "$NEW_COMMIT..$OLD_COMMIT" | sed 's/^/  /'
    die "This checkout has diverged. Push/stash those commits, or rerun with --force to discard them."
  fi
  log "Discarding diverged local commits (--force)"
fi

if [ "$OLD_COMMIT" = "$NEW_COMMIT" ] && [ "$FORCE" != 1 ]; then
  log "Already up to date at $NEW_COMMIT."
else
  if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
    log "Re-syncing checkout to $NEW_COMMIT"
  else
    log "Updating $OLD_COMMIT -> $NEW_COMMIT"
  fi
  git checkout -q "$BRANCH" 2>/dev/null || git checkout -q -B "$BRANCH" "$REMOTE/$BRANCH"
  git reset -q --hard "$REMOTE/$BRANCH"
  if [ "$OLD_COMMIT" != "$NEW_COMMIT" ]; then
    log "Changed files:"
    git --no-pager diff --name-status "$OLD_COMMIT" "$NEW_COMMIT" | sed 's/^/  /'
  fi
fi

if [ "$DEPLOY" = 1 ]; then
  [ "$(id -u)" -eq 0 ] || die "--deploy must run as root, e.g.: sudo ./update.sh --deploy"
  [ -d "$BASE_DIR" ] || die "No deployment found at $BASE_DIR (run deploy/install-8086.sh first)."
  log "Running deploy/update-from-github.sh"
  BASE_DIR="$BASE_DIR" BRANCH="$BRANCH" "$REPO_DIR/deploy/update-from-github.sh"
fi

log "Done."
