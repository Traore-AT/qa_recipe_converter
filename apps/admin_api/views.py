"""
Super-Admin API views — panorama global du système.

Toutes les routes sont restreintes aux superusers (voir permissions.IsSuperAdmin).
Chaque action de mutation est tracée dans ActivityLog avec un action_type préfixé
par 'admin_'.
"""
import random
import string
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Q, Count
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView, ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.teams.models import Team, TeamMember, Project
from apps.core.models import ConversionJob, ExtractedUseCase
from apps.qamanagement.models import Sprint, Defect, WeeklyReport, ActivityLog

from .permissions import IsSuperAdmin
from .serializers import (
    AdminUserSerializer, AdminUserCreateSerializer, AdminResetPasswordSerializer,
    AdminTeamSerializer, TransferOwnerSerializer,
    AdminProjectSerializer, AdminJobSerializer, AdminActivitySerializer,
    AdminUseCaseSerializer, AdminSprintSerializer,
    AdminDefectSerializer, AdminReportSerializer,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _log_admin(request, action_type, description, team=None, project=None, metadata=None):
    ActivityLog.objects.create(
        actor=request.user,
        action_type=action_type,
        description=description,
        metadata=metadata or {},
        team=team,
        project=project,
    )


def _fmt_bytes(n: int) -> str:
    for unit in ('o', 'Ko', 'Mo', 'Go', 'To'):
        if n < 1024:
            return f'{n:.1f} {unit}' if unit != 'o' else f'{n} {unit}'
        n /= 1024
    return f'{n:.1f} Po'


def _media_stats():
    root = settings.MEDIA_ROOT
    categories = {}
    total_size = 0
    total_files = 0
    if root and Path(root).exists():
        for path in Path(root).rglob('*'):
            if path.is_file():
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                total_size += size
                total_files += 1
                rel = path.relative_to(root).parts
                cat = rel[0] if rel else 'other'
                categories[cat] = categories.get(cat, 0) + size
    db_file = settings.DATABASES['default']['NAME']
    db_size = Path(db_file).stat().st_size if Path(db_file).exists() else 0
    return {
        'media': {
            'total_files': total_files,
            'total_bytes': total_size,
            'total_bytes_human': _fmt_bytes(total_size),
            'categories': categories,
        },
        'database': {
            'bytes': db_size,
            'bytes_human': _fmt_bytes(db_size),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY (KPIs globaux)
# ─────────────────────────────────────────────────────────────────────────────
class AdminSummaryView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        last_24h = timezone.now() - timedelta(hours=24)

        users = User.objects.all()
        ucs = ExtractedUseCase.objects.all()
        jobs = ConversionJob.objects.all()
        defects = Defect.objects.all()
        sprints = Sprint.objects.all()
        reports = WeeklyReport.objects.all()

        success_rate = 0.0
        if ucs.count():
            success_rate = round(ucs.filter(status='Passé').count() / ucs.count() * 100, 1)

        summary = {
            'users': {
                'total': users.count(),
                'active': users.filter(is_active=True).count(),
                'admins': users.filter(Q(is_staff=True) | Q(is_superuser=True)).count(),
                'recent': users.filter(date_joined__gte=last_24h).count(),
            },
            'teams': {
                'total': Team.objects.count(),
                'new_24h': Team.objects.filter(created_at__gte=last_24h).count(),
            },
            'projects': {
                'total': Project.objects.count(),
                'active': Project.objects.filter(status='active').count(),
                'archived': Project.objects.filter(status='archived').count(),
            },
            'jobs': {
                'total': jobs.count(),
                'pending': jobs.filter(status='PENDING').count(),
                'processing': jobs.filter(status='PROCESSING').count(),
                'done': jobs.filter(status='DONE').count(),
                'error': jobs.filter(status='ERROR').count(),
                'recent_24h': jobs.filter(created_at__gte=last_24h).count(),
            },
            'use_cases': {
                'total': ucs.count(),
                'passed': ucs.filter(status='Passé').count(),
                'failed': ucs.filter(status='Échoué').count(),
                'blocked': ucs.filter(status='Bloqué').count(),
                'in_progress': ucs.filter(status='En cours').count(),
                'automated': ucs.filter(is_automated=True).count(),
                'success_rate': success_rate,
            },
            'defects': {
                'total': defects.count(),
                'open': defects.filter(status__in=['open', 'reopened']).count(),
                'critical': defects.filter(severity='critical').count(),
                'resolved': defects.filter(status='resolved').count(),
            },
            'sprints': {
                'total': sprints.count(),
                'active': sprints.filter(status='active').count(),
            },
            'reports': {
                'total': reports.count(),
                'submitted': reports.filter(status='submitted').count(),
            },
            'activity': ActivityLog.objects.filter(created_at__gte=last_24h).count(),
            'storage': _media_stats(),
        }

        recent = ActivityLog.objects.select_related('actor', 'team', 'project').order_by('-created_at')[:6]
        summary['recent_activity'] = AdminActivitySerializer(recent, many=True).data
        return Response(summary)


# ─────────────────────────────────────────────────────────────────────────────
# STATISTICS (agrégats temporels)
# ─────────────────────────────────────────────────────────────────────────────
class AdminStatisticsView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        try:
            days = min(int(request.query_params.get('days', 30)), 90)
        except (TypeError, ValueError):
            days = 30
        start = timezone.now() - timedelta(days=days)

        def daily(model_qs, field):
            return list(
                model_qs.filter(**{f'{field}__gte': start})
                .annotate(date=TruncDate(field))
                .values('date')
                .annotate(count=Count('id'))
                .order_by('date')
            )

        def status_breakdown(model_qs, field):
            return list(model_qs.values(field).annotate(count=Count('id')))

        return Response({
            'users': daily(User.objects.all(), 'date_joined'),
            'conversions': daily(ConversionJob.objects.all(), 'created_at'),
            'teams': daily(Team.objects.all(), 'created_at'),
            'projects': daily(Project.objects.all(), 'created_at'),
            'jobs_by_status': status_breakdown(ConversionJob.objects.all(), 'status'),
            'uc_by_status': status_breakdown(ExtractedUseCase.objects.all(), 'status'),
            'defects_by_status': status_breakdown(Defect.objects.all(), 'status'),
            'defects_by_severity': status_breakdown(Defect.objects.all(), 'severity'),
        })


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM (état du stockage / base)
# ─────────────────────────────────────────────────────────────────────────────
class AdminSystemView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        data = _media_stats()
        data['last_cleanup'] = 'Voir commande : python manage.py cleanup_old_files'
        return Response(data)


# ─────────────────────────────────────────────────────────────────────────────
# USERS
# ─────────────────────────────────────────────────────────────────────────────
class AdminUserListView(ListCreateAPIView):
    permission_classes = [IsSuperAdmin]
    serializer_class = AdminUserSerializer
    ordering = ('-date_joined',)

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return AdminUserCreateSerializer
        return AdminUserSerializer

    def get_queryset(self):
        qs = User.objects.all().order_by('-date_joined')
        query = self.request.query_params.get('search', '').strip()
        if query:
            qs = qs.filter(
                Q(username__icontains=query)
                | Q(email__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
            )
        active = self.request.query_params.get('active')
        if active in ('true', 'false'):
            qs = qs.filter(is_active=active == 'true')
        staff = self.request.query_params.get('staff')
        if staff in ('true', 'false'):
            qs = qs.filter(is_staff=staff == 'true')
        return qs

    def perform_create(self, serializer):
        user = serializer.save()
        _log_admin(self.request, 'admin_user_created',
                   f"Création du compte « {user.username} » par l'administration.")


class AdminUserDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsSuperAdmin]
    serializer_class = AdminUserSerializer
    queryset = User.objects.all()

    def _raise_superuser_guards(self, instance, is_superuser, is_active):
        if instance == self.request.user:
            if not is_superuser:
                raise ValidationError("Vous ne pouvez pas retirer votre propre accès super-admin.")
            if not is_active:
                raise ValidationError("Vous ne pouvez pas désactiver votre propre compte.")
        if instance.is_superuser and not is_superuser:
            remaining = User.objects.filter(is_superuser=True).exclude(pk=instance.pk).count()
            if remaining == 0:
                raise ValidationError("Impossible de retirer le statut super-admin du dernier super-admin.")

    def perform_update(self, serializer):
        instance = serializer.instance
        data = serializer.validated_data
        self._raise_superuser_guards(
            instance,
            is_superuser=data.get('is_superuser', instance.is_superuser),
            is_active=data.get('is_active', instance.is_active),
        )
        serializer.save()
        _log_admin(self.request, 'admin_user_updated',
                   f"Compte « {instance.username} » mis à jour (id={instance.pk}).",
                   metadata={k: str(v) for k, v in data.items() if k in ('is_active', 'is_staff', 'is_superuser')})

    def perform_destroy(self, instance):
        if instance == self.request.user:
            raise ValidationError("Vous ne pouvez pas supprimer votre propre compte.")
        if instance.is_superuser and User.objects.filter(is_superuser=True).count() <= 1:
            raise ValidationError("Impossible de supprimer le dernier super-admin.")
        if instance.owned_teams.exists():
            raise ValidationError(
                "Cet utilisateur possède des équipes. Transférez leur propriété avant la suppression.")
        username = instance.username
        _log_admin(self.request, 'admin_user_deleted', f"Suppression du compte « {username} ».")
        instance.delete()


class AdminResetPasswordView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        serializer = AdminResetPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        generated = not serializer.validated_data.get('password')
        password = serializer.validated_data.get('password') or ''.join(
            random.choices(string.ascii_letters + string.digits, k=12)
        )
        user.set_password(password)
        user.save(update_fields=['password'])
        _log_admin(request, 'admin_user_password_reset',
                   f"Mot de passe réinitialisé pour « {user.username} ».")
        payload = {'detail': 'Mot de passe réinitialisé.'}
        if generated:
            payload['temp_password'] = password
        return Response(payload)


# ─────────────────────────────────────────────────────────────────────────────
# TEAMS
# ─────────────────────────────────────────────────────────────────────────────
class AdminTeamListView(ListAPIView):
    permission_classes = [IsSuperAdmin]
    serializer_class = AdminTeamSerializer

    def get_queryset(self):
        qs = Team.objects.select_related('owner').order_by('-created_at')
        query = self.request.query_params.get('search', '').strip()
        if query:
            qs = qs.filter(name__icontains=query)
        return qs


class AdminTeamDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsSuperAdmin]
    serializer_class = AdminTeamSerializer
    queryset = Team.objects.all()
    lookup_field = 'slug'

    def perform_update(self, serializer):
        instance = serializer.save()
        _log_admin(self.request, 'admin_team_updated',
                   f"Équipe « {instance.name} » mise à jour.")

    def perform_destroy(self, instance):
        name = instance.name
        _log_admin(self.request, 'admin_team_deleted', f"Suppression de l'équipe « {name} ».")
        instance.delete()


class AdminTeamTransferView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, slug):
        team = get_object_or_404(Team, slug=slug)
        serializer = TransferOwnerSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        new_owner = User.objects.get(pk=serializer.validated_data['user_id'])
        if not team.is_member(new_owner):
            raise ValidationError("Le nouvel propriétaire doit déjà être membre de l'équipe.")

        old_owner = team.owner

        old_member = TeamMember.objects.filter(team=team, user=old_owner).first()
        if old_member:
            old_member.role = TeamMember.Role.ADMIN
            old_member.save()

        new_member = TeamMember.objects.filter(team=team, user=new_owner).first()
        if not new_member:
            new_member = TeamMember.objects.create(team=team, user=new_owner, role=TeamMember.Role.OWNER)
        else:
            new_member.role = TeamMember.Role.OWNER
            new_member.save()

        team.owner = new_owner
        team.save(update_fields=['owner', 'updated_at'])

        _log_admin(request, 'admin_team_transferred',
                   f"Propriété de l'équipe « {team.name} » transférée de « {old_owner.username} » "
                   f"à « {new_owner.username} ».")
        return Response(AdminTeamSerializer(team, context={'request': request}).data)


# ─────────────────────────────────────────────────────────────────────────────
# PROJECTS
# ─────────────────────────────────────────────────────────────────────────────
class AdminProjectListView(ListAPIView):
    permission_classes = [IsSuperAdmin]
    serializer_class = AdminProjectSerializer

    def get_queryset(self):
        qs = Project.objects.select_related('team', 'created_by').order_by('-created_at')
        query = self.request.query_params.get('search', '').strip()
        if query:
            qs = qs.filter(Q(name__icontains=query) | Q(team__name__icontains=query))
        status_val = self.request.query_params.get('status')
        if status_val:
            qs = qs.filter(status=status_val)
        return qs


class AdminProjectDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsSuperAdmin]
    serializer_class = AdminProjectSerializer
    queryset = Project.objects.select_related('team', 'created_by').all()

    def perform_update(self, serializer):
        instance = serializer.save()
        _log_admin(self.request, 'admin_project_updated',
                   f"Projet « {instance.name} » mis à jour.")

    def perform_destroy(self, instance):
        name = instance.name
        _log_admin(self.request, 'admin_project_deleted', f"Suppression du projet « {name} ».")
        instance.delete()


# ─────────────────────────────────────────────────────────────────────────────
# JOBS
# ─────────────────────────────────────────────────────────────────────────────
class AdminJobListView(ListAPIView):
    permission_classes = [IsSuperAdmin]
    serializer_class = AdminJobSerializer

    def get_queryset(self):
        qs = ConversionJob.objects.select_related('uploaded_by', 'project__team').order_by('-created_at')
        query = self.request.query_params.get('search', '').strip()
        if query:
            qs = qs.filter(Q(source_filename__icontains=query) | Q(project__name__icontains=query))
        job_status = self.request.query_params.get('status')
        if job_status:
            qs = qs.filter(status=job_status.upper())
        return qs


class AdminJobDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsSuperAdmin]
    serializer_class = AdminJobSerializer
    queryset = ConversionJob.objects.select_related('uploaded_by', 'project__team').all()

    def perform_destroy(self, instance):
        filename = instance.source_filename
        _log_admin(self.request, 'admin_job_deleted',
                   f"Suppression de la conversion « {filename} ».")
        instance.delete()


# ─────────────────────────────────────────────────────────────────────────────
# ACTIVITY (journal global)
# ─────────────────────────────────────────────────────────────────────────────
class AdminActivityListView(ListAPIView):
    permission_classes = [IsSuperAdmin]
    serializer_class = AdminActivitySerializer

    def get_queryset(self):
        qs = ActivityLog.objects.select_related('actor', 'team', 'project').order_by('-created_at')
        action_type = self.request.query_params.get('action_type', '').strip()
        if action_type:
            qs = qs.filter(action_type=action_type)
        actor_id = self.request.query_params.get('user_id', '').strip()
        if actor_id:
            qs = qs.filter(actor_id=actor_id)
        return qs


# ─────────────────────────────────────────────────────────────────────────────
# USE CASES (vue globale des cas extraits)
# ─────────────────────────────────────────────────────────────────────────────
class AdminUseCaseListView(ListAPIView):
    permission_classes = [IsSuperAdmin]
    serializer_class = AdminUseCaseSerializer

    def get_queryset(self):
        qs = ExtractedUseCase.objects.select_related('job__project__team')\
            .order_by('-job__created_at', 'order')
        query = self.request.query_params.get('search', '').strip()
        if query:
            qs = qs.filter(Q(use_case_text__icontains=query) | Q(description__icontains=query))
        status_val = self.request.query_params.get('status', '').strip()
        if status_val:
            qs = qs.filter(status=status_val)
        automated = self.request.query_params.get('automated', '').strip()
        if automated in ('true', 'false'):
            qs = qs.filter(is_automated=automated == 'true')
        project_id = self.request.query_params.get('project', '').strip()
        if project_id:
            qs = qs.filter(job__project_id=project_id)
        return qs


# ─────────────────────────────────────────────────────────────────────────────
# SPRINTS (vue globale)
# ─────────────────────────────────────────────────────────────────────────────
class AdminSprintListView(ListAPIView):
    permission_classes = [IsSuperAdmin]
    serializer_class = AdminSprintSerializer

    def get_queryset(self):
        qs = Sprint.objects.select_related('project__team', 'created_by').order_by('-start_date')
        query = self.request.query_params.get('search', '').strip()
        if query:
            qs = qs.filter(Q(name__icontains=query) | Q(project__name__icontains=query))
        status_val = self.request.query_params.get('status', '').strip()
        if status_val:
            qs = qs.filter(status=status_val)
        return qs


# ─────────────────────────────────────────────────────────────────────────────
# DEFECTS (vue globale des anomalies)
# ─────────────────────────────────────────────────────────────────────────────
class AdminDefectListView(ListAPIView):
    permission_classes = [IsSuperAdmin]
    serializer_class = AdminDefectSerializer

    def get_queryset(self):
        qs = Defect.objects.select_related('project__team', 'reported_by', 'assigned_to', 'use_case')\
            .order_by('-created_at')
        query = self.request.query_params.get('search', '').strip()
        if query:
            qs = qs.filter(Q(title__icontains=query) | Q(description__icontains=query))
        status_val = self.request.query_params.get('status', '').strip()
        if status_val:
            qs = qs.filter(status=status_val)
        severity = self.request.query_params.get('severity', '').strip()
        if severity:
            qs = qs.filter(severity=severity)
        return qs


# ─────────────────────────────────────────────────────────────────────────────
# WEEKLY REPORTS (vue globale)
# ─────────────────────────────────────────────────────────────────────────────
class AdminReportListView(ListAPIView):
    permission_classes = [IsSuperAdmin]
    serializer_class = AdminReportSerializer

    def get_queryset(self):
        qs = WeeklyReport.objects.select_related('user', 'team').order_by('-week_start')
        query = self.request.query_params.get('search', '').strip()
        if query:
            qs = qs.filter(Q(user__username__icontains=query) | Q(team__name__icontains=query))
        status_val = self.request.query_params.get('status', '').strip()
        if status_val:
            qs = qs.filter(status=status_val)
        return qs