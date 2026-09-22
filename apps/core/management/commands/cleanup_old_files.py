"""
Management command: python manage.py cleanup_old_files

Supprime les tâches de conversion et fichiers associés
plus anciens que FILE_RETENTION_HOURS (défaut: 1 heure).

Usage:
    python manage.py cleanup_old_files
    python manage.py cleanup_old_files --hours 2
    python manage.py cleanup_old_files --dry-run
"""
import os
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from apps.core.models import ConversionJob


class Command(BaseCommand):
    help = 'Supprime les fichiers de conversion plus vieux que FILE_RETENTION_HOURS'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=getattr(settings, 'FILE_RETENTION_HOURS', 1),
            help='Nombre d\'heures de rétention (défaut: 1)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simuler sans supprimer'
        )

    def handle(self, *args, **options):
        hours = options['hours']
        dry_run = options['dry_run']
        cutoff = timezone.now() - timedelta(hours=hours)

        old_jobs = ConversionJob.objects.filter(created_at__lt=cutoff)
        count = old_jobs.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS('Aucune tâche à supprimer.'))
            return

        self.stdout.write(f"{'[DRY RUN] ' if dry_run else ''}Suppression de {count} tâche(s) de plus de {hours}h...")

        for job in old_jobs:
            # Delete associated files
            for field in [job.word_file, job.excel_template, job.result_file]:
                if field and field.name:
                    try:
                        path = field.path
                        if os.path.exists(path) and not dry_run:
                            os.remove(path)
                            self.stdout.write(f"  Fichier supprimé: {path}")
                    except Exception as e:
                        self.stderr.write(f"  Erreur suppression fichier: {e}")

        if not dry_run:
            old_jobs.delete()
            self.stdout.write(self.style.SUCCESS(f'✓ {count} tâche(s) supprimée(s).'))
        else:
            self.stdout.write(self.style.WARNING(f'[DRY RUN] {count} tâche(s) auraient été supprimées.'))
