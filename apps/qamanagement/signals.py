"""
Traçabilité des exécutions : tout changement de statut d'un cas de test — quel que
soit l'écran d'origine (Kanban, recette, API) — est historisé et synchronisé.

Les appelants peuvent préciser le contexte via deux attributs transitoires :
    use_case._status_actor   → utilisateur à l'origine du changement
    use_case._status_sprint  → sprint dans lequel le cas a été exécuté
"""
from datetime import timedelta

from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils import timezone

from apps.core.background import run_in_background
from apps.core.models import ExtractedUseCase
from apps.teams.models import ProjectMember, TeamMember
from .models import Defect, Sprint, UseCaseAssignment, UseCaseExecution, UseCaseScreenshot
from .notifications import queue_regression, send_sprint_report

OPEN_SPRINTS = (Sprint.Status.ACTIVE, Sprint.Status.PLANNED)


def _default_sprint(use_case):
    """Sprint ouvert le plus pertinent contenant ce cas (actif en priorité)."""
    sprints = Sprint.objects.filter(assignments__use_case=use_case, status__in=OPEN_SPRINTS)
    return (sprints.filter(status=Sprint.Status.ACTIVE).order_by('-start_date').first()
            or sprints.order_by('-start_date').first())


def is_regression(use_case, old_status, new_status):
    """Un cas régresse s'il échoue alors que son dernier résultat concluant était « Passé »."""
    if new_status != 'Échoué':
        return False
    last = (UseCaseExecution.objects.filter(use_case=use_case, new_status__in=('Passé', 'Échoué'))
            .order_by('-executed_at').values_list('new_status', flat=True).first())
    return (last or old_status) == 'Passé'


@receiver(pre_save, sender=ExtractedUseCase)
def remember_previous_status(sender, instance, raw=False, **kwargs):
    instance._previous_status = None
    if not raw and not instance._state.adding:
        instance._previous_status = (
            ExtractedUseCase.objects.filter(pk=instance.pk).values_list('status', flat=True).first())


@receiver(post_save, sender=ExtractedUseCase)
def track_status_change(sender, instance, created, raw=False, update_fields=None, **kwargs):
    if raw or not instance.project_id:
        return
    if update_fields is not None and 'status' not in update_fields:
        return
    old = '' if created else (instance._previous_status or '')
    new = instance.status
    if old == new or (created and new == ExtractedUseCase.UCStatus.TO_TEST):
        return

    sprint = getattr(instance, '_status_sprint', None) or _default_sprint(instance)
    # Horodatage strictement croissant : deux changements rapprochés restent ordonnés.
    executed_at = timezone.now()
    last = instance.executions.order_by('-executed_at').values_list('executed_at', flat=True).first()
    if last and executed_at <= last:
        executed_at = last + timedelta(microseconds=1)
    execution = UseCaseExecution.objects.create(
        use_case=instance, project_id=instance.project_id, sprint=sprint, executed_at=executed_at,
        old_status=old, new_status=new,
        is_regression=is_regression(instance, old, new),
        executed_by=getattr(instance, '_status_actor', None),
    )
    # Les sprints terminés/clôturés gardent leurs résultats : seuls les sprints ouverts suivent.
    UseCaseAssignment.objects.filter(use_case=instance).filter(
        Q(sprint__isnull=True) | Q(sprint__status__in=OPEN_SPRINTS)
    ).update(status=new)
    if execution.is_regression:
        queue_regression(execution)


@receiver(pre_save, sender=Sprint)
def freeze_results_on_completion(sender, instance, raw=False, **kwargs):
    """À la clôture, fige le statut courant de chaque cas dans ses affectations."""
    instance._just_completed = False
    if raw or instance._state.adding or not instance.is_frozen:
        return
    previous = Sprint.objects.filter(pk=instance.pk).first()
    if previous and not previous.is_frozen:
        instance._just_completed = True
        for assignment in previous.assignments.select_related('use_case'):
            if assignment.status != assignment.use_case.status:
                assignment.status = assignment.use_case.status
                assignment.save(update_fields=['status'])


@receiver(post_save, sender=Sprint)
def send_report_on_completion(sender, instance, raw=False, **kwargs):
    if not raw and getattr(instance, '_just_completed', False):
        instance._just_completed = False
        run_in_background(send_sprint_report, instance.pk)


@receiver(post_delete, sender=UseCaseScreenshot)
def delete_screenshot_file(sender, instance, **kwargs):
    _delete_file(instance.image)


@receiver(post_delete, sender=Defect)
def delete_defect_attachment(sender, instance, **kwargs):
    _delete_file(instance.attachment)


def _delete_file(field_file):
    """Supprime le fichier stocké une fois la suppression en base validée."""
    if field_file and field_file.name:
        storage, name = field_file.storage, field_file.name
        run_in_background(storage.delete, name)


def _drop_duplicate(assignment, survivor):
    """Supprime une affectation en double sans perdre ses captures d'écran."""
    UseCaseScreenshot.objects.filter(assignment=assignment).update(assignment=survivor)
    assignment.delete()


@receiver(pre_delete, sender=Sprint)
def merge_assignments_before_sprint_delete(sender, instance, **kwargs):
    """Évite une collision d'unicité quand les affectations repassent « hors sprint »."""
    for assignment in list(instance.assignments.filter(assigned_to__isnull=False)):
        survivor = UseCaseAssignment.objects.filter(
            use_case_id=assignment.use_case_id, assigned_to_id=assignment.assigned_to_id, sprint__isnull=True,
        ).first()
        if survivor:
            _drop_duplicate(assignment, survivor)


@receiver(pre_delete, sender=ProjectMember)
def release_member_cases(sender, instance, **kwargs):
    """Le cas reste dans le sprint (non assigné) ; une copie assignée à un autre membre prime."""
    for assignment in list(instance.uc_assignments.all()):
        survivor = UseCaseAssignment.objects.filter(
            use_case_id=assignment.use_case_id, sprint_id=assignment.sprint_id,
        ).exclude(pk=assignment.pk).first()
        if survivor:
            _drop_duplicate(assignment, survivor)


@receiver(pre_delete, sender=TeamMember)
def leave_team_projects(sender, instance, **kwargs):
    """Quitter l'équipe retire aussi l'accès à ses projets."""
    for member in ProjectMember.objects.filter(project__team_id=instance.team_id, user_id=instance.user_id):
        member.delete()
