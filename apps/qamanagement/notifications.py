"""Alertes e-mail (régressions, anomalies critiques) et envoi des rapports de sprint."""
from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify

from apps.core.background import run_in_background
from apps.teams.email import send_email
from apps.teams.models import ProjectMember, TeamMember
from apps.teams.permissions import can_view_project
from .models import Defect, ProjectQASettings, Sprint, UseCaseExecution

_pending = ContextVar('pending_regressions', default=None)


def project_recipients(project, extra=()):
    """
    Adresses des responsables : propriétaire et administrateurs de l'équipe, leads QA du projet,
    plus `extra` (ex. assigné). Comptes actifs, ayant accès au projet, sans doublon.
    """
    ids = set(TeamMember.objects.filter(team=project.team, role__in=('owner', 'admin')).values_list('user_id', flat=True))
    ids |= set(ProjectMember.objects.filter(project=project, role='lead').values_list('user_id', flat=True))
    ids |= {u.pk for u in extra if u}
    emails = {}
    for user in User.objects.filter(pk__in=ids, is_active=True).exclude(email='').order_by('pk'):
        if can_view_project(user, project):
            emails.setdefault(user.email.lower(), user.email)
    return list(emails.values())


def project_url(project, path=''):
    return f'{settings.FRONTEND_URL}/teams/{project.team.slug}/projects/{project.slug}{path}'


def _send(project, recipients, subject, title, intro, rows, url, label, attachments=()):
    if not recipients:
        return False
    context = {'title': title, 'intro': intro, 'rows': rows, 'url': url, 'button': label,
               'project': project, 'site_url': settings.SITE_URL}
    lines = [title, '', intro, '', *(f'- {k} : {v}' for k, v in rows), '', f'{label} : {url}']
    return send_email(f'[{project.name}] {subject}', '\n'.join(lines),
                      render_to_string('emails/qa_alert.html', context), recipients, attachments)


# ─────────────────────────────────────────────────────────────────────────────
# Régressions
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def grouped_alerts():
    """Regroupe en un seul e-mail par projet les régressions d'un traitement de masse."""
    if _pending.get() is not None:
        yield
        return
    collected = []
    token = _pending.set(collected)
    try:
        yield
    finally:
        _pending.reset(token)
    if collected:
        run_in_background(send_regression_alert, collected)


def queue_regression(execution):
    collected = _pending.get()
    if collected is not None:
        collected.append(execution.pk)
    else:
        run_in_background(send_regression_alert, [execution.pk])


def send_regression_alert(execution_ids):
    by_project = {}
    for e in UseCaseExecution.objects.filter(pk__in=execution_ids).select_related(
            'use_case', 'project__team', 'sprint', 'executed_by'):
        by_project.setdefault(e.project, []).append(e)
    for project, executions in by_project.items():
        if not ProjectQASettings.for_project(project).alert_on_regression:
            continue
        count = len(executions)
        rows = []
        for e in executions:
            who = (e.executed_by.get_full_name() or e.executed_by.username) if e.executed_by else 'Système'
            title = e.use_case.use_case_text or e.use_case.description
            rows.append((f'CAS-{e.use_case.case_number:03d}',
                         f'{title[:120]} — {e.sprint.name if e.sprint else "hors sprint"}, par {who}'))
        _send(project, project_recipients(project),
              f'{count} régression{"s" if count > 1 else ""} détectée{"s" if count > 1 else ""}',
              'Régression détectée', 'Ces cas de test étaient passés et échouent désormais :',
              rows, project_url(project, '/defects'), 'Voir les régressions')


# ─────────────────────────────────────────────────────────────────────────────
# Anomalies critiques
# ─────────────────────────────────────────────────────────────────────────────

def alert_if_critical(defect, was_critical_and_open=False):
    """À appeler après création/modification : alerte quand l'anomalie *devient* critique et active."""
    if defect.severity == Defect.Severity.CRITICAL and defect.is_open and not was_critical_and_open:
        run_in_background(send_critical_defect_alert, defect.pk)


def send_critical_defect_alert(defect_id):
    defect = Defect.objects.select_related('project__team', 'sprint', 'use_case', 'assigned_to', 'reported_by').get(pk=defect_id)
    project = defect.project
    if not ProjectQASettings.for_project(project).alert_on_critical_defect:
        return
    rows = [('Ticket', defect.ticket_number), ('Titre', defect.title),
            ('Priorité', defect.get_priority_display()), ('Statut', defect.get_status_display())]
    if defect.use_case:
        rows.append(('Cas de test', f'CAS-{defect.use_case.case_number:03d}'))
    if defect.sprint:
        rows.append(('Sprint', defect.sprint.name))
    if defect.reported_by:
        rows.append(('Signalée par', defect.reported_by.get_full_name() or defect.reported_by.username))
    _send(project, project_recipients(project, [defect.assigned_to]), f'Anomalie critique : {defect.title[:80]}',
          'Anomalie critique', 'Une anomalie critique requiert votre attention :',
          rows, project_url(project, '/defects'), 'Voir les anomalies')


# ─────────────────────────────────────────────────────────────────────────────
# Rapport de sprint
# ─────────────────────────────────────────────────────────────────────────────

def send_sprint_report(sprint_id, generated_by='Envoi automatique', automatic=True):
    from apps.teams.reports import generate_project_report
    sprint = Sprint.objects.select_related('project__team').get(pk=sprint_id)
    project = sprint.project
    if automatic and not ProjectQASettings.for_project(project).auto_report_on_close:
        return False
    recipients = project_recipients(project)
    if not recipients:
        return False
    stats = sprint.stats
    pdf = generate_project_report(project, [sprint], generated_by)
    filename = f'rapport-{project.slug}-{slugify(sprint.name) or "sprint"}.pdf'
    sent = _send(
        project, recipients, f'Rapport du sprint {sprint.name}', f'Rapport de sprint — {sprint.name}',
        f'Le sprint « {sprint.name} » est {sprint.get_status_display().lower()}. Son rapport complet est joint à cet e-mail.',
        [('Période', f'{sprint.start_date:%d/%m/%Y} au {sprint.end_date:%d/%m/%Y}'), ('Cas de test', stats['total']),
         ('Passés', stats['passed']), ('Échoués', stats['failed']), ('Avancement', f'{stats["progress_pct"]} %')],
        project_url(project, f'/sprints/{sprint.id}'), 'Ouvrir le sprint',
        [(filename, pdf, 'application/pdf')])
    if sent:
        Sprint.objects.filter(pk=sprint.pk).update(report_sent_at=timezone.now())
    return sent
