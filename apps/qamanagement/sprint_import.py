"""
Import de cas de test directement dans un sprint (Kanban), à la manière de l'import de tickets Jira.

Règles :
- les cas importés appartiennent à ce sprint : ils ne sont jamais proposés ni acceptés dans un autre
  sprint (seul un report explicite « Reporter les cas » les partage) ;
- un cas déjà présent dans le projet (même intitulé et même ticket Jira) n'est pas recréé, de même
  qu'une ligne répétée dans le document : ré-importer un document ne crée pas de doublons ;
- tout est créé en une seule transaction : un échec n'enregistre rien.
"""
import re
import unicodedata

from django.db import transaction

from apps.core.models import ConversionJob, ExtractedUseCase
from apps.core.use_cases import save_extracted_use_cases, split_jira_keys
from .models import UseCaseAssignment
from .services import assign_cases


class SprintImportError(Exception):
    """Import impossible (message destiné à l'utilisateur)."""


def _title(row):
    return (row.get('use_case_text') or row.get('description') or '').strip()


def _fingerprint(title, jira):
    """Identité d'un cas : intitulé normalisé + ensemble de ses tickets Jira."""
    text = ''.join(c for c in unicodedata.normalize('NFD', title.lower()) if unicodedata.category(c) != 'Mn')
    words = ' '.join(re.sub(r'[^a-z0-9]+', ' ', text).split())
    return (words, frozenset(k.upper() for k in split_jira_keys(jira))) if words else None


def plan_rows(project, rows):
    """Chaque ligne du document avec son éventuel doublon (cas existant ou ligne précédente)."""
    existing = {}
    for case in ExtractedUseCase.objects.filter(project=project).order_by('case_number'):
        key = _fingerprint(case.use_case_text or case.description, case.jira_ticket)
        if key:
            existing.setdefault(key, case.case_number)
    seen, plan = {}, []
    for index, row in enumerate(rows, start=1):
        key = _fingerprint(_title(row), row.get('jira_ticket'))
        entry = {'row': index, 'title': _title(row), 'jira_ticket': row.get('jira_ticket', ''),
                 'duplicate_of': None, 'duplicate_row': None}
        if key in existing:
            entry['duplicate_of'] = existing[key]
        elif key in seen:
            entry['duplicate_row'] = seen[key]
        elif key:
            seen[key] = index
        plan.append(entry)
    return plan


def import_into_sprint(project, sprint, user, word_file, rows, members):
    """Crée la recette, ses nouveaux cas et leurs affectations dans `sprint`. Retourne le résumé."""
    if sprint.is_frozen:
        raise SprintImportError("Ce sprint est terminé : rouvrez-le pour y importer des cas.")
    plan = plan_rows(project, rows)
    keep = [row for row, entry in zip(rows, plan) if not (entry['duplicate_of'] or entry['duplicate_row'])]
    if not keep:
        raise SprintImportError('Aucun nouveau cas à importer : tous les cas du document existent déjà dans le projet.')

    job = None
    try:
        with transaction.atomic():
            job = ConversionJob.objects.create(
                source_filename=word_file.name, word_file=word_file, project=project, sprint=sprint,
                uploaded_by=user, status=ConversionJob.Status.DONE)
            save_extracted_use_cases(job, keep)
            cases = list(job.use_cases.order_by('order'))
            job.use_cases_count = len(cases)
            job.save(update_fields=['use_cases_count'])
            if members:
                assignments, _, received = assign_cases(sprint, members, cases, user)
            else:
                assignments = UseCaseAssignment.objects.bulk_create([
                    UseCaseAssignment(use_case=case, sprint=sprint, assigned_by=user, status=case.status)
                    for case in cases])
                received = {}
    except Exception:
        if job is not None and job.word_file:
            job.word_file.storage.delete(job.word_file.name)  # pas de fichier orphelin si la transaction échoue
        raise

    return {
        'job': job,
        'cases': cases,
        'assignments': assignments,
        'skipped': [e for e in plan if e['duplicate_of'] or e['duplicate_row']],
        'distribution': [{'user_id': pm.user_id, 'username': pm.user.username,
                          'full_name': pm.user.get_full_name() or pm.user.username,
                          'count': received.get(pm.pk, 0)} for pm in members],
    }
