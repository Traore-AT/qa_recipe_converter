"""
Synchronisation bidirectionnelle des anomalies avec Jira Cloud (API REST v3).

Application → Jira : création du ticket, titre, description, priorité, statut, commentaires.
Jira → application : webhook signé (HMAC) et/ou synchronisation planifiée (`sync_jira`).

Règles :
- les secrets viennent de l'environnement ; l'activation par projet est réservée aux super-admins ;
- une modification locale non transmise (jira_sync_error) est prioritaire sur l'état distant ;
- les tickets créés ici portent l'étiquette `qa-recipe`, ce qui évite leur réimport.
"""
import base64
import json
import logging
import re
import urllib.error
import urllib.request
from urllib.parse import quote

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.background import run_in_background
from .models import ActivityLog, Defect, DefectComment, ProjectQASettings
from .notifications import alert_if_critical, project_url
from .services import record_defect_changes, snapshot_defect

logger = logging.getLogger(__name__)

TIMEOUT = 15
LABEL = 'qa-recipe'
COMMENT_MARK = '[QA Recipe] '
ISSUE_KEY_RE = re.compile(r'^[A-Z][A-Z0-9_]+-\d+$')

PRIORITY_TO_JIRA = {'high': 'High', 'medium': 'Medium', 'low': 'Low'}
PRIORITY_FROM_JIRA = {'highest': 'high', 'high': 'high', 'medium': 'medium', 'low': 'low', 'lowest': 'low'}
CATEGORY_OF = {'open': 'new', 'reopened': 'new', 'in_progress': 'indeterminate', 'resolved': 'done', 'closed': 'done'}


class JiraError(Exception):
    """Échec d'un appel Jira (message affichable)."""


def is_configured():
    return bool(settings.JIRA_BASE_URL and settings.JIRA_EMAIL and settings.JIRA_API_TOKEN)


def issue_url(key):
    return f'{settings.JIRA_BASE_URL.rstrip("/")}/browse/{key}' if key and settings.JIRA_BASE_URL else None


def is_enabled(project):
    return is_configured() and ProjectQASettings.for_project(project).jira_active


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────

def api(method, path, payload=None):
    token = base64.b64encode(f'{settings.JIRA_EMAIL}:{settings.JIRA_API_TOKEN}'.encode()).decode()
    request = urllib.request.Request(
        f'{settings.JIRA_BASE_URL.rstrip("/")}/rest/api/3/{path}', method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': f'Basic {token}', 'Accept': 'application/json', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raise JiraError(_error_message(exc)) from exc
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise JiraError('Jira est injoignable.') from exc


def _error_message(exc):
    if exc.code in (401, 403):
        return f'Jira a refusé l’accès (HTTP {exc.code}) : vérifiez le compte technique et ses droits.'
    try:
        data = json.loads(exc.read())
        details = [*data.get('errorMessages', []), *(f'{k} : {v}' for k, v in (data.get('errors') or {}).items())]
    except (ValueError, AttributeError, TypeError):
        details = []
    return f'Jira (HTTP {exc.code}) : ' + ('; '.join(details)[:300] or 'erreur')


# ─────────────────────────────────────────────────────────────────────────────
# Conversions
# ─────────────────────────────────────────────────────────────────────────────

def to_adf(text):
    """Texte brut → Atlassian Document Format."""
    paragraphs = []
    for line in (text or '').split('\n'):
        paragraphs.append({'type': 'paragraph', 'content': [{'type': 'text', 'text': line}] if line else []})
    return {'type': 'doc', 'version': 1, 'content': paragraphs}


def from_adf(node):
    """ADF (ou texte) → texte brut."""
    if node is None:
        return ''
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return ''.join(from_adf(n) for n in node)
    if not isinstance(node, dict):
        return ''
    if node.get('type') == 'text':
        return node.get('text', '')
    if node.get('type') == 'hardBreak':
        return '\n'
    attrs = node.get('attrs') or {}
    inner = from_adf(node.get('content') or []) or (attrs.get('text', '') if node.get('type') == 'mention' else '')
    if node.get('type') in ('paragraph', 'heading', 'listItem', 'codeBlock', 'blockquote'):
        return inner.rstrip('\n') + '\n'
    return inner.strip('\n') if node.get('type') == 'doc' else inner


def _description(defect):
    parts = [defect.description]
    for label, value in (('Étapes de reproduction', defect.steps_to_reproduce),
                         ('Comportement attendu', defect.expected_behavior),
                         ('Comportement observé', defect.actual_behavior),
                         ('Environnement', defect.environment), ('Résolution', defect.resolution)):
        if value:
            parts.append(f'{label} :\n{value}')
    context = [f'Sévérité : {defect.get_severity_display()}']
    if defect.use_case:
        context.append(f'Cas de test : CAS-{defect.use_case.case_number:03d}')
    if defect.sprint:
        context.append(f'Sprint : {defect.sprint.name}')
    if defect.is_regression:
        context.append('Régression : oui')
    parts.append('\n'.join(context))
    parts.append(f'{defect.ticket_number} — {project_url(defect.project, "/defects")}')
    return '\n\n'.join(p for p in parts if p)


def _fields(defect, with_priority=True):
    fields = {'summary': ' '.join(defect.title.split())[:255], 'description': to_adf(_description(defect))}
    if with_priority:
        fields['priority'] = {'name': PRIORITY_TO_JIRA[defect.priority]}
    return fields


# ─────────────────────────────────────────────────────────────────────────────
# Application → Jira
# ─────────────────────────────────────────────────────────────────────────────

def queue_push(defect):
    if is_enabled(defect.project):
        run_in_background(push_defect, defect.pk)


def queue_comment(comment):
    if comment.defect.jira_key and is_enabled(comment.defect.project):
        run_in_background(push_comment, comment.pk)


def push_defect(defect_id):
    """Crée ou met à jour le ticket. Retourne True si tout a été transmis ; l'erreur est conservée sinon."""
    with transaction.atomic():
        # Verrou : deux envois simultanés ne doivent pas créer deux tickets.
        defect = Defect.objects.select_for_update(of=('self',)).select_related(
            'project__team', 'sprint', 'use_case').get(pk=defect_id)
        config = ProjectQASettings.for_project(defect.project)
        if not (is_configured() and config.jira_active):
            return False
        error = ''
        try:
            if defect.jira_key:
                _update_issue(defect)
            else:
                _create_issue(defect, config)
            _push_status(defect)
        except JiraError as exc:
            error = str(exc)
            logger.warning('Jira : envoi de %s impossible (%s)', defect.ticket_number, error)
        # update() : ne touche ni updated_at ni les champs modifiés entre-temps.
        Defect.objects.filter(pk=defect.pk).update(
            jira_key=defect.jira_key, jira_sync_error=error[:500],
            **({} if error else {'jira_synced_at': timezone.now()}))
    return not error


def _with_priority_fallback(call, defect):
    """Certains projets Jira n'exposent pas le champ priorité : nouvel essai sans lui."""
    try:
        return call(_fields(defect))
    except JiraError as exc:
        if 'priority' not in str(exc).lower():
            raise
        return call(_fields(defect, with_priority=False))


def _create_issue(defect, config):
    def create(fields):
        return api('POST', 'issue', {'fields': {
            **fields, 'project': {'key': config.jira_project_key}, 'issuetype': {'name': config.jira_issue_type},
            'labels': [LABEL, defect.ticket_number]}})
    key = _with_priority_fallback(create, defect).get('key', '')
    if not ISSUE_KEY_RE.match(key):
        raise JiraError('Réponse Jira inattendue à la création du ticket.')
    defect.jira_key = key
    case_key = (defect.use_case.jira_ticket if defect.use_case else '').strip().upper()
    if ISSUE_KEY_RE.match(case_key):
        try:
            api('POST', 'issueLink', {'type': {'name': 'Relates'}, 'inwardIssue': {'key': key}, 'outwardIssue': {'key': case_key}})
        except JiraError as exc:
            logger.info('Jira : lien %s → %s non créé (%s)', key, case_key, exc)


def _update_issue(defect):
    _with_priority_fallback(lambda fields: api('PUT', f'issue/{quote(defect.jira_key)}', {'fields': fields}), defect)


def _push_status(defect):
    wanted = CATEGORY_OF[defect.status]
    key = quote(defect.jira_key)
    if _category(api('GET', f'issue/{key}?fields=status').get('fields') or {}) == wanted:
        return
    for transition in api('GET', f'issue/{key}/transitions').get('transitions') or []:
        target = ((transition.get('to') or {}).get('statusCategory') or {}).get('key')
        if target == wanted:
            api('POST', f'issue/{key}/transitions', {'transition': {'id': transition['id']}})
            return
    raise JiraError(f'Aucune transition Jira vers « {defect.get_status_display()} » pour {defect.jira_key}.')


def push_comment(comment_id):
    comment = DefectComment.objects.select_related('defect', 'author').get(pk=comment_id)
    if comment.jira_comment_id or not comment.author_id:
        return
    name = comment.author.get_full_name() or comment.author.username
    try:
        created = api('POST', f'issue/{quote(comment.defect.jira_key)}/comment',
                      {'body': to_adf(f'{COMMENT_MARK}{name} : {comment.content}')})
    except JiraError as exc:
        logger.warning('Jira : commentaire non transmis (%s)', exc)
        Defect.objects.filter(pk=comment.defect_id).update(jira_sync_error=str(exc)[:500])
        return
    DefectComment.objects.filter(pk=comment.pk).update(jira_comment_id=str(created.get('id', '')))


# ─────────────────────────────────────────────────────────────────────────────
# Jira → application
# ─────────────────────────────────────────────────────────────────────────────

def _category(fields):
    return (((fields.get('status') or {}).get('statusCategory')) or {}).get('key')


def _local_status(defect, category):
    """Statut local correspondant à la catégorie Jira ; inchangé s'il y correspond déjà."""
    if category not in ('new', 'indeterminate', 'done') or CATEGORY_OF[defect.status] == category:
        return defect.status
    if category == 'done':
        return 'resolved'
    if category == 'indeterminate':
        return 'in_progress'
    return 'open' if defect.is_open else 'reopened'


def apply_issue(defect, issue):
    """Reporte l'état d'un ticket Jira sur l'anomalie. Retourne True si elle a changé."""
    fields = issue.get('fields') or {}
    with transaction.atomic():
        defect = Defect.objects.select_for_update(of=('self',)).select_related('project__team', 'sprint', 'use_case').get(pk=defect.pk)
        before = snapshot_defect(defect)
        was_alerting = defect.severity == 'critical' and defect.is_open
        title = ' '.join((fields.get('summary') or '').split())[:300]
        # Un titre local de plus de 255 caractères est tronqué par Jira : ce n'est pas une modification.
        if title and title != ' '.join(defect.title.split())[:255]:
            defect.title = title
        priority = PRIORITY_FROM_JIRA.get(((fields.get('priority') or {}).get('name') or '').lower())
        if priority:
            defect.priority = priority
        status = _local_status(defect, _category(fields))
        if status != defect.status:
            defect.apply_status(status)
        changed = snapshot_defect(defect) != before
        if changed:
            defect.jira_synced_at, defect.jira_sync_error = timezone.now(), ''
            defect.save()
            record_defect_changes(defect, before, None)
            log = ActivityLog.objects.create(
                project=defect.project, team=defect.project.team, actor=None, action_type='defect_jira_sync',
                description=f'Anomalie "{defect.title}" mise à jour depuis Jira ({defect.jira_key})')
            log.target_users.set([u for u in {defect.assigned_to, defect.reported_by} if u])
        else:
            Defect.objects.filter(pk=defect.pk).update(jira_synced_at=timezone.now())
    if changed:
        alert_if_critical(defect, was_alerting)
    return changed


def import_issue(config, issue):
    """Crée une anomalie depuis un ticket Jira inconnu. Retourne l'anomalie, ou None si le ticket est ignoré."""
    fields, key = issue.get('fields') or {}, issue.get('key') or ''
    labels = fields.get('labels') or []
    issue_type = (fields.get('issuetype') or {}).get('name') or ''
    if not ISSUE_KEY_RE.match(key) or LABEL in labels or issue_type.lower() != config.jira_issue_type.lower():
        return None
    with transaction.atomic():
        defect, created = Defect.objects.get_or_create(jira_key=key, defaults={
            'project': config.project,
            'title': ' '.join((fields.get('summary') or key).split())[:300],
            'description': from_adf(fields.get('description')),
            'priority': PRIORITY_FROM_JIRA.get(((fields.get('priority') or {}).get('name') or '').lower(), 'medium'),
            'jira_synced_at': timezone.now(),
        })
        if not created:
            return None
        status = _local_status(defect, _category(fields))
        if status != defect.status:
            defect.apply_status(status)
            defect.save()
        ActivityLog.objects.create(project=config.project, team=config.project.team, actor=None,
                                   action_type='defect_created', description=f'Anomalie importée de Jira ({key}) : {defect.title}')
    return defect


def apply_comment(defect, comment):
    """Ajoute un commentaire venu de Jira (idempotent ; ignore l'écho de nos propres commentaires)."""
    comment_id, text = str(comment.get('id') or ''), from_adf(comment.get('body')).strip()
    if not comment_id or not text or text.startswith(COMMENT_MARK.strip()):
        return False
    author = ((comment.get('author') or {}).get('displayName') or 'Jira')[:150]
    _, created = DefectComment.objects.get_or_create(
        defect=defect, jira_comment_id=comment_id, defaults={'external_author': f'{author} (Jira)', 'content': text})
    return created


def _config_for(issue):
    """Configuration active du projet Jira auquel appartient un ticket."""
    key = (issue.get('key') or '').rsplit('-', 1)[0]
    return ProjectQASettings.objects.filter(jira_enabled=True, jira_project_key=key).exclude(
        jira_project_key='').select_related('project__team').first()


def handle_webhook(payload):
    """Traite un événement Jira déjà authentifié. Retourne un libellé de résultat."""
    issue = payload.get('issue')
    if not isinstance(issue, dict):
        return 'ignored'
    config = _config_for(issue)
    if config is None:
        return 'ignored'
    event = payload.get('webhookEvent') or ''
    defect = Defect.objects.filter(project=config.project, jira_key=issue.get('key')).first()

    if event == 'jira:issue_deleted':
        if defect is None:
            return 'ignored'
        Defect.objects.filter(pk=defect.pk).update(jira_key=None, jira_sync_error='Ticket supprimé dans Jira.')
        return 'unlinked'
    if event == 'comment_created':
        if defect is None:
            return 'ignored'
        return 'commented' if apply_comment(defect, payload.get('comment') or {}) else 'ignored'
    if event not in ('jira:issue_created', 'jira:issue_updated'):
        return 'ignored'
    if defect is not None:
        if defect.jira_sync_error:
            queue_push(defect)  # la modification locale en attente est prioritaire
            return 'pending_push'
        return 'updated' if apply_issue(defect, issue) else 'unchanged'
    if config.jira_import:
        return 'imported' if import_issue(config, issue) else 'ignored'
    return 'ignored'


SYNC_FIELDS = ['summary', 'description', 'priority', 'status', 'labels', 'issuetype', 'comment']


def _search(jql):
    token = None
    while True:
        payload = {'jql': jql, 'fields': SYNC_FIELDS, 'maxResults': 100}
        if token:
            payload['nextPageToken'] = token
        page = api('POST', 'search/jql', payload)
        yield from page.get('issues') or []
        token = page.get('nextPageToken')
        if not token:
            return


def sync_project(project):
    """Synchronisation complète dans les deux sens. Lève JiraError si Jira est inaccessible."""
    config = ProjectQASettings.for_project(project)
    if not (is_configured() and config.jira_active):
        raise JiraError('La synchronisation Jira n’est pas activée pour ce projet.')
    result = {'pushed': 0, 'updated': 0, 'imported': 0, 'errors': 0}
    api('GET', f'project/{quote(config.jira_project_key)}')  # échoue tôt si la configuration est incorrecte

    pending = Defect.objects.filter(project=project)
    open_statuses = [s for s in Defect.Status.values if s not in Defect.DONE_STATUSES]
    to_push = set(pending.exclude(jira_sync_error='').values_list('pk', flat=True))
    to_push |= set(pending.filter(jira_key__isnull=True, status__in=open_statuses).values_list('pk', flat=True))
    for pk in to_push:
        result['pushed' if push_defect(pk) else 'errors'] += 1

    linked = {d.jira_key: d for d in pending.filter(jira_key__isnull=False, jira_sync_error='').exclude(pk__in=to_push)}
    keys = [k for k in linked if ISSUE_KEY_RE.match(k)]
    for i in range(0, len(keys), 50):
        for issue in _search(f'key in ({",".join(keys[i:i + 50])})'):
            if issue.get('key') in linked:
                defect = linked[issue['key']]
                result['updated'] += apply_issue(defect, issue)
                for comment in ((issue.get('fields') or {}).get('comment') or {}).get('comments') or []:
                    apply_comment(defect, comment)
    # Commentaires rédigés avant la liaison ou dont l'envoi avait échoué.
    for pk in DefectComment.objects.filter(defect__project=project, defect__jira_key__isnull=False,
                                           author__isnull=False, jira_comment_id='').values_list('pk', flat=True):
        push_comment(pk)
    if config.jira_import:
        issue_type = config.jira_issue_type.replace('\\', '\\\\').replace('"', '\\"')
        jql = (f'project = "{config.jira_project_key}" AND issuetype = "{issue_type}" AND statusCategory != Done '
               f'AND (labels is EMPTY OR labels != "{LABEL}")')
        for issue in _search(jql):
            if not Defect.objects.filter(jira_key=issue.get('key')).exists():
                result['imported'] += import_issue(config, issue) is not None
    return result
