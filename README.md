# Correspondence Management System (starter)

Django + PostgreSQL starter for a LAN-hosted correspondence register.

## Included in this first build
- Staff login using Django authentication
- Correspondence register with original spreadsheet fields
- Incoming / outgoing / unspecified direction
- Search by ID, sender, recipient, description, reference and date range
- Attachments, action history and collection history
- CSV export permission
- Audit entries for core changes
- Import command for XLSX, CSV and Markdown pipe tables

## Important before production
This is a development starter, not yet a production-approved records system. The current import command deliberately labels imported direction as `Other / Not specified`, because the source register does not contain a reliable incoming/outgoing field. It stores source row values in `original_row`. Review import results before operational use. Do not expose media files publicly; configure authenticated attachment downloads before production, and add a formal retention/backup policy. Sensitive personal/medical references require access controls and an organizational privacy review.

## Local setup
1. Install Python 3.11+, PostgreSQL and the requirements.
2. Create database and user as shown in `deploy/ubuntu-install.sh`.
3. Copy `.env.example` to `.env` and set secure values. Export variables into the shell or use systemd EnvironmentFile.
4. Run:
   ```
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python manage.py makemigrations registry
python manage.py migrate
   python manage.py createsuperuser
   python manage.py runserver 0.0.0.0:8000
   ```
5. Browse to `http://SERVER-LAN-IP:8000/`.

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

## User roles
Create staff users in Django Admin (`/admin/`). Grant `registry.export_correspondence` to users allowed to export. Django's default `is_staff` is for admin access; ordinary staff users can use the main register without admin access. For production, define groups such as Registry Clerk, Supervisor, Read-only and System Administrator.

## LAN deployment
For a full Ubuntu walkthrough serving the site on port 8086 (Nginx 8086 -> Gunicorn 127.0.0.1:8087), see `deploy/UBUNTU_DEPLOY_8086.md`, with `deploy/correspondence-8086.service`, `deploy/nginx-8086.conf.example` and the incremental updater `deploy/update-from-github.sh`.

See `deploy/ubuntu-install.sh` for base packages and PostgreSQL setup. Configure Nginx + Gunicorn with `deploy/correspondence.service` and `deploy/nginx.conf.example`. Replace the example LAN IP/hostname and set a firewall rule allowing TCP/80 only from the office subnet. Add internal HTTPS if credentials or sensitive records cross shared networks.

## Known next steps
- Confirm the two blank/ambiguous `FROM` and `TO` columns in the original workbook.
- Verify all original workbook sheets and rows; the provided Markdown extract appears to be only a small portion of the register.
- Add protected attachment serving, file type/size validation, virus scanning, password policy, rate limiting, complete audit coverage and tested restore procedure before go-live.
- Add XLSX export and printable report layouts.
