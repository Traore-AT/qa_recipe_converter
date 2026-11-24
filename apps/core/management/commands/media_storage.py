"""
Outils du stockage des fichiers.

    python manage.py media_storage --check             # écriture / lecture / URL / suppression
    python manage.py media_storage --migrate [--dry-run]  # copie media/ vers le stockage S3
"""
import uuid
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.files.base import ContentFile, File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db.models import FileField


class Command(BaseCommand):
    help = 'Vérifie le stockage des fichiers ou y migre le contenu de media/.'

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true')
        parser.add_argument('--migrate', action='store_true')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if not (options['check'] or options['migrate']):
            raise CommandError('Indiquez --check ou --migrate.')
        if options['check']:
            self.check_storage()
        if options['migrate']:
            self.migrate_files(options['dry_run'])

    def check_storage(self):
        name = default_storage.save(f'healthcheck/{uuid.uuid4().hex}.txt', ContentFile(b'ok'))
        try:
            with default_storage.open(name, 'rb') as handle:
                if handle.read() != b'ok':
                    raise CommandError('Le contenu relu ne correspond pas.')
            default_storage.url(name)
        finally:
            default_storage.delete(name)
        self.stdout.write(self.style.SUCCESS(f'Stockage opérationnel ({type(default_storage._wrapped).__name__}).'))

    def migrate_files(self, dry_run):
        try:
            default_storage.path('x')
        except NotImplementedError:
            pass
        else:
            raise CommandError("Le stockage actif est le disque local : configurez d'abord S3_BUCKET.")
        copied = skipped = missing = 0
        for model in apps.get_models():
            for field in (f for f in model._meta.get_fields() if isinstance(f, FileField)):
                names = model._default_manager.exclude(**{field.name: ''}).exclude(
                    **{f'{field.name}__isnull': True}).values_list(field.name, flat=True)
                for name in names.iterator():
                    source = Path(settings.MEDIA_ROOT) / name
                    if default_storage.exists(name):
                        skipped += 1
                    elif not source.is_file():
                        missing += 1
                        self.stderr.write(f'Introuvable localement : {name}')
                    else:
                        copied += 1
                        if not dry_run:
                            with source.open('rb') as handle:
                                default_storage.save(name, File(handle))
        prefix = '[simulation] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}{copied} fichier(s) copié(s), {skipped} déjà présent(s), {missing} introuvable(s).'))
