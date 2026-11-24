"""Isolation de la suite de tests : aucun effet de bord sur le poste ou les données réelles."""
import pytest


@pytest.fixture(autouse=True)
def isolated_environment(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path / 'media'
    settings.FILE_SEARCH_ROOT = str(tmp_path)
    settings.BACKGROUND_TASKS_INLINE = True
    # Jamais de service externe, quelle que soit la configuration du poste.
    settings.STORAGES = {**settings.STORAGES, 'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}}
    for name in ('JIRA_BASE_URL', 'JIRA_EMAIL', 'JIRA_API_TOKEN', 'JIRA_WEBHOOK_SECRET'):
        setattr(settings, name, '')
    settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

    # Ne jamais ouvrir réellement VS Code pendant les tests.
    import subprocess
    real_popen = subprocess.Popen

    class GuardedPopen(real_popen):
        def __new__(cls, args=None, *a, **kw):
            if isinstance(args, (list, tuple)) and args and args[0] == 'code':
                return object()
            return real_popen.__new__(cls)

    monkeypatch.setattr(subprocess, 'Popen', GuardedPopen)

    # L'horloge Windows peut rendre deux fois le même instant : on garantit un ordre strict
    # pour que les tests de tri chronologique soient déterministes.
    from datetime import timedelta
    from django.utils import timezone
    real_now, last = timezone.now, [None]

    def strictly_increasing_now():
        value = real_now()
        if last[0] is not None and value <= last[0]:
            value = last[0] + timedelta(microseconds=1)
        last[0] = value
        return value

    monkeypatch.setattr(timezone, 'now', strictly_increasing_now)
