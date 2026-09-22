#!/bin/sh
set -e

echo "Running database migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput 2>/dev/null || true

echo "Starting gunicorn..."
exec gunicorn config.wsgi:application --config /app/gunicorn.conf.py
