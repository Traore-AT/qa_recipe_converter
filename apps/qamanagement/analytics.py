"""
Indicateurs QA d'un projet, d'un sprint ou d'une sélection de sprints :
statistiques, niveau de performance, courbe d'évolution, régressions, rapport complet.

Tous les calculs acceptent `sprints` : None = projet entier, sinon liste de Sprint.
"""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from apps.core.models import ExtractedUseCase
from .models import CASE_STATUSES, Defect, Sprint, UseCaseExecution

CONCLUSIVE = ('Passé', 'Échoué')
SEVERITIES = ('critical', 'major', 'minor', 'trivial')
PRIORITIES = ('high', 'medium', 'low')

# Pondération du score de performance (total = 100).
SCORE_WEIGHTS = {'pass_rate': 40, 'execution_rate': 25, 'resolution_rate': 20, 'stability_rate': 15}
PERFORMANCE_LEVELS = [
    (85, 'excellent', 'Excellent'),
    (70, 'good', 'Bon'),
    (50, 'average', 'Moyen'),
    (0, 'critical', 'Critique'),
]

# Au-delà de ce nombre de jours, la courbe passe en pas hebdomadaire.
MAX_DAILY_POINTS = 62


def _pct(part, whole):
    return round(part / whole * 100, 1) if whole else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Périmètre
# ─────────────────────────────────────────────────────────────────────────────

def scope_case_statuses(project, sprints=None):
    """{id du cas: statut} pour le périmètre. Entre plusieurs sprints, le plus récent l'emporte."""
    if sprints is None:
        return dict(ExtractedUseCase.objects.filter(project=project).values_list('id', 'status'))
    result = {}
    for sprint in sorted(sprints, key=lambda s: (s.start_date, s.created_at)):
        result.update(sprint.case_statuses())
    return result


def scope_defects(project, sprints=None):
    defects = Defect.objects.filter(project=project)
    return defects if sprints is None else defects.filter(sprint__in=sprints)


def scope_executions(project, sprints=None):
    executions = UseCaseExecution.objects.filter(project=project)
    return executions if sprints is None else executions.filter(sprint__in=sprints)


# ─────────────────────────────────────────────────────────────────────────────
# Statistiques
# ─────────────────────────────────────────────────────────────────────────────

def summarize_statuses(statuses):
    counts = Counter(statuses)
    total = sum(counts.values())
    passed, failed, blocked = counts['Passé'], counts['Échoué'], counts['Bloqué']
    executed = passed + failed + blocked
    return {
        'total': total,
        'passed': passed,
        'failed': failed,
        'blocked': blocked,
        'in_progress': counts['En cours'],
        'not_run': counts['À tester'],
        'executed': executed,
        'execution_rate': _pct(executed, total),
        'success_rate': _pct(passed, total),
        'pass_rate': _pct(passed, executed),
    }


def defect_stats(defects):
    rows = list(defects.values('status', 'severity', 'priority', 'is_regression', 'reopen_count',
                               'created_at', 'resolved_at'))
    by_status = Counter(r['status'] for r in rows)
    by_severity = Counter(r['severity'] for r in rows)
    by_priority = Counter(r['priority'] for r in rows)
    total = len(rows)
    done = by_status['resolved'] + by_status['closed']
    durations = [(r['resolved_at'] - r['created_at']).total_seconds() / 86400 for r in rows if r['resolved_at']]
    return {
        'total': total,
        'open': by_status['open'],
        'in_progress': by_status['in_progress'],
        'resolved': by_status['resolved'],
        'closed': by_status['closed'],
        'reopened': by_status['reopened'],
        'active': total - done,
        'critical': by_severity['critical'],
        'major': by_severity['major'],
        'minor': by_severity['minor'],
        'trivial': by_severity['trivial'],
        'regressions': sum(1 for r in rows if r['is_regression']),
        'reopen_total': sum(r['reopen_count'] for r in rows),
        'resolution_rate': _pct(done, total),
        'avg_resolution_days': round(sum(durations) / len(durations), 1) if durations else None,
        'by_severity': {s: by_severity[s] for s in SEVERITIES},
        'by_priority': {p: by_priority[p] for p in PRIORITIES},
        'by_status': {s: by_status[s] for s in Defect.Status.values},
    }


def project_performance(project, sprints=None):
    """Niveau de performance des tests : score pondéré /100 et composantes."""
    statuses = scope_case_statuses(project, sprints)
    stats = summarize_statuses(statuses.values())
    defects = defect_stats(scope_defects(project, sprints))
    regressed = (scope_executions(project, sprints).filter(is_regression=True, use_case_id__in=statuses)
                 .values('use_case_id').distinct().count())
    automated = ExtractedUseCase.objects.filter(id__in=statuses, is_automated=True).count()

    components = {
        'pass_rate': stats['pass_rate'],
        'execution_rate': stats['execution_rate'],
        # Sans anomalie à traiter, la composante n'est pas pénalisante.
        'resolution_rate': defects['resolution_rate'] if defects['total'] else 100.0,
        'stability_rate': max(0.0, round(100 - _pct(regressed, stats['executed']), 1)) if stats['executed'] else 100.0,
    }
    if stats['executed']:
        score = round(sum(components[k] * w for k, w in SCORE_WEIGHTS.items()) / 100, 1)
        level, label = next((code, text) for floor, code, text in PERFORMANCE_LEVELS if score >= floor)
    else:
        score, level, label = None, 'not_evaluated', 'Non évalué'

    return {
        'score': score,
        'level': level,
        'level_label': label,
        'components': components,
        'weights': SCORE_WEIGHTS,
        'stats': stats,
        'defects': defects,
        'regressed_cases': regressed,
        'automation_rate': _pct(automated, stats['total']),
        'automated': automated,
        'defect_density': round(defects['total'] / stats['executed'], 2) if stats['executed'] else 0.0,
    }


def sprint_summary(project, sprint):
    perf = project_performance(project, [sprint])
    return {
        'id': str(sprint.id),
        'name': sprint.name,
        'goal': sprint.goal,
        'status': sprint.status,
        'status_label': sprint.get_status_display(),
        'start_date': sprint.start_date.isoformat(),
        'end_date': sprint.end_date.isoformat(),
        'duration_days': sprint.duration_days,
        'stats': perf['stats'],
        'defects': perf['defects'],
        'regressed_cases': perf['regressed_cases'],
        'score': perf['score'],
        'level': perf['level'],
        'level_label': perf['level_label'],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Courbe d'évolution
# ─────────────────────────────────────────────────────────────────────────────

def _bounds(project, cases, executions, sprints, today):
    if sprints is not None:
        if not sprints:
            return today, today
        start = min(s.start_date for s in sprints)
        end = min(max(s.end_date for s in sprints), today)
        # Sprint non terminé mais dont l'échéance est dépassée : on suit jusqu'à aujourd'hui.
        if any(not s.is_frozen and s.end_date < today for s in sprints):
            end = today
        return min(start, end), end
    candidates = [timezone.localtime(c['created_at']).date() for c in cases.values() if c['created_at']]
    candidates += [timezone.localtime(e['executed_at']).date() for e in executions[:1]]
    return min(candidates, default=today), today


def project_evolution(project, sprints=None):
    """
    Reconstitue, jour par jour (ou semaine par semaine), l'état des cas et des anomalies
    à partir de l'historique d'exécution. Le dernier point d'une période en cours
    correspond toujours à l'état réel affiché ailleurs dans l'application.
    """
    today = timezone.localdate()
    current = scope_case_statuses(project, sprints)
    cases = {c['id']: c for c in ExtractedUseCase.objects.filter(id__in=current).values('id', 'created_at')}
    executions = list(UseCaseExecution.objects.filter(use_case_id__in=current).order_by('executed_at')
                      .values('use_case_id', 'old_status', 'new_status', 'executed_at', 'is_regression', 'sprint_id'))
    defects = list(scope_defects(project, sprints).values('created_at', 'resolved_at'))
    start, end = _bounds(project, cases, executions, sprints, today)

    days = (end - start).days
    step = 1 if days <= MAX_DAILY_POINTS else 7
    dates = [start + timedelta(days=i) for i in range(0, days + 1, step)]
    if dates[-1] != end:
        dates.append(end)

    first_status = {}
    for e in executions:
        if e['use_case_id'] not in first_status:
            first_status[e['use_case_id']] = e['old_status'] or 'À tester'
            # Un cas existe au plus tard à sa première exécution.
            case = cases[e['use_case_id']]
            if case['created_at'] and e['executed_at'] < case['created_at']:
                case['created_at'] = e['executed_at']
    state = {cid: first_status.get(cid, current[cid]) for cid in current}
    sprint_ids = None if sprints is None else {s.id for s in sprints}

    points, index, regressions, executed_count = [], 0, 0, 0
    tz = timezone.get_current_timezone()
    for day in dates:
        limit = timezone.make_aware(datetime.combine(day, time.max), tz)
        while index < len(executions) and executions[index]['executed_at'] <= limit:
            e = executions[index]
            state[e['use_case_id']] = e['new_status']
            if sprint_ids is None or e['sprint_id'] in sprint_ids:
                executed_count += 1
                regressions += int(e['is_regression'])
            index += 1
        if day == end:
            # Fige la fin de courbe sur l'état de référence (résultats figés inclus).
            visible = dict(current)
        elif sprints is not None:
            visible = state
        else:
            visible = {cid: st for cid, st in state.items()
                       if not cases[cid]['created_at'] or cases[cid]['created_at'] <= limit}
        summary = summarize_statuses(visible.values())
        created = sum(1 for d in defects if d['created_at'] <= limit)
        resolved = sum(1 for d in defects if d['resolved_at'] and d['resolved_at'] <= limit)
        points.append({
            'date': day.isoformat(),
            **{k: summary[k] for k in ('total', 'passed', 'failed', 'blocked', 'in_progress', 'not_run',
                                       'executed', 'success_rate', 'execution_rate', 'pass_rate')},
            'executions': executed_count,
            'regressions': regressions,
            'defects_created': created,
            'defects_resolved': resolved,
            'defects_open': created - resolved,
        })

    trend = round(points[-1]['success_rate'] - points[0]['success_rate'], 1)
    return {
        'granularity': 'day' if step == 1 else 'week',
        'start': start.isoformat(),
        'end': end.isoformat(),
        'trend': trend,
        'points': points,
    }


def sprint_trend(project, sprints=None):
    """Comparaison sprint par sprint, dans l'ordre chronologique."""
    qs = Sprint.objects.filter(project=project) if sprints is None else sprints
    return [sprint_summary(project, s) for s in sorted(qs, key=lambda s: (s.start_date, s.created_at))]


# ─────────────────────────────────────────────────────────────────────────────
# Régressions
# ─────────────────────────────────────────────────────────────────────────────

def user_label(user):
    return (user.get_full_name() or user.username) if user else None


def list_regressions(project, sprints=None):
    """Cas ayant régressé (Passé → Échoué), avec leurs anomalies liées."""
    executions = (scope_executions(project, sprints).filter(is_regression=True)
                  .select_related('use_case', 'sprint', 'executed_by').order_by('-executed_at'))
    case_ids = {e.use_case_id for e in executions}
    linked = defaultdict(dict)
    for defect in (Defect.objects.filter(project=project)
                   .filter(Q(use_case_id__in=case_ids) | Q(related_use_cases__in=case_ids))
                   .distinct().prefetch_related('related_use_cases')):
        for cid in {defect.use_case_id, *(c.id for c in defect.related_use_cases.all())} & case_ids:
            linked[cid][defect.id] = {
                'id': str(defect.id), 'ticket_number': defect.ticket_number, 'title': defect.title,
                'status': defect.status, 'severity': defect.severity,
            }
    grouped = {}
    for e in executions:
        row = grouped.get(e.use_case_id)
        if row:
            row['occurrences'] += 1
            continue
        grouped[e.use_case_id] = {
            'use_case': str(e.use_case_id),
            'case_number': e.use_case.case_number,
            'title': e.use_case.use_case_text or e.use_case.description,
            'current_status': e.use_case.status,
            'is_active': e.use_case.status == 'Échoué',
            'occurrences': 1,
            'last_detected_at': e.executed_at.isoformat(),
            'detected_by': user_label(e.executed_by),
            'sprint': str(e.sprint_id) if e.sprint_id else None,
            'sprint_name': e.sprint.name if e.sprint else None,
            'defects': list(linked[e.use_case_id].values()),
        }
    return list(grouped.values())


# ─────────────────────────────────────────────────────────────────────────────
# Rapport complet (projet, sprint, plusieurs sprints)
# ─────────────────────────────────────────────────────────────────────────────

def member_workload(project, sprints=None):
    """Répartition des cas par membre, dédoublonnée par cas."""
    from .models import UseCaseAssignment
    qs = UseCaseAssignment.objects.filter(use_case__project=project).select_related(
        'assigned_to__user', 'use_case', 'sprint')
    if sprints is not None:
        qs = qs.filter(sprint__in=sprints)
    per_member = defaultdict(dict)
    for a in qs.order_by('assigned_at'):
        name = user_label(a.assigned_to.user) if a.assigned_to_id else 'Non assigné'
        per_member[name][a.use_case_id] = a.effective_status
    return [{'member': name, **summarize_statuses(cases.values())} for name, cases in sorted(per_member.items())]


def defect_row(d):
    """Ligne d'anomalie des rapports et exports."""
    return {
        'id': str(d.id),
        'ticket_number': d.ticket_number,
        'jira_key': d.jira_key,
        'title': d.title,
        'severity': d.severity, 'severity_label': d.get_severity_display(),
        'priority': d.priority, 'priority_label': d.get_priority_display(),
        'status': d.status, 'status_label': d.get_status_display(),
        'is_regression': d.is_regression,
        'case_numbers': sorted({c.case_number for c in d.related_use_cases.all()}
                               | ({d.use_case.case_number} if d.use_case else set())),
        'sprint_name': d.sprint.name if d.sprint else None,
        'assigned_to': user_label(d.assigned_to),
        'created_at': d.created_at.isoformat(),
        'resolved_at': d.resolved_at.isoformat() if d.resolved_at else None,
    }


def build_report(project, sprints=None):
    statuses = scope_case_statuses(project, sprints)
    cases = list(ExtractedUseCase.objects.filter(id__in=statuses).order_by('case_number', 'order'))

    defects = list(scope_defects(project, sprints).select_related('use_case', 'sprint', 'assigned_to')
                   .prefetch_related('related_use_cases').order_by('-created_at'))
    tickets = defaultdict(list)
    for d in defects:
        for cid in {d.use_case_id, *(c.id for c in d.related_use_cases.all())}:
            if cid:
                tickets[cid].append(d.ticket_number)

    if sprints is None:
        scope = {'type': 'project', 'label': 'Projet complet'}
    elif len(sprints) == 1:
        scope = {'type': 'sprint', 'label': f'Sprint « {sprints[0].name} »'}
    else:
        scope = {'type': 'sprints', 'label': f'{len(sprints)} sprints sélectionnés'}

    data = {
        'project': {
            'name': project.name, 'slug': project.slug, 'description': project.description,
            'status': project.status, 'status_label': project.get_status_display(),
            'team': project.team.name,
        },
        'scope': scope,
        'generated_at': timezone.now().isoformat(),
        'performance': project_performance(project, sprints),
        'evolution': project_evolution(project, sprints),
        'sprints': sprint_trend(project, sprints),
        'regressions': list_regressions(project, sprints),
        'members': member_workload(project, sprints),
        'cases': [{
            'id': str(c.id),
            'case_number': c.case_number,
            'title': c.use_case_text or c.description,
            'status': statuses[c.id],
            'is_automated': c.is_automated,
            'jira_ticket': c.jira_ticket,
            'defects': tickets.get(c.id, []),
        } for c in cases],
        'defects': [defect_row(d) for d in defects],
    }
    from .narrative import project_narrative
    # Phrases clés (introduction, points clés, plan d'action, recommandations, conclusion).
    data['narrative'] = project_narrative(project, sprints, data)
    return data


__all__ = [
    'CASE_STATUSES', 'build_report', 'defect_stats', 'list_regressions', 'project_evolution',
    'project_performance', 'sprint_summary', 'sprint_trend',
]
