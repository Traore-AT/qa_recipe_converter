import csv
import io
import logging
import uuid
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.text import slugify
from datetime import date, timedelta
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from .models import (
    Sprint, UseCaseAssignment, UseCaseExecution, Defect, WeeklyReport, ActivityLog,
    UseCaseComment, UseCaseScreenshot, CASE_STATUSES,
)
from .services import (
    assign_cases, assignable_members, cases_planned_elsewhere, record_defect_changes, resolve_assignee,
    snapshot_defect,
)
from . import analytics, jira
from .notifications import alert_if_critical
from .serializers import (
    SprintSerializer, SprintCreateSerializer,
    UseCaseAssignmentSerializer, CreateAssignmentSerializer,
    DefectSerializer, DefectWriteSerializer, DefectCommentSerializer, DefectHistorySerializer,
    WeeklyReportSerializer,
    ActivityLogSerializer, UseCaseCommentSerializer,
    UseCaseScreenshotSerializer,
    SprintCaseCreateSerializer, SprintCaseUpdateSerializer,
)
from apps.teams.models import Team, Project, ProjectMember, TeamMember
from apps.teams.permissions import can_view_project, can_manage_project, can_write_project
from apps.core.models import ConversionJob, ExtractedUseCase
from apps.core.use_cases import clean_jira_ticket
from apps.teams.serializers import UserPublicSerializer, ProjectSerializer

logger = logging.getLogger(__name__)


def _get_team_and_project(slug, project_slug, user):
    team = get_object_or_404(Team, slug=slug)
    if not team.is_member(user):
        raise PermissionDenied("Vous n'êtes pas membre de cette équipe.")
    project = get_object_or_404(Project, team=team, slug=project_slug)
    if not can_view_project(user, project):
        raise PermissionDenied("Accès refusé à ce projet.")
    return team, project


def _require_write(user, project):
    if project.is_archived:
        raise PermissionDenied('Projet archivé — lecture seule.')
    if not can_write_project(user, project):
        raise PermissionDenied('Les lecteurs ne peuvent pas modifier ce projet.')


def _assignments(**filters):
    """Affectations avec tout ce que leur sérialisation consulte (évite les requêtes N+1)."""
    return UseCaseAssignment.objects.filter(**filters).select_related(
        'use_case', 'sprint', 'assigned_to__user', 'assigned_by', 'carried_from',
    ).prefetch_related('use_case__defects', 'use_case__related_defects', 'use_case__executions',
                       'use_case__assignments__sprint')


def _bad_request(field, message):
    from rest_framework.exceptions import ValidationError
    return ValidationError({field: [message]})


def _uuid_or_400(value, field):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise _bad_request(field, 'Identifiant invalide.')


def _int_or_400(value, field):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise _bad_request(field, 'Nombre invalide.')


def _date_or_400(value, field):
    if not value:
        return None
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except ValueError:
        raise _bad_request(field, 'Date invalide (format attendu : AAAA-MM-JJ).')


def _log_activity(project, team, actor, action_type, description, metadata=None, target_users=None):
    log = ActivityLog.objects.create(
        project=project,
        team=team or (project.team if project else None),
        actor=actor,
        action_type=action_type,
        description=description,
        metadata=metadata or {},
    )
    if target_users:
        log.target_users.set(target_users)
    return log


# ─────────────────────────────────────────────────────────────────────────────
# SPRINTS
# ─────────────────────────────────────────────────────────────────────────────

class SprintListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        sprints = Sprint.objects.filter(project=project).select_related('created_by')
        status_f = request.query_params.get('status')
        if status_f:
            sprints = sprints.filter(status=status_f)
        s = SprintSerializer(sprints, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})

    def post(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Seuls les admins/leads peuvent créer des sprints.'},
                            status=status.HTTP_403_FORBIDDEN)
        s = SprintCreateSerializer(data=request.data)
        if s.is_valid():
            sprint = s.save(project=project, created_by=request.user)
            _log_activity(project, team, request.user, 'sprint_created',
                          f'Sprint "{sprint.name}" créé ({sprint.start_date} → {sprint.end_date})')
            return Response(SprintSerializer(sprint, context={'request': request}).data,
                            status=status.HTTP_201_CREATED)
        return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)


class SprintDetailView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def _get_sprint(self, slug, project_slug, sprint_id, user):
        team, project = _get_team_and_project(slug, project_slug, user)
        sprint = get_object_or_404(Sprint, id=sprint_id, project=project)
        return team, project, sprint

    def get(self, request, slug, project_slug, sprint_id):
        _, _, sprint = self._get_sprint(slug, project_slug, sprint_id, request.user)
        return Response(SprintSerializer(sprint, context={'request': request}).data)

    def patch(self, request, slug, project_slug, sprint_id):
        team, project, sprint = self._get_sprint(slug, project_slug, sprint_id, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Non autorisé.'}, status=status.HTTP_403_FORBIDDEN)
        before = {'name': sprint.name, 'start_date': str(sprint.start_date), 'end_date': str(sprint.end_date)}
        s = SprintCreateSerializer(sprint, data=request.data, partial=True)
        if not s.is_valid():
            return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)
        sprint = s.save()
        changes = []
        if before['name'] != sprint.name:
            changes.append(f'renommé « {before["name"]} » → « {sprint.name} »')
        if (before['start_date'], before['end_date']) != (str(sprint.start_date), str(sprint.end_date)):
            changes.append(f'période {sprint.start_date} → {sprint.end_date} ({sprint.duration_days} jours)')
        _log_activity(project, team, request.user, 'sprint_updated',
                      f'Sprint "{sprint.name}" mis à jour → {sprint.get_status_display()}'
                      + (f' ({" ; ".join(changes)})' if changes else ''),
                      metadata={'before': before})
        return Response(SprintSerializer(sprint, context={'request': request}).data)

    def delete(self, request, slug, project_slug, sprint_id):
        team, project, sprint = self._get_sprint(slug, project_slug, sprint_id, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Non autorisé.'}, status=status.HTTP_403_FORBIDDEN)
        sprint.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────────────────
# SPRINT BOARD
# ─────────────────────────────────────────────────────────────────────────────

class SprintBoardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug, sprint_id):
        team, project, sprint = SprintDetailView()._get_sprint(slug, project_slug, sprint_id, request.user)
        assignments = _assignments(sprint=sprint)
        columns = {
            'À tester': [],
            'En cours': [],
            'Passé': [],
            'Échoué': [],
            'Bloqué': [],
        }
        for a in assignments:
            status_key = a.effective_status
            if status_key not in columns:
                status_key = 'À tester'
            columns[status_key].append(UseCaseAssignmentSerializer(a, context={'request': request}).data)
        return Response({
            'sprint': SprintSerializer(sprint, context={'request': request}).data,
            'columns': columns,
            'tickets': [],
            'stats': sprint.stats,
        })


def _sprint_assignment(sprint, ticket_id):
    """Affectation désignée par son id ou celui du cas (un cas peut être confié à plusieurs membres)."""
    from django.http import Http404
    assignment = _assignments(sprint=sprint).filter(Q(use_case_id=ticket_id) | Q(id=ticket_id)).first()
    if assignment is None:
        raise Http404('Cas introuvable dans ce sprint.')
    return assignment


def _reassignment_conflict(serializer, assignment):
    """Réponse 400 si le cas est déjà confié à ce membre dans le même sprint, sinon None."""
    assignee = serializer.validated_data.get('assigned_to', assignment.assigned_to)
    if assignee and UseCaseAssignment.objects.filter(
            use_case=assignment.use_case, sprint=assignment.sprint, assigned_to=assignee
    ).exclude(pk=assignment.pk).exists():
        return Response({'assigned_to': ['Ce cas est déjà assigné à ce membre dans ce sprint.']},
                        status=status.HTTP_400_BAD_REQUEST)
    return None


class SprintCaseListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def _get_sprint(self, slug, project_slug, sprint_id, user):
        _, project = _get_team_and_project(slug, project_slug, user)
        return get_object_or_404(Sprint, id=sprint_id, project=project)

    def get(self, request, slug, project_slug, sprint_id):
        sprint = self._get_sprint(slug, project_slug, sprint_id, request.user)
        assignments = _assignments(sprint=sprint)
        search = request.query_params.get('ticket', '').strip()
        if search:
            number = search.upper().removeprefix('CAS-').strip()
            if number.isdigit():
                assignments = assignments.filter(Q(use_case__case_number=int(number)) | Q(use_case__jira_ticket__icontains=search) | Q(use_case__ticket_number__icontains=search))
            else:
                assignments = assignments.filter(Q(use_case__ticket_number__icontains=search) | Q(use_case__jira_ticket__icontains=search))
        data = UseCaseAssignmentSerializer(assignments, many=True, context={'request': request}).data
        return Response({'results': data, 'count': len(data)})

    def post(self, request, slug, project_slug, sprint_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Seuls les responsables du projet peuvent créer des tickets.'}, status=status.HTTP_403_FORBIDDEN)
        sprint = get_object_or_404(Sprint, id=sprint_id, project=project)
        serializer = SprintCaseCreateSerializer(data=request.data, context={'request': request, 'project': project})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            assigned_to = serializer.validated_data.pop('assigned_to', None)
            initial_comment = serializer.validated_data.pop('comment', '').strip()
            case = serializer.save(project=project, order=0)
            if case.status != ExtractedUseCase.UCStatus.TO_TEST:
                # Le cas est rattaché au sprint après sa création : on complète son historique.
                UseCaseExecution.objects.filter(use_case=case).update(sprint=sprint, executed_by=request.user)
            assignment = UseCaseAssignment.objects.create(
                use_case=case, sprint=sprint, assigned_to=assigned_to,
                assigned_by=request.user, status=case.status)
            if initial_comment:
                UseCaseComment.objects.create(use_case=case, author=request.user, content=initial_comment)
        _log_activity(project, team, request.user, 'use_case_created',
                      f'CAS-{case.case_number:03d} créé dans le sprint « {sprint.name} ».')
        return Response(UseCaseAssignmentSerializer(assignment, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)


class SprintCaseDetailView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def _get_ticket(self, slug, project_slug, sprint_id, ticket_id, user):
        _, project = _get_team_and_project(slug, project_slug, user)
        sprint = get_object_or_404(Sprint, id=sprint_id, project=project)
        return project, sprint, _sprint_assignment(sprint, ticket_id)

    def patch(self, request, slug, project_slug, sprint_id, ticket_id):
        project, sprint, assignment = self._get_ticket(slug, project_slug, sprint_id, ticket_id, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Non autorisé.'}, status=status.HTTP_403_FORBIDDEN)
        serializer = SprintCaseUpdateSerializer(assignment.use_case, data=request.data, partial=True,
                                                context={'request': request, 'project': project, 'assignment': assignment})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        conflict = _reassignment_conflict(serializer, assignment)
        if conflict:
            return conflict
        serializer.save()
        assignment = _assignments(pk=assignment.pk).get()
        return Response(UseCaseAssignmentSerializer(assignment, context={'request': request}).data)

    def delete(self, request, slug, project_slug, sprint_id, ticket_id):
        project, _, assignment = self._get_ticket(slug, project_slug, sprint_id, ticket_id, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Non autorisé.'}, status=status.HTTP_403_FORBIDDEN)
        case = assignment.use_case
        job_id = case.job_id
        with transaction.atomic():
            case.delete()
            if job_id:
                job = ConversionJob.objects.select_for_update().get(pk=job_id)
                job.use_cases_count = job.use_cases.count()
                job.save(update_fields=['use_cases_count'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class SprintCaseCommentListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def _ticket(self, slug, project_slug, sprint_id, ticket_id, user):
        _, project = _get_team_and_project(slug, project_slug, user)
        sprint = get_object_or_404(Sprint, id=sprint_id, project=project)
        return _sprint_assignment(sprint, ticket_id)

    def get(self, request, slug, project_slug, sprint_id, ticket_id):
        assignment = self._ticket(slug, project_slug, sprint_id, ticket_id, request.user)
        comments = UseCaseComment.objects.filter(use_case=assignment.use_case).select_related('author')
        return Response({'results': UseCaseCommentSerializer(comments, many=True, context={'request': request}).data,
                         'count': comments.count()})

    def post(self, request, slug, project_slug, sprint_id, ticket_id):
        assignment = self._ticket(slug, project_slug, sprint_id, ticket_id, request.user)
        serializer = UseCaseCommentSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        comment = serializer.save(use_case=assignment.use_case, author=request.user)
        return Response(UseCaseCommentSerializer(comment, context={'request': request}).data, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# USE CASE ASSIGNMENTS
# ─────────────────────────────────────────────────────────────────────────────

class AssignmentListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        assignments = _assignments(use_case__project=project)

        sprint_f = request.query_params.get('sprint')
        if sprint_f:
            assignments = assignments.filter(sprint_id=_uuid_or_400(sprint_f, 'sprint'))
        user_f = request.query_params.get('user_id')
        if user_f:
            assignments = assignments.filter(assigned_to__user_id=_int_or_400(user_f, 'user_id'))

        s = UseCaseAssignmentSerializer(assignments, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})

    def post(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Seuls les admins/leads peuvent assigner des cas.'},
                            status=status.HTTP_403_FORBIDDEN)

        s = CreateAssignmentSerializer(data=request.data)
        if not s.is_valid():
            return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)

        use_case_ids = s.validated_data['use_case_ids']
        sprint_id = s.validated_data.get('sprint_id')
        user_ids = s.validated_data.get('user_ids') or [s.validated_data['user_id']]

        # Tout membre de l'équipe peut recevoir des cas : il rejoint alors le projet comme testeur.
        members = []
        for user_id in user_ids:
            pm, error = resolve_assignee(project, user_id)
            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
            members.append(pm)

        sprint = None
        if sprint_id:
            sprint = get_object_or_404(Sprint, id=sprint_id, project=project)
            if sprint.is_frozen:
                return Response({'error': "Ce sprint est terminé : rouvrez-le pour y assigner des cas."},
                                status=status.HTTP_400_BAD_REQUEST)

        ucs = ExtractedUseCase.objects.filter(id__in=use_case_ids, project=project).order_by('case_number')
        errors = []
        elsewhere = {}
        if sprint:
            # Un cas planifié dans un autre sprint n'est pas réassigné ici (il se reporte explicitement).
            for case_id, name in (UseCaseAssignment.objects
                                  .filter(use_case__in=ucs, use_case_id__in=cases_planned_elsewhere(project, sprint))
                                  .exclude(sprint=sprint).values_list('use_case_id', 'sprint__name')):
                elsewhere.setdefault(case_id, name)
        blocked = []
        for uc in ucs:
            if uc.id in elsewhere:
                errors.append(f'CAS-{uc.case_number:03d} est déjà planifié dans le sprint « {elsewhere[uc.id]} » '
                              '(utilisez « Reporter les cas »)')
                blocked.append(uc.id)
        created_assignments, refused, received = assign_cases(
            sprint, members, [uc for uc in ucs if uc.id not in blocked], request.user)
        errors += refused
        created = [str(a.use_case_id) for a in created_assignments]

        if not created and errors:
            return Response({'error': ' ; '.join(errors), 'created': [], 'count': 0, 'errors': errors},
                            status=status.HTTP_400_BAD_REQUEST)
        distribution = [{'user_id': pm.user_id, 'username': pm.user.username,
                         'full_name': pm.user.get_full_name() or pm.user.username, 'count': received[pm.pk]}
                        for pm in members]
        for row in distribution:
            if row['count']:
                _log_activity(project, team, request.user, 'assignments_created',
                              f'{row["count"]} cas assigné(s) à {row["username"]}',
                              target_users=[pm.user for pm in members if pm.user_id == row['user_id']])

        # Send email notification for each assignment
        if sprint and created_assignments:
            try:
                from apps.teams.email import send_sprint_assignment_email
                for assignment in created_assignments:
                    send_sprint_assignment_email(assignment, sprint, request.user)
            except Exception:
                logger.exception("Failed to send assignment email")

        return Response({'created': created, 'count': len(created), 'errors': errors, 'distribution': distribution},
                        status=status.HTTP_201_CREATED)


class AssignmentDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, slug, project_slug, assignment_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Non autorisé.'}, status=status.HTTP_403_FORBIDDEN)
        assignment = get_object_or_404(UseCaseAssignment, id=assignment_id, use_case__project=project)
        case = assignment.use_case
        job_id = case.job_id
        with transaction.atomic():
            case.delete()
            if job_id:
                job = ConversionJob.objects.select_for_update().get(pk=job_id)
                job.use_cases_count = job.use_cases.count()
                job.save(update_fields=['use_cases_count'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    def patch(self, request, slug, project_slug, assignment_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Non autorisé.'}, status=status.HTTP_403_FORBIDDEN)
        assignment = get_object_or_404(_assignments(sprint__project=project, use_case__project=project),
                                       id=assignment_id)
        serializer = SprintCaseUpdateSerializer(
            assignment.use_case, data=request.data, partial=True,
            context={'request': request, 'project': project, 'assignment': assignment})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        conflict = _reassignment_conflict(serializer, assignment)
        if conflict:
            return conflict
        previous = assignment.assigned_to_id
        serializer.save()
        assignment = _assignments(pk=assignment.pk).get()
        if assignment.assigned_to_id and assignment.assigned_to_id != previous:
            target = assignment.assigned_to.user
            _log_activity(project, team, request.user, 'assignments_created',
                          f'CAS-{assignment.use_case.case_number:03d} assigné à {target.username}',
                          target_users=[target])
        return Response(UseCaseAssignmentSerializer(assignment, context={'request': request}).data)


# ─────────────────────────────────────────────────────────────────────────────
# USE CASE EXECUTION (status update + comment)
# ─────────────────────────────────────────────────────────────────────────────

class UseCaseStatusUpdateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def patch(self, request, slug, project_slug, uc_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        _require_write(request.user, project)
        uc = get_object_or_404(ExtractedUseCase, id=uc_id, project=project)

        new_status = request.data.get('status')
        if new_status not in CASE_STATUSES:
            return Response({'error': f'Statut invalide. Valeurs: {", ".join(CASE_STATUSES)}'},
                            status=status.HTTP_400_BAD_REQUEST)

        sprint = None
        if request.data.get('sprint_id'):
            sprint = get_object_or_404(Sprint, id=_uuid_or_400(request.data['sprint_id'], 'sprint_id'), project=project)
            if sprint.is_frozen:
                return Response({'error': 'Ce sprint est terminé : rouvrez-le pour modifier les résultats.'},
                                status=status.HTTP_400_BAD_REQUEST)
            if not UseCaseAssignment.objects.filter(sprint=sprint, use_case=uc).exists():
                # Chaque sprint ne rend compte que de ses propres cas.
                return Response({'error': "Ce cas n'appartient pas à ce sprint."},
                                status=status.HTTP_400_BAD_REQUEST)

        old_status = uc.status
        uc.status = new_status
        uc.observed_results = request.data.get('observed_results', uc.observed_results) or ''
        # L'historique d'exécution et la synchronisation des sprints ouverts sont gérés par signals.py
        uc._status_actor, uc._status_sprint = request.user, sprint
        uc.save()

        # Notify project leads
        lead_members = ProjectMember.objects.filter(project=project, role='lead').values_list('user', flat=True)
        lead_users = User.objects.filter(id__in=lead_members)
        _log_activity(
            project, team, request.user, 'use_case_status',
            f'UC#{uc.order} "{uc.use_case_text or uc.description[:50]}" : {old_status} → {new_status}',
            metadata={'uc_id': str(uc.id), 'old_status': old_status, 'new_status': new_status},
            target_users=list(lead_users)
        )

        # Notify leads by email when member completes all their assignments in a sprint
        completed_statuses = ['Passé', 'Échoué', 'Bloqué']
        if new_status in completed_statuses and old_status not in completed_statuses:
            try:
                pm = ProjectMember.objects.filter(project=project, user=request.user).first()
                if pm:
                    sprint_assignments = UseCaseAssignment.objects.filter(
                        assigned_to=pm,
                        sprint__isnull=False
                    ).select_related('sprint')
                    sprints_affected = set(a.sprint for a in sprint_assignments if a.sprint)
                    for sprint in sprints_affected:
                        other_assignments = [a for a in sprint_assignments if a.sprint_id == sprint.id]
                        all_done = all(a.status in completed_statuses for a in other_assignments)
                        if all_done:
                            from apps.teams.email import send_member_completed_email
                            send_member_completed_email(pm, sprint, project)
            except Exception:
                logger.exception("Failed to send completion email")

        return Response({
            'id': str(uc.id),
            'status': uc.status,
            'observed_results': uc.observed_results,
            'is_regression': bool(old_status != new_status and UseCaseExecution.objects.filter(
                use_case=uc).order_by('-executed_at').values_list('is_regression', flat=True).first()),
        })


class UseCaseJiraUpdateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def patch(self, request, slug, project_slug, uc_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        _require_write(request.user, project)
        uc = get_object_or_404(ExtractedUseCase, id=uc_id, project=project)

        uc.jira_ticket = clean_jira_ticket(request.data.get('jira_ticket'))
        uc.save()

        return Response({
            'id': str(uc.id),
            'jira_ticket': uc.jira_ticket,
        })


class UseCaseCommentListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def get(self, request, slug, project_slug, uc_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        uc = get_object_or_404(ExtractedUseCase, id=uc_id, project=project)
        comments = UseCaseComment.objects.filter(use_case=uc).select_related('author')
        s = UseCaseCommentSerializer(comments, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})

    def post(self, request, slug, project_slug, uc_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        uc = get_object_or_404(ExtractedUseCase, id=uc_id, project=project)
        s = UseCaseCommentSerializer(data=request.data, context={'request': request})
        if s.is_valid():
            comment = s.save(use_case=uc, author=request.user)
            lead_members = ProjectMember.objects.filter(project=project, role='lead').values_list('user', flat=True)
            lead_users = User.objects.filter(id__in=lead_members).exclude(id=request.user.id)
            _log_activity(
                project, team, request.user, 'use_case_comment',
                f'Commentaire sur UC#{uc.order} par {request.user.username}',
                metadata={'uc_id': str(uc.id), 'comment_id': str(comment.id)},
                target_users=list(lead_users)
            )
            return Response(UseCaseCommentSerializer(comment, context={'request': request}).data,
                            status=status.HTTP_201_CREATED)
        return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)


# ─────────────────────────────────────────────────────────────────────────────
# DEFECTS
# ─────────────────────────────────────────────────────────────────────────────

def _defects(project):
    return Defect.objects.filter(project=project).select_related(
        'reported_by', 'assigned_to', 'use_case', 'sprint', 'regression_of',
    ).prefetch_related('related_use_cases', 'comments')


def _flag(value):
    return str(value).lower() in ('1', 'true', 'oui')


def _filter_defects(defects, params):
    """Filtres communs à la liste et à l'export des anomalies."""
    for field in ('status', 'severity', 'priority'):
        if params.get(field):
            defects = defects.filter(**{field: params[field]})
    if params.get('assigned_to'):
        defects = defects.filter(assigned_to_id=_int_or_400(params['assigned_to'], 'assigned_to'))
    if params.get('sprint') == 'none':
        defects = defects.filter(sprint__isnull=True)
    elif params.get('sprint'):
        defects = defects.filter(sprint_id=_uuid_or_400(params['sprint'], 'sprint'))
    if params.get('use_case'):
        case_id = _uuid_or_400(params['use_case'], 'use_case')
        defects = defects.filter(Q(use_case_id=case_id) | Q(related_use_cases=case_id)).distinct()
    if params.get('regression'):
        defects = defects.filter(is_regression=_flag(params['regression']))
    if params.get('ticket'):
        defects = defects.filter(ticket_number__iexact=params['ticket'].strip())
    search = params.get('search', '').strip()
    if search:
        defects = defects.filter(Q(title__icontains=search) | Q(description__icontains=search)
                                 | Q(ticket_number__icontains=search))
    return defects


class DefectListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    PAGE_SIZE = 20

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        defects = _filter_defects(_defects(project), request.query_params)
        params = request.query_params

        count = defects.count()
        # Pagination à la demande : sans `page`, la liste complète est renvoyée.
        if params.get('page'):
            page = max(_int_or_400(params['page'], 'page'), 1)
            defects = defects[(page - 1) * self.PAGE_SIZE:page * self.PAGE_SIZE]
        s = DefectSerializer(defects, many=True, context={'request': request})
        return Response({'results': s.data, 'count': count})

    def post(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        _require_write(request.user, project)
        s = DefectWriteSerializer(data=request.data, context={'request': request, 'project': project})
        if not s.is_valid():
            return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)
        extra = {}
        if 'is_regression' not in s.validated_data:
            # Détection automatique : le cas lié passait auparavant, ou une anomalie d'origine est indiquée.
            case = s.validated_data.get('use_case')
            extra['is_regression'] = bool(s.validated_data.get('regression_of')) or bool(
                case and UseCaseExecution.objects.filter(use_case=case, is_regression=True).exists()
                and case.status == ExtractedUseCase.UCStatus.FAILED)
        with transaction.atomic():
            new_status = s.validated_data.pop('status', Defect.Status.OPEN)
            defect = s.save(project=project, reported_by=request.user, **extra)
            if new_status != defect.status:
                defect.apply_status(new_status)
                defect.save()
        _log_activity(project, team, request.user, 'defect_created',
                      f'{"Régression" if defect.is_regression else "Anomalie"} signalée : '
                      f'{defect.title} ({defect.get_severity_display()})',
                      target_users=[defect.assigned_to] if defect.assigned_to else None)
        alert_if_critical(defect)
        jira.queue_push(defect)
        defect = _defects(project).get(pk=defect.pk)
        return Response(DefectSerializer(defect, context={'request': request}).data, status=status.HTTP_201_CREATED)


class DefectDetailView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _get_defect(self, slug, project_slug, defect_id, user):
        team, project = _get_team_and_project(slug, project_slug, user)
        defect = get_object_or_404(_defects(project), id=defect_id)
        return team, project, defect

    def get(self, request, slug, project_slug, defect_id):
        _, _, defect = self._get_defect(slug, project_slug, defect_id, request.user)
        return Response(DefectSerializer(defect, context={'request': request}).data)

    def patch(self, request, slug, project_slug, defect_id):
        team, project, defect = self._get_defect(slug, project_slug, defect_id, request.user)
        _require_write(request.user, project)
        s = DefectWriteSerializer(defect, data=request.data, partial=True,
                                  context={'request': request, 'project': project})
        if not s.is_valid():
            return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)
        before = snapshot_defect(defect)
        was_alerting = defect.severity == 'critical' and defect.is_open
        old_status, old_label, old_assignee = defect.status, defect.get_status_display(), defect.assigned_to_id
        with transaction.atomic():
            new_status = s.validated_data.pop('status', None)
            if new_status and new_status != defect.status:
                defect.apply_status(new_status)
            s.save()
            record_defect_changes(defect, before, request.user)

        if old_status != defect.status:
            interested = {u for u in (defect.assigned_to, defect.reported_by) if u and u != request.user}
            _log_activity(project, team, request.user, 'defect_status',
                          f'Anomalie "{defect.title}" : {old_label} → {defect.get_status_display()}',
                          target_users=list(interested))
        if defect.assigned_to_id and defect.assigned_to_id != old_assignee:
            _log_activity(project, team, request.user, 'defect_assigned',
                          f'Anomalie "{defect.title}" assignée à {defect.assigned_to.username}',
                          target_users=[defect.assigned_to])

        alert_if_critical(defect, was_alerting)
        jira.queue_push(defect)
        defect = _defects(project).get(pk=defect.pk)
        return Response(DefectSerializer(defect, context={'request': request}).data)

    def delete(self, request, slug, project_slug, defect_id):
        team, project, defect = self._get_defect(slug, project_slug, defect_id, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Non autorisé.'}, status=status.HTTP_403_FORBIDDEN)
        _log_activity(project, team, request.user, 'defect_deleted',
                      f'Anomalie {defect.ticket_number} "{defect.title}" supprimée')
        defect.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DefectCommentListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def get(self, request, slug, project_slug, defect_id):
        _, _, defect = DefectDetailView()._get_defect(slug, project_slug, defect_id, request.user)
        data = DefectCommentSerializer(defect.comments.select_related('author'), many=True).data
        return Response({'results': data, 'count': len(data)})

    def post(self, request, slug, project_slug, defect_id):
        team, project, defect = DefectDetailView()._get_defect(slug, project_slug, defect_id, request.user)
        _require_write(request.user, project)
        s = DefectCommentSerializer(data=request.data)
        if not s.is_valid():
            return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)
        comment = s.save(defect=defect, author=request.user)
        jira.queue_comment(comment)
        interested = {u for u in (defect.assigned_to, defect.reported_by) if u and u != request.user}
        _log_activity(project, team, request.user, 'defect_comment',
                      f'Commentaire sur {defect.ticket_number} "{defect.title}"', target_users=list(interested))
        return Response(DefectCommentSerializer(comment).data, status=status.HTTP_201_CREATED)


class DefectHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug, defect_id):
        _, _, defect = DefectDetailView()._get_defect(slug, project_slug, defect_id, request.user)
        data = DefectHistorySerializer(defect.history.select_related('actor'), many=True).data
        return Response({'results': data, 'count': len(data)})


class DefectStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        sprints = _sprints_param(request, project)
        return Response(analytics.defect_stats(analytics.scope_defects(project, sprints)))


class RegressionListView(APIView):
    """Cas précédemment « Passé » puis « Échoué », avec leurs anomalies."""
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        rows = analytics.list_regressions(project, _sprints_param(request, project))
        return Response({'results': rows, 'count': len(rows), 'active': sum(r['is_active'] for r in rows)})


# ─────────────────────────────────────────────────────────────────────────────
# MEMBRES ASSIGNABLES, PERFORMANCE, ÉVOLUTION & RAPPORTS
# ─────────────────────────────────────────────────────────────────────────────

def _sprints_param(request, project):
    """`?sprints=id1,id2` (ou paramètre répété) → liste de Sprint du projet ; absent → None (projet entier)."""
    raw = [v.strip() for part in request.query_params.getlist('sprints') for v in part.split(',') if v.strip()]
    if not raw:
        return None
    ids = {_uuid_or_400(v, 'sprints') for v in raw}
    sprints = list(Sprint.objects.filter(project=project, id__in=ids).order_by('start_date', 'created_at'))
    if len(sprints) != len(ids):
        raise _bad_request('sprints', "Un ou plusieurs sprints n'appartiennent pas à ce projet.")
    return sprints


class AssignableMembersView(APIView):
    """Tous les membres de l'équipe pouvant recevoir des cas de test du projet."""
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        results = [{
            'user': UserPublicSerializer(tm.user).data,
            'team_role': tm.role,
            'project_member_id': str(pm.id) if pm else None,
            'in_project': pm is not None,
        } for tm, pm in assignable_members(project)]
        return Response({'results': results, 'count': len(results)})


class ProjectPerformanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        sprints = _sprints_param(request, project)
        return Response({
            **analytics.project_performance(project, sprints),
            'sprints': analytics.sprint_trend(project, sprints),
        })


class ProjectEvolutionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        return Response(analytics.project_evolution(project, _sprints_param(request, project)))


class TeamPerformanceView(APIView):
    """Comparaison du niveau de performance et de l'évolution de chaque projet visible de l'équipe."""
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(request.user):
            return Response({'error': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)
        results = []
        for project in Project.objects.filter(team=team).select_related('team'):
            if not can_view_project(request.user, project):
                continue
            evolution = analytics.project_evolution(project)
            results.append({
                'project': {'id': str(project.id), 'name': project.name, 'slug': project.slug,
                            'color': project.color, 'status': project.status},
                'performance': analytics.project_performance(project),
                'trend': evolution['trend'],
                'evolution': [{'date': p['date'], 'success_rate': p['success_rate'],
                               'execution_rate': p['execution_rate']} for p in evolution['points']],
            })
        results.sort(key=lambda r: (r['performance']['score'] is None, -(r['performance']['score'] or 0)))
        return Response({'results': results, 'count': len(results)})


def _team_for_reports(slug, user):
    team = get_object_or_404(Team, slug=slug)
    if not team.is_member(user):
        raise PermissionDenied("Vous n'êtes pas membre de cette équipe.")
    return team


def _portfolio_projects(request, team):
    """
    `?projects=id1,id2` (ou paramètre répété) → projets visibles de l'équipe, dans l'ordre alphabétique.
    Absent → tous les projets visibles. Un projet inconnu, d'une autre équipe ou non visible → 400.
    """
    visible = [p for p in Project.objects.filter(team=team).select_related('team').order_by('name')
               if can_view_project(request.user, p)]
    raw = [v.strip() for part in request.query_params.getlist('projects') for v in part.split(',') if v.strip()]
    if not raw:
        projects = visible
    else:
        ids = {_uuid_or_400(v, 'projects') for v in raw}
        projects = [p for p in visible if p.id in ids]
        if len(projects) != len(ids):
            raise _bad_request('projects', "Un ou plusieurs projets n'appartiennent pas à cette équipe ou ne vous sont pas accessibles.")
    if not projects:
        raise _bad_request('projects', 'Sélectionnez au moins un projet.')
    return projects


class TeamPortfolioView(APIView):
    """Aperçu du rapport global : niveau de performance de chaque projet choisi et de l'ensemble."""
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        team = _team_for_reports(slug, request.user)
        projects = _portfolio_projects(request, team)
        rows = [(p, analytics.project_performance(p)) for p in projects]
        overall = analytics.portfolio_performance([perf for _, perf in rows])
        return Response({
            'team': {'name': team.name, 'slug': team.slug},
            'performance': overall,
            'projects': [{'id': str(p.id), 'name': p.name, 'slug': p.slug, 'status': p.status,
                          'status_label': p.get_status_display(), 'sprints': p.sprints.count(), 'performance': perf}
                         for p, perf in rows],
        })


class TeamPortfolioPDFView(APIView):
    """Rapport PDF global des projets choisis (`?projects=` ; tous les projets visibles par défaut)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        from apps.teams.reports import generate_portfolio_report
        team = _team_for_reports(slug, request.user)
        projects = _portfolio_projects(request, team)
        pdf = generate_portfolio_report(team, projects, request.user.get_full_name() or request.user.username)
        _log_activity(None, team, request.user, 'portfolio_report',
                      f'Rapport global généré pour {len(projects)} projet(s) : {", ".join(p.name for p in projects)}')
        suffix = slugify(projects[0].name) if len(projects) == 1 else f'{len(projects)}-projets'
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = (
            f'attachment; filename="rapport-global-{team.slug}-{suffix}-{timezone.localdate():%Y%m%d}.pdf"')
        return response


class ProjectReportView(APIView):
    """Rapport (JSON) d'un projet, d'un sprint ou d'une sélection de sprints."""
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        return Response(analytics.build_report(project, _sprints_param(request, project)))


def _report_pdf_response(request, project, sprints):
    from apps.teams.reports import generate_project_report
    pdf = generate_project_report(project, sprints, request.user.get_full_name() or request.user.username)
    if sprints is None:
        suffix = 'projet'
    elif len(sprints) == 1:
        suffix = slugify(sprints[0].name) or 'sprint'
    else:
        suffix = f'{len(sprints)}-sprints'
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="rapport-{project.slug}-{suffix}-{timezone.localdate():%Y%m%d}.pdf"')
    return response


class ProjectReportPDFView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        return _report_pdf_response(request, project, _sprints_param(request, project))


class SprintReportPDFView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug, sprint_id):
        _, project, sprint = SprintDetailView()._get_sprint(slug, project_slug, sprint_id, request.user)
        return _report_pdf_response(request, project, [sprint])


# ─────────────────────────────────────────────────────────────────────────────
# WEEKLY REPORTS
# ─────────────────────────────────────────────────────────────────────────────

class WeeklyReportListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(request.user):
            return Response({'error': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)

        target_user_id = _int_or_400(request.query_params.get('user_id', request.user.id), 'user_id')
        if target_user_id != request.user.id and not team.is_admin(request.user):
            return Response({'error': "Seuls les responsables peuvent consulter les rapports d'un autre membre."},
                            status=status.HTTP_403_FORBIDDEN)
        reports = WeeklyReport.objects.filter(team=team, user_id=target_user_id).select_related('user')
        s = WeeklyReportSerializer(reports, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})

    def post(self, request, slug):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(request.user):
            return Response({'error': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)

        # Auto-compute week start/end if not provided
        data = request.data.copy()
        today = timezone.localdate()
        data['week_start'] = _date_or_400(data.get('week_start'), 'week_start') or (
            today - timedelta(days=today.weekday()))
        data['week_end'] = _date_or_400(data.get('week_end'), 'week_end') or (
            data['week_start'] + timedelta(days=6))
        wants_submit = data.get('status') == WeeklyReport.Status.SUBMITTED

        report, created = WeeklyReport.objects.get_or_create(
            user=request.user,
            team=team,
            week_start=data['week_start'],
            defaults={
                'week_end': data['week_end'],
                'accomplishments': data.get('accomplishments', ''),
                'blockers': data.get('blockers', ''),
                'next_week_plans': data.get('next_week_plans', ''),
                'additional_notes': data.get('additional_notes', ''),
            }
        )
        already_submitted = report.status == WeeklyReport.Status.SUBMITTED
        if not created:
            for field in ['accomplishments', 'blockers', 'next_week_plans', 'additional_notes']:
                if field in data:
                    setattr(report, field, data[field])
        if wants_submit and not already_submitted:
            report.submit()
        elif not created:
            report.save()

        if report.status == 'submitted' and not already_submitted:
            # Notify team leads/admins
            admin_members = TeamMember.objects.filter(
                team=team, role__in=['owner', 'admin']
            ).exclude(user=request.user).values_list('user', flat=True)
            admin_users = User.objects.filter(id__in=admin_members)
            _log_activity(
                None, team, request.user, 'weekly_report_submitted',
                f'{request.user.username} a soumis son rapport hebdomadaire (semaine du {report.week_start})',
                target_users=list(admin_users)
            )

        return Response(WeeklyReportSerializer(report, context={'request': request}).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class WeeklyReportDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_report(self, slug, report_id, user):
        team = get_object_or_404(Team, slug=slug)
        report = get_object_or_404(WeeklyReport, id=report_id, team=team)
        if report.user != user and not team.is_admin(user):
            raise PermissionDenied("Vous ne pouvez pas consulter ce rapport.")
        return team, report

    def get(self, request, slug, report_id):
        _, report = self._get_report(slug, report_id, request.user)
        return Response(WeeklyReportSerializer(report, context={'request': request}).data)

    def patch(self, request, slug, report_id):
        team, report = self._get_report(slug, report_id, request.user)
        if report.user != request.user:
            return Response({'error': 'Seul son auteur peut modifier un rapport.'}, status=status.HTTP_403_FORBIDDEN)
        allowed = ['accomplishments', 'blockers', 'next_week_plans', 'additional_notes']
        for field in allowed:
            if field in request.data:
                setattr(report, field, request.data[field])
        if request.data.get('status') == 'submitted' and report.status == 'draft':
            report.submit()
            admin_members = TeamMember.objects.filter(
                team=team, role__in=['owner', 'admin']
            ).exclude(user=request.user).values_list('user', flat=True)
            admin_users = User.objects.filter(id__in=admin_members)
            _log_activity(
                None, team, request.user, 'weekly_report_submitted',
                f'{request.user.username} a soumis son rapport hebdomadaire',
                target_users=list(admin_users)
            )
        else:
            report.save()
        return Response(WeeklyReportSerializer(report, context={'request': request}).data)


class WeeklyReportCurrentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(request.user):
            return Response({'error': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        report, _ = WeeklyReport.objects.get_or_create(
            user=request.user,
            team=team,
            week_start=week_start,
            defaults={'week_end': week_start + timedelta(days=6)}
        )
        return Response(WeeklyReportSerializer(report, context={'request': request}).data)


# ─────────────────────────────────────────────────────────────────────────────
# MEMBER PROGRESS (for team leads)
# ─────────────────────────────────────────────────────────────────────────────

class MemberProgressView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        members = ProjectMember.objects.filter(project=project).select_related('user')
        result = []
        for pm in members:
            assignments = _assignments(assigned_to=pm)
            total = assignments.count()
            passed = assignments.filter(status='Passé').count()
            failed = assignments.filter(status='Échoué').count()
            blocked = assignments.filter(status='Bloqué').count()
            in_progress = assignments.filter(status='En cours').count()
            not_run = assignments.filter(status='À tester').count()
            result.append({
                'user': UserPublicSerializer(pm.user).data,
                'total_ucs': total,
                'passed': passed,
                'failed': failed,
                'blocked': blocked,
                'in_progress': in_progress,
                'not_run': not_run,
                'progress_pct': round((passed + failed) / total * 100, 1) if total > 0 else 0,
                'assigned_ucs': UseCaseAssignmentSerializer(assignments, many=True, context={'request': request}).data,
            })
        return Response({'results': result})


# ─────────────────────────────────────────────────────────────────────────────
# ACTIVITY / NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

def _visible_activities(user):
    """Notifications ciblées + activité des équipes de l'utilisateur, hors projets privés inaccessibles."""
    from apps.teams.models import TeamMember
    memberships = TeamMember.objects.filter(user=user)
    managed = memberships.filter(role__in=['owner', 'admin']).values('team_id')
    visible_project = (
        Q(project__isnull=True) | ~Q(project__visibility='private')
        | Q(project__team_id__in=managed)
        | Q(project_id__in=ProjectMember.objects.filter(user=user).values('project_id'))
    )
    ids = ActivityLog.objects.filter(
        Q(target_users=user) | (Q(team_id__in=memberships.values('team_id')) & visible_project)
    ).values('id')
    return ActivityLog.objects.filter(id__in=ids)


def _unread(activities, user):
    return activities.exclude(read_by=user).exclude(actor=user)


class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = _visible_activities(request.user)
        unread_count = _unread(qs, request.user).count()
        activities = qs.select_related('actor', 'project').prefetch_related('read_by')[:50]
        s = ActivityLogSerializer(activities, many=True, context={'request': request})
        return Response({
            'results': s.data,
            'unread_count': unread_count,
        })


class NotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, notification_id):
        activity = get_object_or_404(_visible_activities(request.user), id=notification_id)
        activity.read_by.add(request.user)
        ActivityLog.objects.filter(pk=activity.pk).update(is_read=True)
        return Response({'detail': 'Marqué comme lu.'})


class MarkAllNotificationsReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ids = list(_unread(_visible_activities(request.user), request.user).values_list('id', flat=True))
        request.user.read_notifications.add(*ids)
        ActivityLog.objects.filter(id__in=ids).update(is_read=True)
        return Response({'detail': 'Tout marqué comme lu.'})


class TeamActivityView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(request.user):
            return Response({'error': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)
        activities = ActivityLog.objects.filter(team=team).select_related('actor', 'project')[:30]
        s = ActivityLogSerializer(activities, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})


class ProjectActivityView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        activities = ActivityLog.objects.filter(project=project).select_related('actor')[:30]
        s = ActivityLogSerializer(activities, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})


# ─────────────────────────────────────────────────────────────────────────────
# UNASSIGNED USE CASES (for a project)
# ─────────────────────────────────────────────────────────────────────────────

class UnassignedUseCasesView(APIView):
    """
    Cas assignables. Avec `?sprint=` : cas sans responsable dans ce sprint, à l'exclusion des cas
    déjà planifiés dans un autre sprint (ils se reportent via « Reporter les cas »).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        cases = ExtractedUseCase.objects.filter(project=project).order_by('case_number')
        sprint_id = request.query_params.get('sprint')
        hidden = 0
        if sprint_id:
            sprint = get_object_or_404(Sprint, id=_uuid_or_400(sprint_id, 'sprint'), project=project)
            assigned_here = UseCaseAssignment.objects.filter(sprint=sprint, assigned_to__isnull=False)
            cases = cases.exclude(id__in=assigned_here.values('use_case_id'))
            elsewhere = cases_planned_elsewhere(project, sprint)
            hidden = cases.filter(id__in=elsewhere).count()
            cases = cases.exclude(id__in=elsewhere)
        else:
            cases = cases.exclude(id__in=UseCaseAssignment.objects.filter(
                use_case__project=project).values('use_case_id'))
        from apps.teams.serializers import ExtractedUseCaseSerializerBasic
        data = ExtractedUseCaseSerializerBasic(cases, many=True).data
        return Response({'results': data, 'count': len(data), 'hidden_other_sprints': hidden})


# ─────────────────────────────────────────────────────────────────────────────
# USE CASE DETAIL (with full info + screenshots)
# ─────────────────────────────────────────────────────────────────────────────

class UseCaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug, assignment_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        assignment = get_object_or_404(_assignments(use_case__project=project), id=assignment_id)
        uc = assignment.use_case
        from apps.api.serializers import ExtractedUseCaseSerializer
        screenshots = UseCaseScreenshot.objects.filter(assignment=assignment)
        comments = UseCaseComment.objects.filter(use_case=uc)
        return Response({
            'assignment': UseCaseAssignmentSerializer(assignment, context={'request': request}).data,
            'use_case': ExtractedUseCaseSerializer(uc).data,
            'screenshots': UseCaseScreenshotSerializer(screenshots, many=True, context={'request': request}).data,
            'comments': UseCaseCommentSerializer(comments, many=True).data,
        })


# ─────────────────────────────────────────────────────────────────────────────
# USE CASE SCREENSHOTS
# ─────────────────────────────────────────────────────────────────────────────

class UseCaseScreenshotListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request, slug, project_slug, assignment_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        assignment = get_object_or_404(UseCaseAssignment, id=assignment_id, use_case__project=project)
        screenshots = UseCaseScreenshot.objects.filter(assignment=assignment)
        s = UseCaseScreenshotSerializer(screenshots, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})

    def post(self, request, slug, project_slug, assignment_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        _require_write(request.user, project)
        assignment = get_object_or_404(UseCaseAssignment, id=assignment_id, use_case__project=project)
        if not request.FILES.get('image'):
            return Response({'error': 'Image requise.'}, status=status.HTTP_400_BAD_REQUEST)
        s = UseCaseScreenshotSerializer(data={'assignment': assignment.id, 'image': request.FILES['image'],
                                              'caption': request.data.get('caption', '')})
        if not s.is_valid():
            return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)
        screenshot = s.save(uploaded_by=request.user)
        _log_activity(project, team, request.user, 'screenshot_uploaded',
                      f'Capture ajoutée à UC#{assignment.use_case.order}',
                      metadata={'assignment_id': str(assignment.id), 'screenshot_id': str(screenshot.id)})
        return Response(UseCaseScreenshotSerializer(screenshot, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)


class UseCaseScreenshotDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, slug, project_slug, assignment_id, screenshot_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        _require_write(request.user, project)
        screenshot = get_object_or_404(UseCaseScreenshot, id=screenshot_id, assignment_id=assignment_id,
                                       assignment__use_case__project=project)
        screenshot.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────────────────
# SPRINT CSV EXPORT
# ─────────────────────────────────────────────────────────────────────────────

class SprintCSVExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug, sprint_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        sprint = get_object_or_404(Sprint, id=sprint_id, project=project)
        assignments = _assignments(sprint=sprint).order_by('use_case__case_number')

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['N°', 'CAS', 'Use Case ID', 'Description', 'Préconditions',
                         'Étapes', 'Résultats Attendus', 'Résultats Observés',
                         'Assigné à', 'Statut', 'Automatisé'])

        for a in assignments:
            uc = a.use_case
            writer.writerow([
                uc.case_number,
                f"CAS-{uc.case_number:03d}",
                uc.use_case_text,
                uc.description,
                uc.preconditions,
                uc.steps,
                uc.expected_results,
                uc.observed_results,
                (a.assigned_to.user.get_full_name() or a.assigned_to.user.username) if a.assigned_to_id else 'Non assigné',
                a.effective_status,
                'Oui' if uc.is_automated else 'Non',
            ])

        response = HttpResponse(output.getvalue(), content_type='text/csv; charset=utf-8')
        name = slugify(sprint.name) or 'sprint'
        response['Content-Disposition'] = f'attachment; filename="recettes_{name}_{sprint.start_date}.csv"'
        return response
