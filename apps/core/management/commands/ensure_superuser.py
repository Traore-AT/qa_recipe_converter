"""
Management command: python manage.py ensure_superuser

Crée un superutilisateur (idempotent) à partir des variables
d'environnement DJANGO_SUPERUSER_* — utile sur Render Free où le
Shell n'est pas disponible.

Usage:
    python manage.py ensure_superuser
    DJANGO_SUPERUSER_USERNAME=admin DJANGO_SUPERUSER_EMAIL=a@b.c DJANGO_SUPERUSER_PASSWORD=*** python manage.py ensure_superuser
"""
import os
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Crée un superutilisateur idempotent via les variables d\'environnement DJANGO_SUPERUSER_*'

    def handle(self, *args, **options):
        User = get_user_model()
        username = os.getenv('DJANGO_SUPERUSER_USERNAME', 'admin')
        email = os.getenv('DJANGO_SUPERUSER_EMAIL', 'admin@example.com')
        password = os.getenv('DJANGO_SUPERUSER_PASSWORD')

        if not password:
            self.stdout.write(
                self.style.WARNING(
                    'Aucun mot de passe (DJANGO_SUPERUSER_PASSWORD) — superuser ignoré.'
                )
            )
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(
                self.style.WARNING(f"Le superutilisateur '{username}' existe déjà.")
            )
            return

        with transaction.atomic():
            User.objects.create_superuser(
                username=username, email=email, password=password
            )
        self.stdout.write(
            self.style.SUCCESS(f"Superutilisateur '{username}' créé.")
        )