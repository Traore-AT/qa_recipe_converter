"""Accès aux fichiers indépendant du stockage (disque local ou S3)."""
import os
import shutil
import tempfile
from contextlib import contextmanager


@contextmanager
def local_copy(field_file):
    """
    Chemin local d'un fichier stocké. Sur disque : le fichier lui-même ;
    sur un stockage distant : une copie temporaire supprimée en sortie.
    """
    try:
        path = field_file.path
    except NotImplementedError:
        path = None
    if path is not None:
        yield path
        return
    handle, temp_path = tempfile.mkstemp(suffix=os.path.splitext(field_file.name)[1])
    try:
        with os.fdopen(handle, 'wb') as target, field_file.storage.open(field_file.name, 'rb') as source:
            shutil.copyfileobj(source, target)
        yield temp_path
    finally:
        os.unlink(temp_path)
