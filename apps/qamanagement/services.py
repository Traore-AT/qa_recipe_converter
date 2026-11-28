"""Règles métier partagées par les vues et serializers QA."""
from apps.teams.models import ProjectMember, TeamMember
from .models import DefectHistory


def resolve_assignee(project, user_id):
    """
    Retourne (ProjectMember, None) pour un membre de l'équipe, en l'ajoutant au projet si besoin,
    ou (None, message d'erreur) s'il ne peut pas recevoir de cas de test.
    """
    member = ProjectMember.objects.filter(project=project, user_id=user_id).first()
    team_member = TeamMember.objects.filter(team=project.team, user_id=user_id).first()
    if team_member is None:
        return None, "Cet utilisateur n'est pas membre de l'équipe."
    if team_member.role == TeamMember.Role.VIEWER or (member and member.role == ProjectMember.Role.VIEWER):
        return None, 'Un lecteur ne peut pas recevoir de cas de test.'
    if member is None:
        member, _ = ProjectMember.objects.get_or_create(
            project=project, user_id=user_id, defaults={'role': ProjectMember.Role.TESTER})
    return member, None


def cases_planned_elsewhere(project, sprint):
    """
    Identifiants des cas planifiés dans un autre sprint du projet et absents de `sprint`.

    Chaque sprint garde ses propres cas : un cas n'apparaît dans plusieurs sprints que s'il y a été
    explicitement reporté (« Reporter les cas ») ou rejoué (campagne de non-régression).
    """
    from .models import UseCaseAssignment
    in_sprint = UseCaseAssignment.objects.filter(sprint=sprint).values('use_case_id')
    return (UseCaseAssignment.objects.filter(use_case__project=project, sprint__isnull=False)
            .exclude(sprint=sprint).exclude(use_case_id__in=in_sprint).values('use_case_id'))


def assign_cases(sprint, members, cases, by):
    """
    Confie `cases` (triés) aux `members` dans `sprint`, en équilibrant la charge de chacun dans ce sprint.

    Un membre ne reçoit jamais deux fois le même cas ; une affectation « en attente » (sans responsable)
    du sprint est complétée plutôt que dupliquée.
    Retourne (affectations créées ou complétées, erreurs, {membre.pk: nombre reçu}).
    """
    from .models import UseCaseAssignment
    load = {pm.pk: UseCaseAssignment.objects.filter(sprint=sprint, assigned_to=pm).count() for pm in members}
    received = {pm.pk: 0 for pm in members}
    done, errors = [], []
    for case in cases:
        holders = set(UseCaseAssignment.objects.filter(use_case=case, sprint=sprint, assigned_to__in=members)
                      .values_list('assigned_to_id', flat=True))
        candidates = [pm for pm in members if pm.pk not in holders]
        if not candidates:
            errors.append(f'CAS-{case.case_number:03d} déjà assigné à ce membre dans ce sprint')
            continue
        pm = min(candidates, key=lambda m: (load[m.pk], members.index(m)))
        waiting = UseCaseAssignment.objects.filter(use_case=case, sprint=sprint, assigned_to__isnull=True).first()
        if waiting:
            waiting.assigned_to, waiting.assigned_by = pm, by
            waiting.save(update_fields=['assigned_to', 'assigned_by'])
            assignment = waiting
        else:
            assignment = UseCaseAssignment.objects.create(
                use_case=case, assigned_to=pm, sprint=sprint, assigned_by=by, status=case.status)
        load[pm.pk] += 1
        received[pm.pk] += 1
        done.append(assignment)
    return done, errors, received


def assignable_members(project):
    """Membres de l'équipe, avec leur éventuelle appartenance au projet."""
    in_project = {m.user_id: m for m in ProjectMember.objects.filter(project=project)}
    rows = []
    for tm in TeamMember.objects.filter(team=project.team, user__is_active=True).select_related('user'):
        pm = in_project.get(tm.user_id)
        if tm.role == TeamMember.Role.VIEWER or (pm and pm.role == ProjectMember.Role.VIEWER):
            continue
        rows.append((tm, pm))
    return sorted(rows, key=lambda r: (r[0].user.get_full_name() or r[0].user.username).lower())


TRACKED_DEFECT_FIELDS = {
    'title': 'Titre', 'severity': 'Sévérité', 'priority': 'Priorité', 'status': 'Statut',
    'sprint': 'Sprint', 'use_case': 'Cas de test', 'assigned_to': 'Assigné à',
    'is_regression': 'Régression', 'regression_of': "Anomalie d'origine", 'resolution': 'Résolution',
}


def _display(defect, field):
    if field in ('severity', 'priority', 'status'):
        return getattr(defect, f'get_{field}_display')()
    value = getattr(defect, field)
    if value is None or value == '':
        return ''
    if isinstance(value, bool):
        return 'Oui' if value else 'Non'
    if field == 'use_case':
        return f'CAS-{value.case_number:03d}'
    if field == 'sprint':
        return value.name
    if field == 'regression_of':
        return value.ticket_number
    if field == 'assigned_to':
        return value.get_full_name() or value.username
    return str(value)


def snapshot_defect(defect):
    values = {field: _display(defect, field) for field in TRACKED_DEFECT_FIELDS}
    values['related_use_cases'] = ', '.join(
        f'CAS-{n:03d}' for n in sorted(defect.related_use_cases.values_list('case_number', flat=True)))
    return values


def record_defect_changes(defect, before, actor):
    labels = {**TRACKED_DEFECT_FIELDS, 'related_use_cases': 'Autres cas impactés'}
    after = snapshot_defect(defect)
    DefectHistory.objects.bulk_create([
        DefectHistory(defect=defect, actor=actor, field=labels[f], old_value=before[f], new_value=after[f])
        for f in labels if before[f] != after[f]
    ])
