"""
Management command: python manage.py safe_migrate

Applique les migrations en attente sans jamais gêner le fonctionnement du système :

- rien à appliquer (migrations déjà passées, par exemple par une commande Render) → sortie immédiate ;
- verrou PostgreSQL (advisory lock) : deux démarrages simultanés n'appliquent pas les migrations
  en même temps ; si le verrou n'est pas obtenu à temps, l'étape est ignorée ;
- une erreur est journalisée mais ne fait pas échouer la commande (sauf avec --strict).

Usage:
    python manage.py safe_migrate                  # tolérant (démarrage du serveur)
    python manage.py safe_migrate --strict         # échoue si une migration échoue (CI, pré-déploiement)
    python manage.py safe_migrate --lock-timeout 30
"""
import logging
import time
from contextlib import contextmanager

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor

logger = logging.getLogger(__name__)

# Identifiant arbitraire mais stable du verrou applicatif « migrations ».
LOCK_KEY = 784_221_015


def pending_migrations(connection):
    executor = MigrationExecutor(connection)
    return executor.migration_plan(executor.loader.graph.leaf_nodes())


@contextmanager
def migration_lock(connection, timeout):
    """True si le verrou est obtenu (ou inutile hors PostgreSQL), False après `timeout` secondes."""
    if connection.vendor != 'postgresql':
        yield True
        return
    deadline = time.monotonic() + timeout
    with connection.cursor() as cursor:
        while True:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [LOCK_KEY])
            if cursor.fetchone()[0]:
                break
            if time.monotonic() >= deadline:
                yield False
                return
            time.sleep(1)
        try:
            yield True
        finally:
            cursor.execute('SELECT pg_advisory_unlock(%s)', [LOCK_KEY])


class Command(BaseCommand):
    help = 'Applique les migrations en attente, sans bloquer si elles sont déjà appliquées.'

    def add_arguments(self, parser):
        parser.add_argument('--strict', action='store_true', help='Échouer si une migration échoue.')
        parser.add_argument('--lock-timeout', type=int, default=60,
                            help='Attente maximale du verrou, en secondes (défaut : 60).')
        parser.add_argument('--database', default=DEFAULT_DB_ALIAS)

    def handle(self, *args, strict=False, lock_timeout=60, database=DEFAULT_DB_ALIAS, **options):
        connection = connections[database]
        try:
            if not pending_migrations(connection):
                self.stdout.write(self.style.SUCCESS('Migrations : base à jour, rien à appliquer.'))
                return
            with migration_lock(connection, lock_timeout) as acquired:
                if not acquired:
                    self.stdout.write(self.style.WARNING(
                        'Migrations : une autre instance les applique déjà — étape ignorée.'))
                    return
                # Une autre instance a pu les appliquer pendant l'attente du verrou.
                plan = pending_migrations(connection)
                if not plan:
                    self.stdout.write(self.style.SUCCESS('Migrations : déjà appliquées par une autre instance.'))
                    return
                self.stdout.write(f'Migrations : {len(plan)} à appliquer…')
                call_command('migrate', database=database, interactive=False, verbosity=1)
                self.stdout.write(self.style.SUCCESS('Migrations : appliquées.'))
        except Exception as exc:
            logger.exception('safe_migrate a échoué')
            message = f'Migrations : échec ({exc}). Le serveur démarre quand même ; corrigez puis redéployez.'
            if strict:
                raise CommandError(message) from exc
            self.stderr.write(self.style.ERROR(message))
