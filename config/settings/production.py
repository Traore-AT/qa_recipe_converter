from django.core.exceptions import ImproperlyConfigured
import dj_database_url

from .base import *

DEBUG = False


def _require_env(key):
    value = os.getenv(key)
    if not value:
        raise ImproperlyConfigured(f"Missing required environment variable: {key}")
    return value


SECRET_KEY = _require_env('SECRET_KEY')

# Render host (https://<app>.onrender.com) + localhost for debugging behind proxy
ALLOWED_HOSTS = [
    h.strip()
    for h in os.getenv('ALLOWED_HOSTS', '.onrender.com,localhost,127.0.0.1').split(',')
    if h.strip()
]

# ── PostgreSQL (Render Managed Postgres) ─────────────────────────────────────
DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600, ssl_require=True),
    }
else:
    raise ImproperlyConfigured('DATABASE_URL is required in production')

# ── HTTPS (Render terminates TLS) ────────────────────────────────────────────
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Cross-site auth (frontend React on Netlify → API on Render)
SESSION_COOKIE_SAMESITE = os.getenv('SESSION_COOKIE_SAMESITE', 'None')
CSRF_COOKIE_SAMESITE = os.getenv('CSRF_COOKIE_SAMESITE', 'None')

# ── CORS / CSRF origins ───────────────────────────────────────────────────────
_BASE_CORS = ','.join(CORS_ALLOWED_ORIGINS)
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv('CORS_ALLOWED_ORIGINS', _BASE_CORS).split(',')
    if o.strip()
]

_BASE_CSRF = ','.join(CSRF_TRUSTED_ORIGINS)
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.getenv('CSRF_TRUSTED_ORIGINS', _BASE_CSRF).split(',')
    if o.strip()
]

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Absolute base URL used in emails (set to the Render backend URL)
SITE_URL = _require_env('SITE_URL')