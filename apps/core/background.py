"""Exécution différée légère : après validation de la transaction, hors du cycle de la requête."""
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import close_old_connections, connections, transaction

logger = logging.getLogger(__name__)
_executors = {}


def _executor():
    # Un pool par processus : gunicorn duplique le processus après le préchargement.
    pid = os.getpid()
    if pid not in _executors:
        _executors[pid] = ThreadPoolExecutor(max_workers=2, thread_name_prefix='qa-bg')
    return _executors[pid]


def _run(func, args, kwargs):
    close_old_connections()
    try:
        func(*args, **kwargs)
    except Exception:
        logger.exception('Échec de la tâche de fond %s', getattr(func, '__name__', func))
    finally:
        connections.close_all()


def run_in_background(func, *args, **kwargs):
    """Une erreur dans la tâche est journalisée et n'affecte jamais l'action de l'utilisateur."""
    if settings.BACKGROUND_TASKS_INLINE:
        try:
            with transaction.atomic():
                func(*args, **kwargs)
        except Exception:
            logger.exception('Échec de la tâche %s', getattr(func, '__name__', func))
        return
    transaction.on_commit(lambda: _executor().submit(_run, func, args, kwargs))
