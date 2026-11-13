# Ubuntu deployment on port 8086

Target layout (Ubuntu 22.04/24.04, LAN server):

```
/opt/correspondence-cms/
├── src/      git clone (fetch/compare only)
├── app/      live application files served by Gunicorn
├── venv/     Python virtual environment
└── .deployed_commit   commit currently installed in app/
/etc/correspondence-cms.env   environment variables (secrets)
/var/lib/correspondence-cms/media   uploaded attachments
```

Nginx listens on **8086** and proxies to Gunicorn on `127.0.0.1:8087`.

## 1. Base packages and database

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip python3-dev build-essential \
    libpq-dev postgresql postgresql-contrib nginx git rsync

sudo -u postgres psql <<'SQL'
CREATE USER correspondence_user WITH PASSWORD 'SET-A-STRONG-PASSWORD';
CREATE DATABASE correspondence_db OWNER correspondence_user;
SQL
```

## 2. Service account and directories

```bash
sudo useradd --system --home /opt/correspondence-cms --shell /usr/sbin/nologin correspondence
sudo mkdir -p /opt/correspondence-cms /var/lib/correspondence-cms/media
sudo chown -R correspondence:correspondence /opt/correspondence-cms /var/lib/correspondence-cms
```

## 3. Clone and install the application

```bash
sudo git clone https://github.com/DSTech-876/DGC_CorespondingLog.git /opt/correspondence-cms/src
sudo rsync -a --exclude '.git' /opt/correspondence-cms/src/ /opt/correspondence-cms/app/

sudo python3 -m venv /opt/correspondence-cms/venv
sudo /opt/correspondence-cms/venv/bin/pip install --upgrade pip
sudo /opt/correspondence-cms/venv/bin/pip install -r /opt/correspondence-cms/app/requirements.txt

# record the installed commit so incremental updates work later
git -C /opt/correspondence-cms/src rev-parse HEAD | sudo tee /opt/correspondence-cms/.deployed_commit
sudo chown -R correspondence:correspondence /opt/correspondence-cms
```

## 4. Environment file

```bash
sudo install -m 640 -o root -g correspondence /dev/null /etc/correspondence-cms.env
sudo nano /etc/correspondence-cms.env
```

```ini
DJANGO_SECRET_KEY=generate-a-long-random-value
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=192.168.1.10,correspondence.lan,127.0.0.1
DB_NAME=correspondence_db
DB_USER=correspondence_user
DB_PASSWORD=SET-A-STRONG-PASSWORD
DB_HOST=127.0.0.1
DB_PORT=5432
MEDIA_ROOT=/var/lib/correspondence-cms/media
```

Generate a key with:
`python3 -c "import secrets;print(secrets.token_urlsafe(64))"`

Because the site is reached on a non-standard port, `DJANGO_ALLOWED_HOSTS`
entries stay host-only (no port) — Django strips the port when validating.

## 5. Migrate, create admin, collect static

```bash
set -a; . /etc/correspondence-cms.env; set +a
cd /opt/correspondence-cms/app
sudo -u correspondence --preserve-env /opt/correspondence-cms/venv/bin/python manage.py migrate
sudo -u correspondence --preserve-env /opt/correspondence-cms/venv/bin/python manage.py createsuperuser
sudo -u correspondence --preserve-env /opt/correspondence-cms/venv/bin/python manage.py collectstatic --noinput
```

## 6. Gunicorn service

Install `deploy/correspondence-8086.service`:

```bash
sudo cp /opt/correspondence-cms/app/deploy/correspondence-8086.service \
        /etc/systemd/system/correspondence.service
sudo systemctl daemon-reload
sudo systemctl enable --now correspondence
sudo systemctl status correspondence --no-pager
curl -I http://127.0.0.1:8087/
```

## 7. Nginx on port 8086

```bash
sudo cp /opt/correspondence-cms/app/deploy/nginx-8086.conf.example \
        /etc/nginx/sites-available/correspondence
sudo ln -sf /etc/nginx/sites-available/correspondence /etc/nginx/sites-enabled/correspondence
sudo nginx -t && sudo systemctl reload nginx
curl -I http://127.0.0.1:8086/
```

If SELinux/AppArmor or `ufw` is active, open the port only to the office subnet:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8086 proto tcp
sudo ufw reload
```

Browse to `http://SERVER-LAN-IP:8086/`.

## 8. Verify

```bash
systemctl is-active correspondence nginx
sudo ss -ltnp | grep -E '8086|8087'
curl -s -o /dev/null -w '%{http_code}\n' http://SERVER-LAN-IP:8086/accounts/login/
```

## 9. Updating after GitHub changes

`deploy/update-from-github.sh` fetches the branch, compares the recorded
deployed commit with the new one, and copies **only added/modified files** into
`/opt/correspondence-cms/app`. It then runs `pip install`, `migrate` or
`collectstatic` only when the matching files changed, restarts the service and
records the new commit.

```bash
sudo cp /opt/correspondence-cms/app/deploy/update-from-github.sh /usr/local/bin/correspondence-update
sudo chmod 750 /usr/local/bin/correspondence-update
sudo correspondence-update
```

Overridable variables: `BRANCH`, `BASE_DIR`, `SRC_DIR`, `APP_DIR`, `VENV_DIR`,
`ENV_FILE`, `APP_USER`, `SERVICE`.

```bash
sudo BRANCH=release correspondence-update
```

Files deleted upstream are listed but not removed from the server; delete them
manually after review. The script refuses to run before the initial install has
recorded a commit in `/opt/correspondence-cms/.deployed_commit`.

## Notes

- Add internal HTTPS before carrying credentials or sensitive records over
  shared networks; with TLS also set `SECURE_PROXY_SSL_HEADER`,
  `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE`.
- `/media/` is served directly by Nginx in the example config. Replace it with
  authenticated attachment downloads before production use.
- Back up PostgreSQL (`pg_dump`) and `/var/lib/correspondence-cms/media` on a
  schedule and test a restore.
