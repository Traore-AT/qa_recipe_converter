"""
Teams API views — all endpoints for Teams, Projects, Members, Invitations, Dashboard.
"""
import logging
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from .models import Team, TeamMember, TeamInvitation, Project, ProjectMember
from .serializers import (
    RegisterSerializer, UserPublicSerializer,
    TeamSerializer, TeamCreateSerializer,
    TeamMemberSerializer, TeamInvitationSerializer, InviteMemberSerializer,
    UpdateMemberRoleSerializer,
    ProjectSerializer, ProjectCreateSerializer,
    ProjectMemberSerializer, AddProjectMemberSerializer,
    ExtractedUseCaseSerializerBasic,
)
from apps.core.models import ExtractedUseCase
from .permissions import (
    can_view_project, can_write_project, can_manage_project, can_manage_team,
)
from .email import send_welcome_email, send_invitation_email, send_password_reset_email

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────────────────────
class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = RegisterSerializer(data=request.data)
        if s.is_valid():
            user = s.save()
            login(request, user)
            send_welcome_email(user)
            # Auto-accept pending invitations for this email
            pending = TeamInvitation.objects.filter(
                email=user.email, status=TeamInvitation.Status.PENDING
            ).exclude(expires_at__lt=timezone.now())
            for invitation in pending:
                if not invitation.team.is_member(user):
                    TeamMember.objects.create(
                        team=invitation.team,
                        user=user,
                        role=invitation.role,
                    )
                invitation.status = TeamInvitation.Status.ACCEPTED
                invitation.save()
            return Response(UserPublicSerializer(user).data, status=status.HTTP_201_CREATED)
        return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get('username') or request.data.get('email', '')
        password = request.data.get('password', '')

        # Allow login by email
        if '@' in username:
            try:
                username = User.objects.get(email=username).username
            except User.DoesNotExist:
                pass

        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response({'error': 'Identifiants invalides.'}, status=status.HTTP_401_UNAUTHORIZED)
        login(request, user)
        return Response(UserPublicSerializer(user).data)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response({'detail': 'Déconnecté.'})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserPublicSerializer(request.user).data)


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email', '')
        if not email:
            return Response({'error': 'Adresse email requise.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            user = User.objects.get(email=email)
            send_password_reset_email(user)
        except User.DoesNotExist:
            pass
        return Response({'detail': 'Si un compte existe avec cet email, un lien de réinitialisation a été envoyé.'})


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        uidb64 = request.data.get('uidb64', '')
        token = request.data.get('token', '')
        password = request.data.get('password', '')
        password_confirm = request.data.get('password_confirm', '')

        if not all([uidb64, token, password, password_confirm]):
            return Response({'error': 'Tous les champs sont requis.'}, status=status.HTTP_400_BAD_REQUEST)
        if password != password_confirm:
            return Response({'error': 'Les mots de passe ne correspondent pas.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(password) < 8:
            return Response({'error': 'Le mot de passe doit contenir au moins 8 caractères.'}, status=status.HTTP_400_BAD_REQUEST)

        from django.utils.http import urlsafe_base64_decode
        from django.contrib.auth.tokens import PasswordResetTokenGenerator
        try:
            uid = urlsafe_base64_decode(uidb64).decode()
            user = User.objects.get(pk=uid)
        except (User.DoesNotExist, ValueError, TypeError):
            return Response({'error': 'Lien de réinitialisation invalide.'}, status=status.HTTP_400_BAD_REQUEST)

        if not PasswordResetTokenGenerator().check_token(user, token):
            return Response({'error': 'Lien de réinitialisation invalide ou expiré.'}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(password)
        user.save()
        return Response({'detail': 'Mot de passe réinitialisé avec succès.'})


# ─────────────────────────────────────────────────────────────────────────────
# TEAMS — list / create
# ─────────────────────────────────────────────────────────────────────────────
class TeamListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        """List all teams the user belongs to."""
        memberships = TeamMember.objects.filter(user=request.user).select_related('team')
        teams = [m.team for m in memberships]
        s = TeamSerializer(teams, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})

    def post(self, request):
        """Create a new team; requester becomes owner."""
        s = TeamCreateSerializer(data=request.data, context={'request': request})
        if s.is_valid():
            team = s.save()
            return Response(TeamSerializer(team, context={'request': request}).data,
                            status=status.HTTP_201_CREATED)
        return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)


# ─────────────────────────────────────────────────────────────────────────────
# TEAMS — detail / update / delete
# ─────────────────────────────────────────────────────────────────────────────
class TeamDetailView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def _get_team(self, slug, user):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Vous n'êtes pas membre de cette équipe.")
        return team

    def get(self, request, slug):
        team = self._get_team(slug, request.user)
        return Response(TeamSerializer(team, context={'request': request}).data)

    def patch(self, request, slug):
        team = self._get_team(slug, request.user)
        if not team.is_admin(request.user):
            return Response({'error': 'Requiert rôle owner ou admin.'},
                            status=status.HTTP_403_FORBIDDEN)
        # Allow updating name, description, avatar
        allowed = {k: v for k, v in request.data.items() if k in ('name', 'description', 'avatar')}
        for k, v in allowed.items():
            setattr(team, k, v)
        if 'avatar' in request.FILES:
            team.avatar = request.FILES['avatar']
        team.save()
        return Response(TeamSerializer(team, context={'request': request}).data)

    def delete(self, request, slug):
        team = self._get_team(slug, request.user)
        if not team.is_owner(request.user):
            return Response({'error': 'Seul le propriétaire peut supprimer l\'équipe.'},
                            status=status.HTTP_403_FORBIDDEN)
        team.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────────────────
# TEAM MEMBERS
# ─────────────────────────────────────────────────────────────────────────────
class TeamMemberListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(request.user):
            return Response({'error': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)
        members = team.members.select_related('user').all()
        s = TeamMemberSerializer(members, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})

    def post(self, request, slug):
        """Invite a member by email."""
        team = get_object_or_404(Team, slug=slug)
        if not team.is_admin(request.user):
            return Response({'error': 'Requiert rôle owner ou admin.'},
                            status=status.HTTP_403_FORBIDDEN)

        s = InviteMemberSerializer(data=request.data)
        if not s.is_valid():
            return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)

        email = s.validated_data['email']
        role  = s.validated_data['role']

        # Check if user already member
        try:
            target_user = User.objects.get(email=email)
            if team.is_member(target_user):
                return Response({'error': 'Cet utilisateur est déjà membre.'},
                                status=status.HTTP_400_BAD_REQUEST)
        except User.DoesNotExist:
            target_user = None

        # Check for pending invitation
        existing = TeamInvitation.objects.filter(
            team=team, email=email, status='pending'
        ).first()
        if existing and existing.is_usable:
            return Response({'error': 'Une invitation est déjà en cours pour cet email.'},
                            status=status.HTTP_400_BAD_REQUEST)

        invitation = TeamInvitation.objects.create(
            team=team,
            invited_by=request.user,
            email=email,
            role=role,
        )

        send_invitation_email(invitation)

        return Response(TeamInvitationSerializer(invitation).data,
                        status=status.HTTP_201_CREATED)


class TeamMemberDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_member(self, slug, user_id):
        team   = get_object_or_404(Team, slug=slug)
        member = get_object_or_404(TeamMember, team=team, user_id=user_id)
        return team, member

    def patch(self, request, slug, user_id):
        """Change a member's role."""
        team, member = self._get_member(slug, user_id)
        if not team.is_admin(request.user):
            return Response({'error': 'Requiert rôle owner ou admin.'},
                            status=status.HTTP_403_FORBIDDEN)
        if member.user == team.owner:
            return Response({'error': 'Impossible de modifier le rôle du propriétaire.'},
                            status=status.HTTP_400_BAD_REQUEST)

        s = UpdateMemberRoleSerializer(data=request.data)
        if not s.is_valid():
            return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)

        member.role = s.validated_data['role']
        member.save()
        return Response(TeamMemberSerializer(member, context={'request': request}).data)

    def delete(self, request, slug, user_id):
        """Remove a member from the team."""
        team, member = self._get_member(slug, user_id)

        is_self_leave = (member.user == request.user)
        is_admin_action = team.is_admin(request.user)

        if not (is_self_leave or is_admin_action):
            return Response({'error': 'Action non autorisée.'}, status=status.HTTP_403_FORBIDDEN)

        if member.user == team.owner:
            return Response(
                {'error': 'Le propriétaire ne peut pas quitter l\'équipe sans transférer ce rôle.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        member.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────────────────
# INVITATIONS — details, accept, reject
# ─────────────────────────────────────────────────────────────────────────────
class InvitationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, token):
        invitation = get_object_or_404(TeamInvitation, token=token)
        if request.user.email != invitation.email:
            return Response(
                {'error': 'Cette invitation est destinée à une autre adresse email.'},
                status=status.HTTP_403_FORBIDDEN
            )
        serializer = TeamInvitationSerializer(invitation, context={'request': request})
        return Response(serializer.data)


class InvitationAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, token):
        invitation = get_object_or_404(TeamInvitation, token=token)

        if not invitation.is_usable:
            return Response(
                {'error': 'Cette invitation est expirée ou déjà traitée.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Auto-add user if email matches
        if request.user.email != invitation.email:
            return Response(
                {'error': 'Cette invitation est destinée à une autre adresse email.'},
                status=status.HTTP_403_FORBIDDEN
            )

        if invitation.team.is_member(request.user):
            invitation.status = TeamInvitation.Status.ACCEPTED
            invitation.save()
            return Response({'detail': 'Vous êtes déjà membre de cette équipe.'})

        TeamMember.objects.create(
            team=invitation.team,
            user=request.user,
            role=invitation.role,
        )
        invitation.status = TeamInvitation.Status.ACCEPTED
        invitation.save()

        return Response({
            'detail': f'Vous avez rejoint l\'équipe "{invitation.team.name}".',
            'team_slug': invitation.team.slug,
        })


class InvitationRejectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, token):
        invitation = get_object_or_404(TeamInvitation, token=token)
        if not invitation.is_usable:
            return Response({'error': 'Invitation expirée ou déjà traitée.'},
                            status=status.HTTP_400_BAD_REQUEST)
        invitation.status = TeamInvitation.Status.REJECTED
        invitation.save()
        return Response({'detail': 'Invitation refusée.'})


# ─────────────────────────────────────────────────────────────────────────────
# PROJECTS — list / create
# ─────────────────────────────────────────────────────────────────────────────
class ProjectListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def _get_team(self, slug, user):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Vous n'êtes pas membre de cette équipe.")
        return team

    def get(self, request, slug):
        team = self._get_team(slug, request.user)
        role = _get_team_role_str(request.user, team)

        qs = Project.objects.filter(team=team).select_related('created_by', 'team')

        # Apply status/visibility filters
        status_f     = request.query_params.get('status')
        visibility_f = request.query_params.get('visibility')
        if status_f:
            qs = qs.filter(status=status_f)
        if visibility_f:
            qs = qs.filter(visibility=visibility_f)

        # Apply visibility permission filtering
        visible = []
        for p in qs:
            if can_view_project(request.user, p):
                visible.append(p)

        s = ProjectSerializer(visible, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})

    def post(self, request, slug):
        team = self._get_team(slug, request.user)
        if not team.is_admin(request.user):
            return Response({'error': 'Seuls owner/admin peuvent créer un projet.'},
                            status=status.HTTP_403_FORBIDDEN)

        s = ProjectCreateSerializer(
            data=request.data,
            context={'request': request, 'team': team}
        )
        if s.is_valid():
            project = s.save()
            return Response(ProjectSerializer(project, context={'request': request}).data,
                            status=status.HTTP_201_CREATED)
        return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)


def _get_team_role_str(user, team):
    if team.owner == user:
        return 'owner'
    m = TeamMember.objects.filter(team=team, user=user).first()
    return m.role if m else None


# ─────────────────────────────────────────────────────────────────────────────
# PROJECTS — detail / update / delete
# ─────────────────────────────────────────────────────────────────────────────
class ProjectDetailView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def _get_project(self, team_slug, project_slug, user):
        team    = get_object_or_404(Team, slug=team_slug)
        project = get_object_or_404(Project, team=team, slug=project_slug)
        if not can_view_project(user, project):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Accès refusé à ce projet.")
        return project

    def get(self, request, slug, project_slug):
        project = self._get_project(slug, project_slug, request.user)
        return Response(ProjectSerializer(project, context={'request': request}).data)

    def patch(self, request, slug, project_slug):
        project = self._get_project(slug, project_slug, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Requiert rôle owner/admin ou lead projet.'},
                            status=status.HTTP_403_FORBIDDEN)
        if project.is_archived:
            return Response({'error': 'Projet archivé — lecture seule.'},
                            status=status.HTTP_400_BAD_REQUEST)

        allowed_fields = ['name', 'description', 'icon', 'color', 'visibility', 'status']
        for field in allowed_fields:
            if field in request.data:
                setattr(project, field, request.data[field])
        if 'avatar' in request.FILES:
            project.avatar = request.FILES['avatar']
        project.save()
        return Response(ProjectSerializer(project, context={'request': request}).data)

    def delete(self, request, slug, project_slug):
        project = self._get_project(slug, project_slug, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Requiert rôle owner/admin ou lead projet.'},
                            status=status.HTTP_403_FORBIDDEN)
        project.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProjectAvatarView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser]

    def patch(self, request, slug, project_slug):
        team    = get_object_or_404(Team, slug=slug)
        project = get_object_or_404(Project, team=team, slug=project_slug)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Non autorisé.'}, status=status.HTTP_403_FORBIDDEN)

        avatar = request.FILES.get('avatar')
        icon   = request.data.get('icon', '')
        color  = request.data.get('color', '')

        if avatar:
            allowed_types = ['image/jpeg', 'image/png', 'image/webp']
            if hasattr(avatar, 'content_type') and avatar.content_type not in allowed_types:
                return Response({'error': 'Format non supporté. JPG, PNG ou WEBP.'},
                                status=status.HTTP_400_BAD_REQUEST)
            if avatar.size > 2 * 1024 * 1024:
                return Response({'error': 'Avatar trop volumineux (max 2 MB).'},
                                status=status.HTTP_400_BAD_REQUEST)
            project.avatar = avatar

        if icon:
            project.icon = icon
            project.avatar = None  # icon replaces avatar
        if color:
            project.color = color

        project.save()
        return Response(ProjectSerializer(project, context={'request': request}).data)


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT MEMBERS
# ─────────────────────────────────────────────────────────────────────────────
class ProjectMemberListView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_project(self, team_slug, project_slug, user):
        team    = get_object_or_404(Team, slug=team_slug)
        project = get_object_or_404(Project, team=team, slug=project_slug)
        if not can_view_project(user, project):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Accès refusé.")
        return project

    def get(self, request, slug, project_slug):
        project = self._get_project(slug, project_slug, request.user)
        members = project.members.select_related('user').all()
        s = ProjectMemberSerializer(members, many=True, context={'request': request})
        return Response({'results': s.data, 'count': len(s.data)})

    def post(self, request, slug, project_slug):
        """Add a team member to the project."""
        project = self._get_project(slug, project_slug, request.user)
        if not can_manage_project(request.user, project):
            return Response({'error': 'Non autorisé.'}, status=status.HTTP_403_FORBIDDEN)

        s = AddProjectMemberSerializer(data=request.data)
        if not s.is_valid():
            return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)

        user_id = s.validated_data['user_id']
        role    = s.validated_data['role']

        try:
            target = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({'error': 'Utilisateur introuvable.'}, status=status.HTTP_404_NOT_FOUND)

        if not project.team.is_member(target):
            return Response({'error': 'Cet utilisateur n\'est pas membre de l\'équipe.'},
                            status=status.HTTP_400_BAD_REQUEST)

        pm, created = ProjectMember.objects.get_or_create(
            project=project, user=target,
            defaults={'role': role}
        )
        if not created:
            return Response({'error': 'Déjà membre du projet.'}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ProjectMemberSerializer(pm, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)


class ProjectMemberDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, slug, project_slug, user_id):
        team    = get_object_or_404(Team, slug=slug)
        project = get_object_or_404(Project, team=team, slug=project_slug)
        member  = get_object_or_404(ProjectMember, project=project, user_id=user_id)

        if not can_manage_project(request.user, project) and member.user != request.user:
            return Response({'error': 'Non autorisé.'}, status=status.HTTP_403_FORBIDDEN)

        member.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
class TeamDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(request.user):
            return Response({'error': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)

        # Visible projects
        projects_qs = Project.objects.filter(team=team).select_related('created_by', 'team')
        visible_projects = [p for p in projects_qs if can_view_project(request.user, p)]

        # Global stats
        from apps.core.models import ConversionJob, ExtractedUseCase
        job_ids = ConversionJob.objects.filter(
            project__in=visible_projects
        ).values_list('id', flat=True)

        ucs = ExtractedUseCase.objects.filter(job_id__in=job_ids)
        uc_total  = ucs.count()
        uc_passed = ucs.filter(status='Passé').count()
        success_rate = round(uc_passed / uc_total * 100, 1) if uc_total > 0 else 0

        proj_total     = len(visible_projects)
        proj_active    = sum(1 for p in visible_projects if p.status == 'active')
        proj_archived  = sum(1 for p in visible_projects if p.status == 'archived')
        proj_completed = sum(1 for p in visible_projects if p.status == 'completed')

        # Recent activity: last 10 ConversionJobs linked to team projects
        recent_jobs = ConversionJob.objects.filter(
            project__in=visible_projects
        ).order_by('-created_at')[:10]

        recent_activity = [
            {
                'type': 'conversion',
                'job_id': str(j.id),
                'filename': j.source_filename,
                'project': j.project.name if j.project else None,
                'use_cases_count': j.use_cases_count,
                'status': j.status,
                'date': j.created_at.isoformat(),
            }
            for j in recent_jobs
        ]

        members = team.members.select_related('user').all()

        return Response({
            'team': TeamSerializer(team, context={'request': request}).data,
            'stats': {
                'projects_total':     proj_total,
                'projects_active':    proj_active,
                'projects_archived':  proj_archived,
                'projects_completed': proj_completed,
                'use_cases_total':    uc_total,
                'use_cases_passed':   uc_passed,
                'success_rate':       success_rate,
                'members_count':      members.count(),
            },
            'projects':        ProjectSerializer(visible_projects, many=True, context={'request': request}).data,
            'members':         TeamMemberSerializer(members, many=True, context={'request': request}).data,
            'recent_activity': recent_activity,
        })


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT PROGRESS
# ─────────────────────────────────────────────────────────────────────────────
class ProjectProgressView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_slug):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(request.user):
            return Response({'error': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)
        project = get_object_or_404(Project, team=team, slug=project_slug)
        if not can_view_project(request.user, project):
            return Response({'error': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)

        ucs = ExtractedUseCase.objects.filter(job__project=project).order_by('order')
        statuses = ['À tester', 'En cours', 'Passé', 'Échoué', 'Bloqué']
        status_counts = {s: ucs.filter(status=s).count() for s in statuses}
        status_counts['total'] = ucs.count()
        status_counts['automated'] = ucs.filter(is_automated=True).count()

        members = ProjectMember.objects.filter(project=project).select_related('user')
        return Response({
            'project': ProjectSerializer(project, context={'request': request}).data,
            'use_cases': ExtractedUseCaseSerializerBasic(ucs, many=True).data,
            'status_counts': status_counts,
            'members': ProjectMemberSerializer(members, many=True, context={'request': request}).data,
        })


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT MEMBER ASSIGN
# ─────────────────────────────────────────────────────────────────────────────
class ProjectMemberAssignView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, slug, project_slug):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_admin(request.user):
            return Response({'error': 'Seuls les admins peuvent assigner des membres.'}, status=status.HTTP_403_FORBIDDEN)
        project = get_object_or_404(Project, team=team, slug=project_slug)

        from django.contrib.auth.models import User
        members_data = request.data.get('members', [])
        added = []
        for m in members_data:
            user_id = m.get('user_id')
            role = m.get('role', ProjectMember.Role.TESTER)
            try:
                user = User.objects.get(id=user_id)
                if not team.is_member(user):
                    continue
                _, created = ProjectMember.objects.get_or_create(project=project, user=user, defaults={'role': role})
                if created:
                    added.append({'user_id': user_id, 'role': role})
            except User.DoesNotExist:
                continue

        return Response({'added': added, 'count': len(added)})


# ─────────────────────────────────────────────────────────────────────────────
# REPORTS — Daily Scrum & Weekly PDF
# ─────────────────────────────────────────────────────────────────────────────
class DailyScrumReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(request.user):
            return Response({'error': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)

        from .reports import generate_daily_scrum
        pdf = generate_daily_scrum(team, generated_by=request.user.get_full_name() or request.user.username)

        from django.http import HttpResponse
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="daily-scrum-{team.slug}-{timezone.now():%Y%m%d}.pdf"'
        return response


class WeeklyReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        team = get_object_or_404(Team, slug=slug)
        if not team.is_member(request.user):
            return Response({'error': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)

        from .reports import generate_weekly_report
        pdf = generate_weekly_report(team, generated_by=request.user.get_full_name() or request.user.username)

        from django.http import HttpResponse
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="weekly-report-{team.slug}-{timezone.now():%Y%m%d}.pdf"'
        return response
