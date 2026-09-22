"""
Gunicorn configuration for QA Recipe Converter — production.
"""
import multiprocessing
import os

# ── Socket ────────────────────────────────────────────────────────────────
bind = os.getenv('GUNICORN_BIND', '0.0.0.0:8000')

# ── Workers ───────────────────────────────────────────────────────────────
# Formula: 2 * CPU cores + 1, capped at 8 for production memory safety
cpu_cores = multiprocessing.cpu_count()
default_workers = min(2 * cpu_cores + 1, 8)
workers = int(os.getenv('GUNICORN_WORKERS', default_workers))
worker_class = os.getenv('GUNICORN_WORKER_CLASS', 'sync')

# ── Timeouts ──────────────────────────────────────────────────────────────
timeout = int(os.getenv('GUNICORN_TIMEOUT', 120))
graceful_timeout = int(os.getenv('GUNICORN_GRACEFUL_TIMEOUT', 30))
keepalive = int(os.getenv('GUNICORN_KEEPALIVE', 5))

# ── Resources ─────────────────────────────────────────────────────────────
max_requests = int(os.getenv('GUNICORN_MAX_REQUESTS', 1000))
max_requests_jitter = int(os.getenv('GUNICORN_MAX_REQUESTS_JITTER', 100))

# ── Logging ───────────────────────────────────────────────────────────────
accesslog = os.getenv('GUNICORN_ACCESS_LOG', '-')
errorlog = os.getenv('GUNICORN_ERROR_LOG', '-')
loglevel = os.getenv('GUNICORN_LOG_LEVEL', 'info')
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# ── Security ──────────────────────────────────────────────────────────────
limit_request_line = int(os.getenv('GUNICORN_LIMIT_REQUEST_LINE', 4094))
limit_request_fields = int(os.getenv('GUNICORN_LIMIT_REQUEST_FIELDS', 100))
limit_request_field_size = int(os.getenv('GUNICORN_LIMIT_REQUEST_FIELD_SIZE', 8190))

# ── Server mechanics ──────────────────────────────────────────────────────
preload_app = True
worker_connections = int(os.getenv('GUNICORN_WORKER_CONNECTIONS', 1000))

# Print worker count on startup
print(f'  → Workers: {workers} ({worker_class})')
print(f'  → Timeout: {timeout}s  |  Max requests: {max_requests}')
print(f'  → Bind: {bind}')
