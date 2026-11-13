#!/usr/bin/env bash
# One-shot, idempotent installer that automates Steps 1-7 of
# UBUNTU_DEPLOY_8086.md (base packages, service account, clone/venv,
# env file, migrate/collectstatic, systemd, nginx). Safe to re-run.
#
# Usage: sudo ./deploy/install-8086.sh
# Overridable env vars: REPO_URL, BRANCH, BASE_DIR, APP_USER, DB_NAME,
#   DB_USER, DB_PASSWORD, ALLOWED_HOSTS, LAN_SUBNET
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/DSTech-876/DGC_CorespondingLog.git}"
BRANCH="${BRANCH:-main}"
BASE_DIR="${BASE_DIR:-/opt/correspondence-cms}"
SRC_DIR="$BASE_DIR/src"
APP_DIR="$BASE_DIR/app"
VENV_DIR="$BASE_DIR/venv"
MEDIA_DIR="/var/lib/correspondence-cms/media"
ENV_FILE="/etc/correspondence-cms.env"
APP_USER="${APP_USER:-correspondence}"
DB_NAME="${DB_NAME:-correspondence_db}"
DB_USER="${DB_USER:-correspondence_user}"
DB_PASSWORD="${DB_PASSWORD:-}"
ALLOWED_HOSTS="${ALLOWED_HOSTS:-}"
LAN_SUBNET="${LAN_SUBNET:-}"

log() { printf '[install] %s\n' "$*"; }

# Hosts the server actually answers on; a missing LAN IP here is the usual
# cause of "Bad Request (400)" when browsing to http://<lan-ip>:8086/.
detect_hosts() {
  local hosts="127.0.0.1,localhost"
  local ip
  for ip in $(hostname -I 2>/dev/null); do
    case "$ip" in *:*) continue ;; esac
    hosts="$hosts,$ip"
  done
  local name
  name="$(hostname -s 2>/dev/null || true)"
  [ -n "$name" ] && hosts="$hosts,$name,$name.local"
  printf '%s' "$hosts"
}

if [ -z "$ALLOWED_HOSTS" ]; then
  ALLOWED_HOSTS="$(detect_hosts)"
  log "Detected DJANGO_ALLOWED_HOSTS=$ALLOWED_HOSTS"
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root, e.g.: sudo ./deploy/install-8086.sh" >&2
  exit 1
fi

# --- 1. Base packages ---------------------------------------------------
log "Installing base packages"
apt-get update
apt-get install -y python3-venv python3-pip python3-dev build-essential \
  libpq-dev postgresql postgresql-contrib nginx git rsync

# --- 2. Service account and directories ----------------------------------
if ! id "$APP_USER" >/dev/null 2>&1; then
  log "Creating service account $APP_USER"
  useradd --system --home "$BASE_DIR" --shell /usr/sbin/nologin "$APP_USER"
else
  log "Service account $APP_USER already exists"
fi
mkdir -p "$BASE_DIR" "$MEDIA_DIR"
chown -R "$APP_USER":"$APP_USER" "$BASE_DIR" "$MEDIA_DIR"

# --- Database --------------------------------------------------------------
if [ -z "$DB_PASSWORD" ]; then
  if [ -f "$ENV_FILE" ] && grep -q '^DB_PASSWORD=' "$ENV_FILE"; then
    # Reuse the password already provisioned on a prior run.
    DB_PASSWORD="$(sed -n "s/^DB_PASSWORD=\"//p" "$ENV_FILE" | head -1 | sed 's/\"$//')"
  else
    DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    log "Generated a random DB_PASSWORD (saved to $ENV_FILE)"
  fi
fi

DB_USER_EXISTS="$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'")"
if [ "$DB_USER_EXISTS" != "1" ]; then
  log "Creating PostgreSQL role $DB_USER"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
    "CREATE USER \"$DB_USER\" WITH PASSWORD '$(printf '%s' "$DB_PASSWORD" | sed "s/'/''/g")';"
else
  if [ -z "$DB_PASSWORD" ] && [ ! -f "$ENV_FILE" ]; then
    echo "PostgreSQL role $DB_USER exists but password is unknown (no .env file to reuse password from)." >&2
    echo "Either:" >&2
    echo "  1. Provide DB_PASSWORD=<password> when rerunning (must match the existing role's password)" >&2
    echo "  2. Drop the existing role and let the installer recreate it" >&2
    exit 1
  fi
  log "PostgreSQL role $DB_USER already exists; leaving its password untouched"
fi
DB_EXISTS="$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")"
if [ "$DB_EXISTS" != "1" ]; then
  log "Creating database $DB_NAME"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\";"
else
  log "Database $DB_NAME already exists"
fi

# --- 3. Clone and install the application ---------------------------------
if [ -d "$SRC_DIR/.git" ]; then
  log "Updating existing clone at $SRC_DIR"
  sudo -u "$APP_USER" git -C "$SRC_DIR" fetch --prune origin "$BRANCH"
  sudo -u "$APP_USER" git -C "$SRC_DIR" checkout -q "$BRANCH"
  sudo -u "$APP_USER" git -C "$SRC_DIR" reset -q --hard "origin/$BRANCH"
else
  log "Cloning $REPO_URL into $SRC_DIR"
  sudo -u "$APP_USER" git clone --branch "$BRANCH" "$REPO_URL" "$SRC_DIR"
fi

log "Syncing $SRC_DIR -> $APP_DIR"
rsync -a --exclude '.git' "$SRC_DIR"/ "$APP_DIR"/

if [ ! -x "$VENV_DIR/bin/python" ]; then
  log "Creating virtualenv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

chown -R "$APP_USER":"$APP_USER" "$BASE_DIR"

# --- 4. Environment file ---------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
  log "Writing $ENV_FILE"
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
  install -m 640 -o root -g "$APP_USER" /dev/null "$ENV_FILE"
  {
    # Double-quote every value: escape backslashes, double quotes, dollar signs, and backticks
    # so both bash and systemd's EnvironmentFile parser receive the original value.
    printf "DJANGO_SECRET_KEY=\"%s\"\n" "$(printf '%s' "$SECRET_KEY" | sed 's/[\\\"$`]/\\&/g')"
    printf "DJANGO_DEBUG=0\n"
    printf "DJANGO_ALLOWED_HOSTS=\"%s\"\n" "$ALLOWED_HOSTS"
    printf "DB_NAME=\"%s\"\n" "$DB_NAME"
    printf "DB_USER=\"%s\"\n" "$DB_USER"
    printf "DB_PASSWORD=\"%s\"\n" "$(printf '%s' "$DB_PASSWORD" | sed 's/[\\\"$`]/\\&/g')"
    printf "DB_HOST=127.0.0.1\n"
    printf "DB_PORT=5432\n"
    printf "MEDIA_ROOT=%s\n" "$MEDIA_DIR"
  } >> "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  chown root:"$APP_USER" "$ENV_FILE"
else
  # Re-runs must still repair a host list that is missing this server's LAN IP,
  # which Django rejects with "Bad Request (400)".
  CURRENT_HOSTS="$(sed -n 's/^DJANGO_ALLOWED_HOSTS="\{0,1\}\(.*\)/\1/p' "$ENV_FILE" | head -1 | sed 's/"$//')"
  MERGED_HOSTS="$CURRENT_HOSTS"
  for host in ${ALLOWED_HOSTS//,/ }; do
    case ",$MERGED_HOSTS," in
      *",$host,"*) ;;
      *) MERGED_HOSTS="${MERGED_HOSTS:+$MERGED_HOSTS,}$host" ;;
    esac
  done
  if [ "$MERGED_HOSTS" != "$CURRENT_HOSTS" ]; then
    log "Updating DJANGO_ALLOWED_HOSTS in $ENV_FILE to $MERGED_HOSTS"
    if grep -q '^DJANGO_ALLOWED_HOSTS=' "$ENV_FILE"; then
      sed -i "s|^DJANGO_ALLOWED_HOSTS=.*|DJANGO_ALLOWED_HOSTS=\"$MERGED_HOSTS\"|" "$ENV_FILE"
    else
      printf 'DJANGO_ALLOWED_HOSTS="%s"\n' "$MERGED_HOSTS" >> "$ENV_FILE"
    fi
  else
    log "$ENV_FILE already exists and lists this server; leaving it untouched"
  fi
fi

# --- 5. Migrate and collectstatic ------------------------------------------
run_manage() {
  ( set -a; . "$ENV_FILE"; set +a
    cd "$APP_DIR"
    sudo -u "$APP_USER" --preserve-env "$VENV_DIR/bin/python" manage.py "$@" )
}
log "Applying database migrations"
run_manage migrate --noinput
log "Collecting static files"
run_manage collectstatic --noinput

# --- 5b. Initial administrator -------------------------------------------
if ! run_manage shell -c "import sys; from django.contrib.auth import get_user_model; sys.exit(0 if get_user_model().objects.filter(is_superuser=True).exists() else 1)" >/dev/null 2>&1; then
  log "Seeding the initial administrator account"
  run_manage seed_admin
  log "Sign in at /admin/ and change the password immediately."
else
  log "A superuser already exists; leaving accounts untouched"
fi

# --- 6. Gunicorn service -----------------------------------------------
log "Installing systemd service"
{
  sed "s|@BASE_DIR@|$BASE_DIR|g; s|@APP_USER@|$APP_USER|g; s|@MEDIA_DIR@|$MEDIA_DIR|g" \
    "$APP_DIR/deploy/correspondence-8086.service"
} > /etc/systemd/system/correspondence.service
systemctl daemon-reload
systemctl enable --now correspondence
systemctl restart correspondence
sleep 2
systemctl is-active --quiet correspondence || {
  echo "correspondence service failed to start; check: journalctl -u correspondence -n 50" >&2
  exit 1
}

# --- 7. Nginx on port 8086 --------------------------------------------
log "Installing nginx site"
if [ ! -f /etc/nginx/sites-available/correspondence ]; then
  {
    sed "s|@BASE_DIR@|$BASE_DIR|g; s|^    server_name .*|    server_name _ ${ALLOWED_HOSTS//,/ };|" \
      "$APP_DIR/deploy/nginx-8086.conf.example"
  } > /etc/nginx/sites-available/correspondence
  ln -sf /etc/nginx/sites-available/correspondence /etc/nginx/sites-enabled/correspondence
  nginx -t
  systemctl reload nginx
else
  log "/etc/nginx/sites-available/correspondence already exists; preserving it"
fi

if [ -n "$LAN_SUBNET" ] && command -v ufw >/dev/null 2>&1; then
  log "Allowing TCP/8086 from $LAN_SUBNET via ufw"
  ufw allow from "$LAN_SUBNET" to any port 8086 proto tcp
  ufw reload
fi

# Record deployment success: all steps and health checks have passed.
sudo -u "$APP_USER" git -C "$SRC_DIR" rev-parse HEAD | tee "$BASE_DIR/.deployed_commit" >/dev/null

log "Done. Verify with:"
log "  systemctl is-active correspondence nginx"
log "  curl -I http://127.0.0.1:8086/"
log "Edit DJANGO_ALLOWED_HOSTS and server_name in /etc/nginx/sites-available/correspondence for your real LAN IP/hostname."
