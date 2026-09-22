from .base import *

DEBUG = True

ALLOWED_HOSTS = ['*']

# ── Mailpit (email development) ───────────────────────────────────────────────
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST     = os.getenv('EMAIL_HOST', 'localhost')
EMAIL_PORT     = int(os.getenv('EMAIL_PORT', 1025))
EMAIL_USE_TLS  = False
EMAIL_USE_SSL  = False

# Mailpit UI : http://localhost:8025
