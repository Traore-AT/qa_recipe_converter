"""Création des cas de test extraits d'un document Word (partagée par l'API et l'interface web)."""
import re

from .models import ExtractedUseCase

TEXT_FIELDS = ('use_case_text', 'description', 'preconditions', 'steps', 'expected_results', 'observed_results')
JIRA_MAX_LENGTH = ExtractedUseCase._meta.get_field('jira_ticket').max_length
# Clé Jira : PROJET-123 (projet en majuscules, commençant par une lettre).
JIRA_KEY_RE = re.compile(r'^[A-Z][A-Z0-9_]+-\d+$')
_SEPARATORS = re.compile(r'[\n\r;,]+')


def split_jira_keys(value) -> list[str]:
    """Références Jira distinctes d'une cellule (« QA-1, QA-2 », une par ligne, …)."""
    return list(dict.fromkeys(p.strip() for p in _SEPARATORS.split(str(value or '')) if p.strip()))


def clean_jira_ticket(value) -> str:
    """Une ou plusieurs références Jira sur une seule ligne (« QA-1, QA-2 »), tronquées à la taille du champ."""
    return ', '.join(split_jira_keys(value))[:JIRA_MAX_LENGTH].rstrip(', ')


def is_valid_jira_key(key) -> bool:
    return bool(JIRA_KEY_RE.match(str(key or '').strip()))


def invalid_jira_keys(value) -> list[str]:
    """Références d'une cellule qui ne respectent pas le format PROJET-123."""
    return [key for key in split_jira_keys(value) if not is_valid_jira_key(key)]


def save_extracted_use_cases(job, rows) -> int:
    """Enregistre les cas extraits (dans l'ordre du document), numéro de ticket Jira compris."""
    for idx, row in enumerate(rows, start=1):
        ExtractedUseCase.objects.create(
            job=job, order=idx,
            jira_ticket=clean_jira_ticket(row.get('jira_ticket')),
            **{field: row.get(field, '') or '' for field in TEXT_FIELDS},
        )
    return len(rows)
