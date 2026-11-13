# Correspondence Management System (starter)

Django + PostgreSQL starter for a LAN-hosted correspondence register.

## Included in this first build
- Modern responsive interface: sidebar navigation shell, dashboard tiles, light/dark theme toggle, print-friendly record pages
- Branded UI using the DGC logo (`static/img/dgc.png`, also used as the favicon) shown on the sign-in page and sidebar
- Staff login using Django authentication
- Correspondence register with original spreadsheet fields
- Incoming / outgoing / unspecified direction
- Search by ID, sender, recipient, description, reference and date range
- Attachments, action history and collection history
- CSV export permission
- Audit entries for core changes
- Role-based user management for administrators (create, edit, deactivate, reset passwords)
- Self-service profile, password change and email-based password reset
- Import command for XLSX, CSV and Markdown pipe tables
- Bootstrap and Bootstrap Icons are vendored in `static/vendor/`, so the UI works on an isolated LAN with no internet access

## Important before production
This is a development starter, not yet a production-approved records system. The current import command deliberately labels imported direction as `Other / Not specified`, because the source register does not contain a reliable incoming/outgoing field. It stores source row values in `original_row`. Review import results before operational use. Do not expose media files publicly; configure authenticated attachment downloads before production, and add a formal retention/backup policy. Sensitive personal/medical references require access controls and an organizational privacy review.

## Local setup
1. Install Python 3.11+, PostgreSQL and the requirements.
2. Create database and user as shown in `deploy/ubuntu-install.sh`.
3. Copy `.env.example` to `.env` and set secure values. Export variables into the shell or use systemd EnvironmentFile. Quote any value containing `$`, `` ` ``, `"`, or spaces (e.g. `DB_PASSWORD='Pa$$w0rd'`), since EnvironmentFile/`.env` values are sourced by the shell.
4. Run:
   ```
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py seed_admin
   python manage.py runserver 0.0.0.0:8000
   ```
   `registry/migrations/` is committed to the repo, so `migrate` alone creates all tables; `makemigrations registry` is only needed after changing `registry/models.py`.
5. Browse to `http://SERVER-LAN-IP:8000/`.

### Quick local test without PostgreSQL
For a fast local smoke test or `manage.py test` run without standing up PostgreSQL, set `DB_ENGINE=sqlite3` (uses a local `db.sqlite3` file). This is a dev/test convenience only — production always uses PostgreSQL:
```
DJANGO_SECRET_KEY=dev DB_ENGINE=sqlite3 python manage.py migrate
DJANGO_SECRET_KEY=dev DB_ENGINE=sqlite3 python manage.py test
```

## Import historical register
Copy the source file to the server, then preview:
```
python manage.py import_register /path/to/register.xlsx --dry-run
```
For the supplied Markdown table:
```
python manage.py import_register "/path/to/Pasted markdown.md" --dry-run
```
After verifying the dry-run count and column mapping:
```
python manage.py import_register /path/to/register.xlsx --skip-existing
```
Use a database backup before importing. The import command does not guess direction or transform ambiguous values. It will not import section labels such as YEAR 2021 or APRIL.

## Seeding the first administrator
`python manage.py seed_admin` creates the initial superuser without an interactive prompt and is safe to re-run. It reads `DJANGO_SUPERUSER_USERNAME` (default `admin`), `DJANGO_SUPERUSER_EMAIL` and `DJANGO_SUPERUSER_PASSWORD`, or the matching `--username`, `--email` and `--password` options. When no password is supplied, a strong one is generated and printed once — store it securely and change it after the first sign in. If the account already exists, the command restores superuser/staff/active privileges and leaves the password alone unless `--reset-password` is given:
```
DJANGO_SUPERUSER_USERNAME=dgcadmin DJANGO_SUPERUSER_PASSWORD='choose-a-strong-password' python manage.py seed_admin
python manage.py seed_admin --username dgcadmin --reset-password
```
`deploy/install-8086.sh` runs this automatically when no superuser exists and prints the generated password at the end of the install.

## Troubleshooting `Bad Request (400)`
Django returns `Bad Request (400)` when the `Host` header is not in `ALLOWED_HOSTS`. Loopback addresses and this server's own hostname/IPv4 addresses are now always allowed automatically, so add any extra name used to reach the site (DNS alias, reverse-proxy hostname) to `DJANGO_ALLOWED_HOSTS`, host-only and without the port. `deploy/install-8086.sh` detects the LAN IP and repairs an existing `DJANGO_ALLOWED_HOSTS` on re-run. Because the site runs on a non-standard port, CSRF origins are derived from the allowed hosts for ports `8000,8001,8086` (override with `DJANGO_SITE_PORTS`, or set `DJANGO_CSRF_TRUSTED_ORIGINS` explicitly).

## User roles
Every account has a registry role that maps to a Django permission group, applied automatically when the role is set:

| Role | Can do |
| --- | --- |
| Viewer | Read the register, open records, use search and filters |
| Records officer | Everything a viewer can, plus register and edit correspondence, upload attachments, add actions and collection entries, export CSV |
| Administrator | Everything above, plus user management and the Django admin site |

Roles are defined in `registry/roles.py`; adding a permission there and re-saving a user re-applies the group. Views are gated on the underlying permissions, so a viewer who reaches a write URL directly receives `403 Forbidden`.

## User management
Administrators manage accounts in the app at `/users/` (sidebar: **Administration → User management**) without needing the Django admin site:
- Search and filter accounts by name, username, email, role and active/inactive status
- Create an account with a temporary password; the user is forced to choose a new one at next sign-in
- Edit names, email, job title, phone and role
- Deactivate or reactivate an account (deactivating preserves its records and audit history)
- Reset another user's password, again forcing a change at next sign-in

Administrators cannot demote or deactivate their own account, which prevents locking the last administrator out. Every user-management action is written to the audit log.

## Passwords and password reset
- Users change their own password at **Account → Change password**.
- Accounts flagged `must_change_password` are redirected to the change-password page until they set their own password; this is enforced by `registry.middleware.ForcePasswordChangeMiddleware`.
- The sign-in page offers **Forgot password?**, which emails a time-limited reset link (`PASSWORD_RESET_TIMEOUT`, default 2 hours).
- Email delivery is configured with `EMAIL_HOST` and friends (see `.env.example`). When `EMAIL_HOST` is unset, Django's console backend is used and the reset message — including the working link — is printed to the service log, which is usable on a closed LAN without a mail relay (`journalctl -u correspondence-8086`).
- Django's standard password validators apply (minimum 8 characters, not entirely numeric, not a common password, not similar to the username).

## LAN deployment
Recommended: run `sudo ./deploy/install-8086.sh` on a fresh Ubuntu 22.04/24.04 LAN server. It automates the full walkthrough below end-to-end and is safe to re-run (idempotent) — it creates the service account, clones/installs the app, writes `/etc/correspondence-cms.env` with a generated secret key (safely quoting every value), creates the PostgreSQL role/database, migrates, collects static files, seeds the first admin user with `seed_admin` (the generated password is printed at the end of the run), and installs the systemd + Nginx units on port 8086. Override `REPO_URL`, `BRANCH`, `DB_PASSWORD`, `ALLOWED_HOSTS`, or `LAN_SUBNET` via environment variables.

For the full manual walkthrough (useful to understand or customize each step) serving the site on port 8086 (Nginx 8086 -> Gunicorn 127.0.0.1:8087), see `deploy/UBUNTU_DEPLOY_8086.md`, with `deploy/correspondence-8086.service`, `deploy/nginx-8086.conf.example` and the incremental updater `deploy/update-from-github.sh`.

To pull updated files from GitHub later, run `./update.sh` in the checkout (add `sudo ... --deploy` to also update `/opt/correspondence-cms` and restart the service). See `deploy/UBUNTU_DEPLOY_8086.md` section 9.

Legacy simple alternative: `deploy/ubuntu-install.sh` (base packages + PostgreSQL only) with `deploy/correspondence.service` and `deploy/nginx.conf.example` (plain `/opt/correspondence-cms` layout, port 8001, no atomic app/src split). Replace the example LAN IP/hostname and set a firewall rule allowing TCP/80 only from the office subnet. Add internal HTTPS if credentials or sensitive records cross shared networks.

## Known next steps
- Confirm the two blank/ambiguous `FROM` and `TO` columns in the original workbook.
- Verify all original workbook sheets and rows; the provided Markdown extract appears to be only a small portion of the register.
- Add protected attachment serving, file type/size validation, virus scanning, password expiry policy, rate limiting, complete audit coverage and tested restore procedure before go-live.
- Configure an SMTP relay so password-reset links reach users by email instead of only the service log.
- Add XLSX export and printable report layouts.
