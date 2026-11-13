#!/usr/bin/env bash
set -euo pipefail
sudo apt update
sudo apt install -y python3-venv python3-pip python3-dev build-essential libpq-dev postgresql postgresql-contrib nginx
echo "Create the PostgreSQL role/database with:"
echo "sudo -u postgres psql"
echo "CREATE USER correspondence_user WITH PASSWORD 'SET-A-STRONG-PASSWORD';"
echo "CREATE DATABASE correspondence_db OWNER correspondence_user;"
echo "\\q"
echo "Then create /opt/correspondence-cms, install the project, configure .env, migrate, collectstatic, and configure Gunicorn/Nginx."
