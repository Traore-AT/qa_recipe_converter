"""Report des cas non terminés et campagnes de non-régression."""
from django.db import transaction
from django.db.models import Q

from apps.core.models import ExtractedUseCase
from apps.teams.models import TeamMember
from .models import Sprint, UseCaseAssignment, UseCaseExecution

UNFINISHED = ('À tester', 'En cours', 'Bloqué')


class CampaignError(Exception):
    """Règle métier non respectée (message destiné à l'utilisateur)."""


def carry_over_candidates(sprint, include_failed=False):
    """Affectations du sprint encore à traiter — y compris au regard du statut actuel du cas."""
    statuses = UNFINISHED + (('Échoué',) if include_failed else ())
    qs = sprint.assignments.select_related('use_case', 'sprint', 'assigned_to__user').order_by('use_case__case_number')
    # Sprint figé : un cas réussi depuis dans un autre sprint n'a plus à être reporté.
    return [a for a in qs if a.effective_status in statuses and a.use_case.status in statuses]


def carry_over(source, target, user, case_ids=None, include_failed=False, close_source=False):
    if target.pk == source.pk:
        raise CampaignError('Choisissez un autre sprint de destination.')
    if target.project_id != source.project_id:
        raise CampaignError('Le sprint de destination doit appartenir au même projet.')
    if target.is_frozen:
        raise CampaignError('Le sprint de destination est terminé.')

    with transaction.atomic():
        wanted = None if case_ids is None else {str(c) for c in case_ids}
        present = set(target.assignments.values_list('use_case_id', flat=True))
        created, skipped = [], []
        for assignment in carry_over_candidates(source, include_failed):
            if wanted is not None and str(assignment.use_case_id) not in wanted:
                continue
            if assignment.use_case_id in present:
                skipped.append(assignment.use_case.case_number)
                continue
            UseCaseAssignment.objects.create(
                use_case=assignment.use_case, sprint=target, assigned_to=assignment.assigned_to,
                assigned_by=user, status=assignment.effective_status, carried_from=source)
            created.append(assignment)
        if close_source and not source.is_frozen:
            source.status = Sprint.Status.COMPLETED
            source.save()
    moved = {a.use_case_id for a in created}
    # Un cas confié à plusieurs membres est reporté une fois par membre, jamais « ignoré » pour autant.
    return {'created': len(moved), 'skipped': sorted(set(skipped) - {a.use_case.case_number for a in created})}


def regression_cases(project, scope='all'):
    """Cas ayant déjà régressé ; scope='active' : seulement ceux encore en échec."""
    ids = UseCaseExecution.objects.filter(project=project, is_regression=True).values('use_case_id')
    flagged = Q(defects__is_regression=True) | Q(related_defects__is_regression=True)
    cases = ExtractedUseCase.objects.filter(project=project).filter(Q(id__in=ids) | flagged).distinct()
    if scope == 'active':
        cases = cases.filter(status='Échoué')
    return cases.order_by('case_number')


def create_regression_campaign(project, user, sprint_data, scope='all', reset_status=True):
    cases = list(regression_cases(project, scope))
    if not cases:
        raise CampaignError("Aucun cas en régression : la campagne n'aurait rien à rejouer.")
    with transaction.atomic():
        sprint = Sprint.objects.create(project=project, created_by=user, kind=Sprint.Kind.REGRESSION, **sprint_data)
        team_ids = set(TeamMember.objects.filter(team=project.team).exclude(role='viewer').values_list('user_id', flat=True))
        last = {}
        for a in UseCaseAssignment.objects.filter(use_case__in=cases, assigned_to__isnull=False).select_related(
                'assigned_to').order_by('assigned_at'):
            if a.assigned_to.user_id in team_ids and a.assigned_to.role != 'viewer':
                last[a.use_case_id] = a.assigned_to
        for case in cases:
            UseCaseAssignment.objects.create(use_case=case, sprint=sprint, assigned_to=last.get(case.id),
                                             assigned_by=user, status=case.status)
            if reset_status and case.status != ExtractedUseCase.UCStatus.TO_TEST:
                case.status = ExtractedUseCase.UCStatus.TO_TEST
                case._status_actor, case._status_sprint = user, sprint
                case.save(update_fields=['status'])
    return sprint, len(cases)
