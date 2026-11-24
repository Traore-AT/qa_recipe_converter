"""Stockage de test sans accès disque : même contrat que S3 (`path()` indisponible)."""
from django.core.files.base import ContentFile
from django.core.files.storage import Storage


class RemoteStorage(Storage):
    def __init__(self):
        self.files = {}

    def _save(self, name, content):
        content.seek(0)
        self.files[name] = content.read()
        return name

    def _open(self, name, mode='rb'):
        if name not in self.files:
            raise FileNotFoundError(name)
        return ContentFile(self.files[name], name)

    def exists(self, name):
        return name in self.files

    def delete(self, name):
        self.files.pop(name, None)

    def url(self, name):
        return f'https://stockage.test/{name}'

    def size(self, name):
        return len(self.files[name])
