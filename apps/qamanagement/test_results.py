"""
Import des résultats de tests automatisés (Cypress et compatibles).

Formats reconnus : JUnit XML, Mochawesome JSON, Mocha JSON, résultat de l'API Cypress
(`cypress.run()`), Cucumber JSON. Un cas est reconnu par son numéro (CAS-001) dans le titre,
à défaut par son ticket Jira, puis par son intitulé exact.
"""
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass

from django.db import transaction

from apps.core.models import ExtractedUseCase
from .models import Defect, UseCaseAssignment, UseCaseComment
from .notifications import alert_if_critical, grouped_alerts

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TESTS = 20_000
MAX_MESSAGE = 1500

CASE_RE = re.compile(r'\bCAS-0*(\d{1,9})\b', re.IGNORECASE)
JIRA_RE = re.compile(r'\b([A-Z][A-Z0-9_]+-\d+)\b')
ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


class ResultsError(Exception):
    """Fichier illisible ou non reconnu (message destiné à l'utilisateur)."""


@dataclass
class TestResult:
    __test__ = False  # n'est pas une classe de test pytest
    title: str
    status: str  # passed | failed | skipped
    message: str = ''


# ─────────────────────────────────────────────────────────────────────────────
# Lecture
# ─────────────────────────────────────────────────────────────────────────────

def _text(value):
    if isinstance(value, dict):
        value = value.get('message') or value.get('estack') or value.get('stack') or ''
    return ANSI_RE.sub('', str(value or '')).strip()[:MAX_MESSAGE]


def _state(raw, has_error=False):
    raw = str(raw or '').lower()
    if raw in ('passed', 'pass'):
        return 'passed'
    if raw in ('failed', 'fail', 'error') or has_error:
        return 'failed'
    return 'skipped'


def _parse_junit(content):
    # Séquences couleur et caractères de contrôle, interdits en XML mais présents dans certains rapports.
    content = re.sub(rb'\x1b\[[0-9;]*m|[\x00-\x08\x0b\x0c\x0e-\x1f]', b'', content)
    # Aucun rapport légitime ne contient de DTD : on écarte les attaques par entités.
    if b'<!DOCTYPE' in content or b'<!ENTITY' in content:
        raise ResultsError('XML refusé : déclaration DOCTYPE/ENTITY interdite.')
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise ResultsError('Fichier XML invalide.')
    if root.tag not in ('testsuites', 'testsuite'):
        raise ResultsError('XML non reconnu : rapport JUnit attendu.')
    results = []

    def walk(node, suites):
        names = suites + ([node.get('name')] if node.tag == 'testsuite' and node.get('name') else [])
        for child in node:
            if child.tag == 'testcase':
                problem = child.find('failure')
                problem = problem if problem is not None else child.find('error')
                state = 'failed' if problem is not None else 'skipped' if child.find('skipped') is not None else 'passed'
                message = _text(problem.get('message') or problem.text) if problem is not None else ''
                title = ' '.join([*names, child.get('classname') or '', child.get('name') or ''])
                results.append(TestResult(title, state, message))
            elif child.tag in ('testsuite', 'testsuites'):
                walk(child, names)

    walk(root, [])
    return results


def _parse_mochawesome(data):
    results = []

    def walk(suite, parents):
        names = parents + [suite.get('title') or '']
        for t in suite.get('tests') or []:
            state = t.get('state') or ('passed' if t.get('pass') else 'failed' if t.get('fail') else 'pending')
            title = t.get('fullTitle') or ' '.join([*names, t.get('title') or ''])
            results.append(TestResult(title, _state(state), _text(t.get('err'))))
        for child in suite.get('suites') or []:
            walk(child, names)

    for root in data['results']:
        walk(root, [])
    return results


def _parse_mocha(data):
    pending = {t.get('fullTitle') for t in data.get('pending') or []}
    failed = {t.get('fullTitle') for t in data.get('failures') or []}
    results = []
    for t in data['tests']:
        full = t.get('fullTitle') or t.get('title') or ''
        error = t.get('err') or {}
        state = 'skipped' if full in pending else 'failed' if error or full in failed else 'passed'
        results.append(TestResult(full, state, _text(error)))
    return results


def _parse_cypress_run(data):
    return [
        TestResult(' '.join(t['title']) if isinstance(t.get('title'), list) else str(t.get('title') or ''),
                   _state(t.get('state')), _text(t.get('displayError')))
        for run in data['runs'] for t in run.get('tests') or []
    ]


def _parse_cucumber(features):
    results = []
    for feature in features:
        for scenario in feature.get('elements') or []:
            if scenario.get('type') == 'background':
                continue
            steps = [s.get('result') or {} for s in scenario.get('steps') or []]
            failure = next((s for s in steps if s.get('status') == 'failed'), None)
            if failure is not None:
                state = 'failed'
            else:
                state = 'passed' if steps and all(s.get('status') == 'passed' for s in steps) else 'skipped'
            # Les étiquettes de la Feature (@CAS-001) ne valent que si elles désignent ce scénario.
            tags = ' '.join(t.get('name', '') for t in [*(scenario.get('tags') or []), *(feature.get('tags') or [])])
            title = f'{feature.get("name", "")} {scenario.get("name", "")} {tags}'
            results.append(TestResult(title, state, _text((failure or {}).get('error_message'))))
    return results


def parse_results(name, content):
    if len(content) > MAX_FILE_BYTES:
        raise ResultsError(f'{name} : fichier trop volumineux (10 Mo maximum).')
    stripped = content.lstrip(b'\xef\xbb\xbf \t\r\n')
    try:
        if stripped.startswith(b'<'):
            results = _parse_junit(stripped)
        else:
            try:
                data = json.loads(stripped.decode('utf-8'))
            except (UnicodeDecodeError, ValueError, RecursionError):
                raise ResultsError('JSON invalide.')
            if isinstance(data, list) and all(isinstance(f, dict) and 'elements' in f for f in data):
                results = _parse_cucumber(data)
            elif isinstance(data, dict) and isinstance(data.get('results'), list):
                results = _parse_mochawesome(data)
            elif isinstance(data, dict) and isinstance(data.get('runs'), list):
                results = _parse_cypress_run(data)
            elif isinstance(data, dict) and isinstance(data.get('tests'), list):
                results = _parse_mocha(data)
            else:
                raise ResultsError('Format non reconnu (JUnit, Mochawesome, Mocha, Cypress ou Cucumber attendu).')
    except ResultsError as exc:
        raise ResultsError(f'{name} : {exc}')
    except (AttributeError, TypeError, KeyError, RecursionError):
        raise ResultsError(f'{name} : structure de rapport inattendue.')
    if len(results) > MAX_TESTS:
        raise ResultsError(f'{name} : trop de tests ({len(results)}).')
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Rapprochement et application
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(text):
    return ' '.join((text or '').lower().split())


class CaseMatcher:
    def __init__(self, project):
        self.by_number, by_jira, by_title = {}, defaultdict(list), defaultdict(list)
        for case in ExtractedUseCase.objects.filter(project=project):
            self.by_number[case.case_number] = case
            if case.jira_ticket:
                by_jira[case.jira_ticket.strip().upper()].append(case)
            if case.use_case_text:
                by_title[_normalize(case.use_case_text)].append(case)
        # Un identifiant ambigu ne doit jamais modifier le mauvais cas.
        self.by_jira = {k: v[0] for k, v in by_jira.items() if len(v) == 1}
        self.by_title = {k: v[0] for k, v in by_title.items() if len(v) == 1}

    def match(self, title):
        numbers = {int(n) for n in CASE_RE.findall(title)}
        if numbers:
            return self.by_number.get(numbers.pop()) if len(numbers) == 1 else None
        for key in JIRA_RE.findall(title):
            if key in self.by_jira:
                return self.by_jira[key]
        normalized = _normalize(title)
        if normalized in self.by_title:
            return self.by_title[normalized]
        # Titre complet « Suite … Intitulé » : l'intitulé du cas en est la fin.
        endings = [c for t, c in self.by_title.items() if len(t) >= 8 and normalized.endswith(' ' + t)]
        return max(endings, key=lambda c: len(c.use_case_text)) if endings else None


def build_plan(project, results, sprint=None):
    """Associe les tests aux cas. Plusieurs tests pour un cas : un seul échec suffit à l'échouer."""
    matcher = CaseMatcher(project)
    in_sprint = set(sprint.assignments.values_list('use_case_id', flat=True)) if sprint else None
    grouped, unmatched, outside, skipped = {}, [], [], 0
    for result in results:
        case = matcher.match(result.title)
        if case is None:
            unmatched.append(' '.join(result.title.split())[:200])
        elif in_sprint is not None and case.id not in in_sprint:
            outside.append(case.case_number)
        elif result.status == 'skipped':
            skipped += 1
        else:
            entry = grouped.setdefault(case.id, {'case': case, 'tests': 0, 'failures': []})
            entry['tests'] += 1
            if result.status == 'failed':
                entry['failures'].append(result.message or 'Échec sans message')
    plan = sorted(grouped.values(), key=lambda e: e['case'].case_number)
    for entry in plan:
        entry['new_status'] = 'Échoué' if entry['failures'] else 'Passé'
    return {'plan': plan, 'unmatched': unmatched, 'outside_sprint': sorted(set(outside)),
            'skipped': skipped, 'total': len(results)}


def apply_plan(project, plan, user, sprint=None, create_defects=False):
    updated = defects = 0
    created = []
    with grouped_alerts(), transaction.atomic():
        for entry in plan:
            case = ExtractedUseCase.objects.select_for_update().get(pk=entry['case'].pk)
            updated += case.status != entry['new_status']
            case.status = entry['new_status']
            case.is_automated = True
            case._status_actor, case._status_sprint = user, sprint
            case.save(update_fields=['status', 'is_automated'])
            if not entry['failures']:
                continue
            message = '\n'.join(dict.fromkeys(entry['failures']))[:MAX_MESSAGE]
            content = f'[Test automatisé] {message}'
            last = UseCaseComment.objects.filter(use_case=case).order_by('-created_at').first()
            if last is None or last.content != content:
                UseCaseComment.objects.create(use_case=case, author=user, content=content)
            if create_defects and not _has_open_defect(case, sprint):
                # L'anomalie n'est rattachée au sprint que si le cas y figure.
                linked = sprint if sprint and UseCaseAssignment.objects.filter(sprint=sprint, use_case=case).exists() else None
                title = f'CAS-{case.case_number:03d} — échec du test automatisé : {case.use_case_text or case.description}'
                created.append(Defect.objects.create(
                    project=project, use_case=case, sprint=linked, reported_by=user, title=title[:300],
                    description='Créée automatiquement lors de l’import des résultats de tests.',
                    actual_behavior=message, expected_behavior=case.expected_results,
                    steps_to_reproduce=case.steps,
                    is_regression=case.executions.filter(is_regression=True, new_status='Échoué').exists()))
                defects += 1
    for defect in created:
        alert_if_critical(defect)
    return {'updated': updated, 'defects_created': created}


def _has_open_defect(case, sprint):
    open_statuses = [s for s in Defect.Status.values if s not in Defect.DONE_STATUSES]
    return (case.defects.filter(status__in=open_statuses).exists()
            or case.related_defects.filter(status__in=open_statuses).exists())
