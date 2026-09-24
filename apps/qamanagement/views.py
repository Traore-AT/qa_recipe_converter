import csv
import io
import logging
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from datetime import date, timedelta
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from .models import Sprint, UseCaseAssignment, Defect, WeeklyReport, ActivityLog, UseCaseComment, UseCaseScreenshot
from .serializers import (
    SprintSerializer, SprintCreateSerializer,
    UseCaseAssignmentSerializer, CreateAssignmentSerializer,
    DefectSerializer, DefectCreateSerializer,
    WeeklyReportSerializer,
    ActivityLogSerializer, UseCaseCommentSerializer,
    MemberProgressSerializer, SprintBoardSerializer, DefectStatsSerializer,
    UseCaseScreenshotSerializer,
)
from apps.teams.models import Team, Project, ProjectMember, TeamMember
from apps.teams.permissions import can_view_project, can_manage_project, can_write_project
from apps.core.models import ExtractedUseCase
from apps.teams.serializers import UserPublicSerializer, ProjectSerializer

logger = logging.getLogger(__name__)


def _get_team_and_project(slug, project_slug, user):
    team = get_object_or_404(Team, slug=slug)
    if not team.is_member(user):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("Vous n'êtes pas membre de cette équipe.")
    project = get_object_or_404(Project, team=team, slug=project_slug)
    if not can_view_project(user, project):
        raise PermissionDenied("Accès refusé à ce projet.")
    return team, project


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
        allowed = ['name', 'goal', 'start_date', 'end_date', 'status']
        for field in allowed:
            if field in request.data:
                setattr(sprint, field, request.data[field])
        sprint.save()
        _log_activity(project, team, request.user, 'sprint_updated',
                      f'Sprint "{sprint.name}" mis à jour → {sprint.get_status_display()}')
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
        assignments = UseCaseAssignment.objects.filter(sprint=sprint).select_related(
            'use_case', 'assigned_to__user', 'assigned_by'
        )
        columns = {
            'À tester': [],
            'En cours': [],
            'Passé': [],
            'Échoué': [],
            'Bloqué': [],
        }
        for a in assignments:
            status_key = a.status
            if status_key not in columns:
                status_key = 'À tester'
            columns[status_key].append(UseCaseAssignmentSerializer(a, context={'request': request}).data)
        return Response({
            'sprint': SprintSerializer(sprint, context={'request': request}).data,
            'columns': columns,
            'stats': sprint.stats,
        })


# ─────────────────────────────────────────────────────────────────────────────
# USE CASE ASSIGNMENTS
# ─────────────────────────────────────────────────────────────────────────────

class AssignmentListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        assignments = UseCaseAssignment.objects.filter(
            use_case__job__project=project
        ).select_related('use_case', 'assigned_to__user', 'assigned_by')

        sprint_f = request.query_params.get('sprint')
        if sprint_f:
            assignments = assignments.filter(sprint_id=sprint_f)
        user_f = request.query_params.get('user_id')
        if user_f:
            assignments = assignments.filter(assigned_to__user_id=user_f)

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
        user_id = s.validated_data['user_id']

        try:
            target_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({'error': 'Utilisateur introuvable.'}, status=status.HTTP_404_NOT_FOUND)

        pm = ProjectMember.objects.filter(project=project, user=target_user).first()
        if not pm:
            return Response({'error': 'Cet utilisateur n\'est pas membre du projet.'},
                            status=status.HTTP_400_BAD_REQUEST)

        sprint = None
        if sprint_id:
            sprint = get_object_or_404(Sprint, id=sprint_id, project=project)

        ucs = ExtractedUseCase.objects.filter(id__in=use_case_ids, job__project=project)
        created = []
        errors = []
        created_assignments = []
        for uc in ucs:
            assignment, was_created = UseCaseAssignment.objects.get_or_create(
                use_case=uc,
                assigned_to=pm,
                defaults={
                    'sprint': sprint,
                    'assigned_by': request.user,
                    'status': uc.status,
                }
            )
            if was_created:
                created.append(str(uc.id))
                created_assignments.append(assignment)
            else:
                errors.append(f'UC#{uc.order} déjà assigné à ce membre')

        _log_activity(project, team, request.user, 'assignments_created',
                      f'{len(created)} cas assigné(s) à {target_user.username}',
                      target_users=[target_user])

        # Send email notification for each assignment
        if sprint and created_assignments:
            try:
                from apps.teams.email import send_sprint_assignment_email
                for assignment in created_assignments:
                    send_sprint_assignment_email(assignment, sprint, request.user)
            except Exception:
                logger.exception("Failed to send assignment email")

        return Response({'created': created, 'count': len(created), 'errors': errors},
                        status=status.HTTP_201_CREATED)


class AssignmentDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, slug, project_slug, assignment_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Non autorisé.'}, status=status.HTTP_403_FORBIDDEN)
        assignment = get_object_or_404(UseCaseAssignment, id=assignment_id,
                                       use_case__job__project=project)
        assignment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────────────────
# USE CASE EXECUTION (status update + comment)
# ─────────────────────────────────────────────────────────────────────────────

class UseCaseStatusUpdateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def patch(self, request, slug, project_slug, uc_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        uc = get_object_or_404(ExtractedUseCase, id=uc_id, job__project=project)

        new_status = request.data.get('status')
        valid_statuses = ['À tester', 'En cours', 'Passé', 'Échoué', 'Bloqué']
        if new_status not in valid_statuses:
            return Response({'error': f'Statut invalide. Valeurs: {", ".join(valid_statuses)}'},
                            status=status.HTTP_400_BAD_REQUEST)

        old_status = uc.status
        uc.status = new_status
        uc.observed_results = request.data.get('observed_results', uc.observed_results)
        uc.save()

        # Sync assigned UC status
        updated = UseCaseAssignment.objects.filter(use_case=uc, assigned_to__user=request.user).update(status=new_status)

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
        })


class UseCaseJiraUpdateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def patch(self, request, slug, project_slug, uc_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        uc = get_object_or_404(ExtractedUseCase, id=uc_id, job__project=project)

        uc.jira_ticket = (request.data.get('jira_ticket') or '').strip()
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
        uc = get_object_or_404(ExtractedUseCase, id=uc_id, job__project=project)
        comments = UseCaseComment.objects.filter(use_case=uc).select_related('author')
        s = UseCaseCommentSerializer(comments, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})

    def post(self, request, slug, project_slug, uc_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        uc = get_object_or_404(ExtractedUseCase, id=uc_id, job__project=project)
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

class DefectListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        defects = Defect.objects.filter(project=project).select_related(
            'reported_by', 'assigned_to', 'use_case'
        )

        status_f = request.query_params.get('status')
        if status_f:
            defects = defects.filter(status=status_f)
        severity_f = request.query_params.get('severity')
        if severity_f:
            defects = defects.filter(severity=severity_f)
        priority_f = request.query_params.get('priority')
        if priority_f:
            defects = defects.filter(priority=priority_f)
        assigned_f = request.query_params.get('assigned_to')
        if assigned_f:
            defects = defects.filter(assigned_to_id=assigned_f)

        s = DefectSerializer(defects, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})

    def post(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        s = DefectCreateSerializer(data=request.data, context={'request': request})
        if s.is_valid():
            defect = s.save(project=project, reported_by=request.user)
            _log_activity(project, team, request.user, 'defect_created',
                          f'Anomalie signalée : {defect.title} ({defect.get_severity_display()})',
                          target_users=[defect.assigned_to] if defect.assigned_to else None)
            return Response(DefectSerializer(defect, context={'request': request}).data,
                            status=status.HTTP_201_CREATED)
        return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)


class DefectDetailView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _get_defect(self, slug, project_slug, defect_id, user):
        team, project = _get_team_and_project(slug, project_slug, user)
        defect = get_object_or_404(Defect, id=defect_id, project=project)
        return team, project, defect

    def get(self, request, slug, project_slug, defect_id):
        _, _, defect = self._get_defect(slug, project_slug, defect_id, request.user)
        return Response(DefectSerializer(defect, context={'request': request}).data)

    def patch(self, request, slug, project_slug, defect_id):
        team, project, defect = self._get_defect(slug, project_slug, defect_id, request.user)
        allowed = ['title', 'description', 'severity', 'priority', 'status',
                   'steps_to_reproduce', 'expected_behavior',
                   'actual_behavior', 'environment']
        old_status = defect.status
        for field in allowed:
            if field in request.data:
                setattr(defect, field, request.data[field])
        if 'assigned_to' in request.data:
            defect.assigned_to_id = request.data['assigned_to']
        if 'attachment' in request.FILES:
            defect.attachment = request.FILES['attachment']
        defect.save()

        if old_status != defect.status:
            interested = []
            if defect.assigned_to:
                interested.append(defect.assigned_to)
            if defect.reported_by and defect.reported_by != request.user:
                interested.append(defect.reported_by)
            _log_activity(project, team, request.user, 'defect_status',
                          f'Anomalie "{defect.title}" : {dict(Defect.Status.choices).get(old_status, old_status)} → {defect.get_status_display()}',
                          target_users=list(set(interested)))

        return Response(DefectSerializer(defect, context={'request': request}).data)

    def delete(self, request, slug, project_slug, defect_id):
        team, project, defect = self._get_defect(slug, project_slug, defect_id, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Non autorisé.'}, status=status.HTTP_403_FORBIDDEN)
        defect.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DefectStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        defects = Defect.objects.filter(project=project)
        total = defects.count()
        data = {
            'total': total,
            'open': defects.filter(status='open').count(),
            'in_progress': defects.filter(status='in_progress').count(),
            'resolved': defects.filter(status='resolved').count(),
            'closed': defects.filter(status='closed').count(),
            'critical': defects.filter(severity='critical').count(),
            'major': defects.filter(severity='major').count(),
            'minor': defects.filter(severity='minor').count(),
            'by_severity': {
                s: defects.filter(severity=s).count()
                for s in ['critical', 'major', 'minor', 'trivial']
            },
            'by_priority': {
                p: defects.filter(priority=p).count()
                for p in ['high', 'medium', 'low']
            },
        }
        return Response(data)


# ─────────────────────────────────────────────────────────────────────────────
# WEEKLY REPORTS
# ─────────────────────────────────────────────────────────────────────────────

class WeeklyReportListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(request.user):
            return Response({'error': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)

        target_user_id = request.query_params.get('user_id', request.user.id)
        reports = WeeklyReport.objects.filter(team=team, user_id=target_user_id).select_related('user')
        s = WeeklyReportSerializer(reports, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})

    def post(self, request, slug):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(request.user):
            return Response({'error': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)

        # Auto-compute week start/end if not provided
        data = request.data.copy()
        if 'week_start' not in data:
            today = date.today()
            data['week_start'] = today - timedelta(days=today.weekday())
        if 'week_end' not in data:
            ws = date.fromisoformat(str(data['week_start'])) if isinstance(data['week_start'], str) else data['week_start']
            data['week_end'] = (ws if isinstance(ws, date) else ws) + timedelta(days=6)

        if isinstance(data['week_start'], str):
            data['week_start'] = date.fromisoformat(data['week_start'])
        if isinstance(data['week_end'], str):
            data['week_end'] = date.fromisoformat(data['week_end'])

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
                'status': data.get('status', 'draft'),
            }
        )
        if not created:
            for field in ['accomplishments', 'blockers', 'next_week_plans', 'additional_notes']:
                if field in data:
                    setattr(report, field, data[field])
            if 'status' in data and data['status'] == 'submitted':
                report.submit()
            else:
                report.save()

        if report.status == 'submitted':
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
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Vous ne pouvez pas modifier ce rapport.")
        return team, report

    def get(self, request, slug, report_id):
        _, report = self._get_report(slug, report_id, request.user)
        return Response(WeeklyReportSerializer(report, context={'request': request}).data)

    def patch(self, request, slug, report_id):
        team, report = self._get_report(slug, report_id, request.user)
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
            assignments = UseCaseAssignment.objects.filter(assigned_to=pm).select_related('use_case')
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

class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = ActivityLog.objects.filter(
            Q(target_users=request.user) | Q(team__isnull=False)
        ).distinct().select_related('actor', 'project')
        unread_count = qs.filter(is_read=False).count()
        activities = qs[:50]
        s = ActivityLogSerializer(activities, many=True, context={'request': request})
        return Response({
            'results': s.data,
            'unread_count': unread_count,
        })


class NotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, notification_id):
        activity = get_object_or_404(ActivityLog, id=notification_id)
        activity.is_read = True
        activity.save()
        return Response({'detail': 'Marqué comme lu.'})


class MarkAllNotificationsReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ActivityLog.objects.filter(target_users=request.user, is_read=False).update(is_read=True)
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
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        all_ucs = ExtractedUseCase.objects.filter(job__project=project)
        assigned_uc_ids = UseCaseAssignment.objects.filter(
            use_case__job__project=project
        ).values_list('use_case_id', flat=True)
        unassigned = all_ucs.exclude(id__in=assigned_uc_ids)
        from apps.teams.serializers import ExtractedUseCaseSerializerBasic
        s = ExtractedUseCaseSerializerBasic(unassigned, many=True)
        return Response({'results': s.data, 'count': len(s.data)})


# ─────────────────────────────────────────────────────────────────────────────
# USE CASE DETAIL (with full info + screenshots)
# ─────────────────────────────────────────────────────────────────────────────

class UseCaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug, assignment_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        assignment = get_object_or_404(UseCaseAssignment, id=assignment_id,
                                       use_case__job__project=project)
        uc = assignment.use_case
        from apps.api.serializers import ExtractedUseCaseSerializer
        screenshots = UseCaseScreenshot.objects.filter(assignment=assignment)
        comments = UseCaseComment.objects.filter(use_case=uc)
        return Response({
            'assignment': UseCaseAssignmentSerializer(assignment, context={'request': request}).data,
            'use_case': ExtractedUseCaseSerializer(uc).data,
            'screenshots': UseCaseScreenshotSerializer(screenshots, many=True).data,
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
        assignment = get_object_or_404(UseCaseAssignment, id=assignment_id,
                                       use_case__job__project=project)
        screenshots = UseCaseScreenshot.objects.filter(assignment=assignment)
        s = UseCaseScreenshotSerializer(screenshots, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})

    def post(self, request, slug, project_slug, assignment_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        assignment = get_object_or_404(UseCaseAssignment, id=assignment_id,
                                       use_case__job__project=project)
        image = request.FILES.get('image')
        if not image:
            return Response({'error': 'Image requise.'}, status=status.HTTP_400_BAD_REQUEST)
        screenshot = UseCaseScreenshot.objects.create(
            assignment=assignment,
            image=image,
            caption=request.data.get('caption', ''),
            uploaded_by=request.user,
        )
        _log_activity(project, team, request.user, 'screenshot_uploaded',
                      f'Capture ajoutée à UC#{assignment.use_case.order}',
                      metadata={'assignment_id': str(assignment.id), 'screenshot_id': str(screenshot.id)})
        return Response(UseCaseScreenshotSerializer(screenshot, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)


class UseCaseScreenshotDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, slug, project_slug, assignment_id, screenshot_id):
        team, project = _get_team_and_project(slug, project_slug, request.user)
        screenshot = get_object_or_404(UseCaseScreenshot, id=screenshot_id,
                                       assignment__use_case__job__project=project)
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
        assignments = UseCaseAssignment.objects.filter(sprint=sprint).select_related(
            'use_case', 'assigned_to__user'
        ).order_by('use_case__order')

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['N°', 'CAS', 'Use Case ID', 'Description', 'Préconditions',
                         'Étapes', 'Résultats Attendus', 'Résultats Observés',
                         'Assigné à', 'Statut', 'Automatisé'])

        for a in assignments:
            uc = a.use_case
            writer.writerow([
                uc.order,
                f"UC-{uc.order:03d}",
                uc.use_case_text,
                uc.description,
                uc.preconditions,
                uc.steps,
                uc.expected_results,
                uc.observed_results,
                a.assigned_to.user.get_full_name() or a.assigned_to.user.username,
                a.status,
                'Oui' if uc.is_automated else 'Non',
            ])

        response = HttpResponse(output.getvalue(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="recettes_{sprint.name}_{sprint.start_date}.csv"'
        return response
