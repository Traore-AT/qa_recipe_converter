#!/usr/bin/env sh
# Applique les migrations en attente sans bloquer (voir apps/core/management/commands/safe_migrate.py).
# Utilisable comme « Pre-Deploy Command » Render ou à la main ; ajoutez --strict pour échouer en cas d'erreur.
cd "$(dirname "$0")/.." || exit 0
python manage.py safe_migrate "$@"
