import os
import socket
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "replace-this-before-production")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"


def _split_env(name, default=""):
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


def _local_hostnames():
    """Host names/IPv4 addresses this machine answers to, so a LAN deployment
    reached by its own IP does not fail host validation with Bad Request (400)."""
    names = {"127.0.0.1", "localhost", "[::1]"}
    try:
        hostname = socket.gethostname()
        names.add(hostname)
        names.add(f"{hostname}.local")
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            names.add(info[4][0])
    except OSError:
        pass
    return sorted(names)


ALLOWED_HOSTS = _split_env("DJANGO_ALLOWED_HOSTS")
if "*" not in ALLOWED_HOSTS:
    # Always keep loopback and this host's own names/addresses reachable.
    ALLOWED_HOSTS = sorted(set(ALLOWED_HOSTS) | set(_local_hostnames()))

# Non-standard ports (e.g. 8086) must appear in the CSRF origin, otherwise login
# POSTs are rejected. Derive origins from the allowed hosts unless set explicitly.
CSRF_TRUSTED_ORIGINS = _split_env("DJANGO_CSRF_TRUSTED_ORIGINS")
if not CSRF_TRUSTED_ORIGINS:
    _ports = _split_env("DJANGO_SITE_PORTS", "8000,8001,8086")
    for _host in ALLOWED_HOSTS:
        if _host == "*":
            continue
        _host = _host.lstrip(".")
        for _scheme in ("http", "https"):
            CSRF_TRUSTED_ORIGINS.append(f"{_scheme}://{_host}")
            CSRF_TRUSTED_ORIGINS.extend(f"{_scheme}://{_host}:{_port}" for _port in _ports)

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "registry",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "registry.middleware.ForcePasswordChangeMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
# DB_ENGINE=sqlite3 is an opt-in local-dev/test shortcut; production keeps postgresql.
if os.environ.get("DB_ENGINE") == "sqlite3":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "correspondence_db"),
            "USER": os.environ.get("DB_USER", "correspondence_user"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "5432"),
        }
    }
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en"
TIME_ZONE = "America/Jamaica"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "private_media"))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# --------------------------------------------------------------------- email
# Self-service password reset emails. Without SMTP settings the links are
# written to the server console/log, which still works for a LAN deployment
# where an administrator reads the link and passes it on.
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
    EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "0") == "1"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "correspondence-register@localhost")
# Password reset links expire after this many seconds (default 2 hours).
PASSWORD_RESET_TIMEOUT = int(os.environ.get("PASSWORD_RESET_TIMEOUT", str(60 * 60 * 2)))

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
