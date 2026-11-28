"""
Lecture d'un document Word envoyé par l'utilisateur, sans l'enregistrer : aperçu d'import
(outil de conversion) et import direct dans un sprint (Kanban).
"""
import os
import shutil
import tempfile
from contextlib import contextmanager

from apps.parser.docx_parser import DocxParser
from .doc_converter import convert_doc_to_docx, is_doc_file, is_libreoffice_available
from .use_cases import clean_jira_ticket, invalid_jira_keys

FIELD_LABELS = {
    'use_case_text': 'Use Case', 'description': 'Description', 'preconditions': 'Préconditions',
    'steps': 'Étapes', 'expected_results': 'Résultats attendus', 'observed_results': 'Résultats observés',
    'jira_ticket': 'Tickets Jira',
}
ANALYSIS_SAMPLE_SIZE = 20


class WordImportError(ValueError):
    """Document illisible (message destiné à l'utilisateur)."""


@contextmanager
def uploaded_docx(word_file):
    """Chemin local .docx d'un fichier envoyé (un .doc est converti si LibreOffice est disponible)."""
    suffix = os.path.splitext(word_file.name)[1].lower()
    work_dir = tempfile.mkdtemp(prefix='word_import_')
    try:
        path = os.path.join(work_dir, f'source{suffix}')
        with open(path, 'wb') as target:
            for chunk in word_file.chunks():
                target.write(chunk)
        if hasattr(word_file, 'seek'):
            word_file.seek(0)  # le fichier peut ensuite être enregistré tel quel
        if not is_doc_file(path):
            yield path
            return
        converted = convert_doc_to_docx(path) if is_libreoffice_available() else None
        if converted is None:
            raise WordImportError("Ce fichier .doc ne peut pas être lu : enregistrez-le au format .docx.")
        try:
            yield converted
        finally:
            shutil.rmtree(os.path.dirname(converted), ignore_errors=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def read_word_document(path):
    """(analyse, lignes) : ce que l'import extraira du document, sans rien enregistrer."""
    parser = DocxParser(path)
    rows = parser.extract_use_cases()
    for row in rows:
        row['jira_ticket'] = clean_jira_ticket(row.get('jira_ticket'))
    tables = [{**t, 'columns': [{**c, 'label': FIELD_LABELS.get(c['field'])} for c in t['columns']]}
              for t in parser.tables_report]
    detected = {c['field'] for t in tables for c in t['columns'] if c['field']}
    invalid = [{'row': i, 'ticket': key} for i, row in enumerate(rows, start=1)
               for key in invalid_jira_keys(row.get('jira_ticket'))]
    warnings = []
    if 'jira_ticket' not in detected:
        warnings.append("Aucune colonne « Tickets Jira » reconnue : les numéros Jira ne seront pas importés.")
    if invalid:
        warnings.append(f'{len(invalid)} référence(s) Jira au format inattendu (attendu : PROJET-123).')
    if not rows:
        warnings.append('Aucun cas de test détecté : vérifiez les en-têtes des tableaux.')
    analysis = {
        'tables': tables,
        'total_cases': len(rows),
        'fields': sorted(detected, key=list(FIELD_LABELS).index),
        'jira_column': 'jira_ticket' in detected,
        'with_jira': sum(1 for r in rows if r.get('jira_ticket')),
        'invalid_jira': invalid,
        'warnings': warnings,
        'sample': [{f: r.get(f, '') for f in FIELD_LABELS} for r in rows[:ANALYSIS_SAMPLE_SIZE]],
    }
    return analysis, rows


def analyse_word_document(path):
    return read_word_document(path)[0]
