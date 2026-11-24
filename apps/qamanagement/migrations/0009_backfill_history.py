"""
Reprise des données existantes :
- date de création des cas (recette importée ou première affectation) ;
- historique d'exécution reconstitué depuis le journal d'activité, pour les courbes d'évolution ;
- statut des affectations réaligné sur celui du cas (référence des sprints terminés) ;
- dates de résolution des anomalies ; notifications déjà lues.
"""
import uuid

from django.db import migrations
from django.utils import timezone


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def backfill(apps, schema_editor):
    UseCase = apps.get_model('core', 'ExtractedUseCase')
    Assignment = apps.get_model('qamanagement', 'UseCaseAssignment')
    Execution = apps.get_model('qamanagement', 'UseCaseExecution')
    ActivityLog = apps.get_model('qamanagement', 'ActivityLog')
    Defect = apps.get_model('qamanagement', 'Defect')
    now = timezone.now()

    for case in UseCase.objects.filter(created_at__isnull=True).select_related('job').iterator():
        first = (Assignment.objects.filter(use_case_id=case.pk).order_by('assigned_at')
                 .values_list('assigned_at', flat=True).first())
        created = case.job.created_at if case.job_id else (first or now)
        UseCase.objects.filter(pk=case.pk).update(created_at=created)

    cases = {c['id']: c for c in UseCase.objects.filter(project__isnull=False).values('id', 'project_id', 'status')}
    last_status, conclusive, rows = {}, {}, []
    for log in ActivityLog.objects.filter(action_type='use_case_status').order_by('created_at').iterator():
        meta = log.metadata or {}
        case_id, new = _uuid(meta.get('uc_id')), meta.get('new_status')
        old = meta.get('old_status') or ''
        if case_id not in cases or not new or new == old:
            continue
        rows.append(Execution(
            id=uuid.uuid4(), use_case_id=case_id, project_id=cases[case_id]['project_id'],
            old_status=old, new_status=new, executed_by_id=log.actor_id, executed_at=log.created_at,
            is_regression=new == 'Échoué' and conclusive.get(case_id, old) == 'Passé',
        ))
        last_status[case_id] = new
        if new in ('Passé', 'Échoué'):
            conclusive[case_id] = new
    for case_id, case in cases.items():
        # Changement non journalisé (ex. glisser-déposer) : daté de la reprise, faute de mieux.
        previous = last_status.get(case_id, 'À tester')
        if case['status'] != previous:
            rows.append(Execution(
                id=uuid.uuid4(), use_case_id=case_id, project_id=case['project_id'],
                old_status=previous, new_status=case['status'], executed_at=now, is_regression=False,
            ))
    Execution.objects.bulk_create(rows, batch_size=500)

    for assignment in Assignment.objects.select_related('use_case').iterator():
        if assignment.status != assignment.use_case.status:
            Assignment.objects.filter(pk=assignment.pk).update(status=assignment.use_case.status)

    for defect in Defect.objects.filter(status__in=['resolved', 'closed'], resolved_at__isnull=True):
        Defect.objects.filter(pk=defect.pk).update(
            resolved_at=defect.updated_at, closed_at=defect.updated_at if defect.status == 'closed' else None)

    for log in ActivityLog.objects.filter(is_read=True).prefetch_related('target_users'):
        log.read_by.add(*log.target_users.all())


class Migration(migrations.Migration):

    dependencies = [
        ('qamanagement', '0008_defect_management_and_executions'),
        ('core', '0009_usecase_optional_project_created_at'),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
