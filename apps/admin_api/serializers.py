"""
Serializers for the Super-Admin space.

Views croisés sur l'ensemble du système : utilisateurs, équipes, projets,
tâches de conversion, journal d'activité global.
"""
import uuid
from django.contrib.auth.models import User
from rest_framework import serializers

from apps.teams.models import Team, Project
from apps.core.models import ConversionJob, ExtractedUseCase
from apps.qamanagement.models import Sprint, Defect, WeeklyReport
from apps.teams.serializers import UserPublicSerializer
from apps.qamanagement.serializers import ActivityLogSerializer


# ─────────────────────────────────────────────────────────────────────────────
# USER
# ─────────────────────────────────────────────────────────────────────────────
class AdminUserSerializer(serializers.ModelSerializer):
    full_name     = serializers.SerializerMethodField()
    teams_count   = serializers.SerializerMethodField()
    projects_count = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'full_name',
                  'is_active', 'is_staff', 'is_superuser', 'last_login', 'date_joined',
                  'teams_count', 'projects_count']
        read_only_fields = ['id', 'last_login', 'date_joined', 'teams_count', 'projects_count']

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_teams_count(self, obj):
        return Team.objects.filter(members__user=obj).distinct().count()

    def get_projects_count(self, obj):
        return Project.objects.filter(members__user=obj).distinct().count()


class AdminUserCreateSerializer(serializers.ModelSerializer):
    password  = serializers.CharField(min_length=8, write_only=True)
    full_name = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'full_name',
                  'password', 'is_active', 'is_staff', 'is_superuser', 'date_joined']
        read_only_fields = ['id', 'date_joined']
        extra_kwargs = {
            'email': {'required': True},
            'is_active': {'default': True},
            'is_staff': {'default': False},
            'is_superuser': {'default': False},
        }

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Ce nom d'utilisateur est déjà pris.")
        return value

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Cet email est déjà utilisé.")
        return value

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class AdminResetPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(min_length=8, required=False, allow_blank=True)


# ─────────────────────────────────────────────────────────────────────────────
# TEAM
# ─────────────────────────────────────────────────────────────────────────────
class AdminTeamSerializer(serializers.ModelSerializer):
    owner          = UserPublicSerializer(read_only=True)
    members        = serializers.SerializerMethodField()
    members_count  = serializers.SerializerMethodField()
    projects_count = serializers.SerializerMethodField()

    class Meta:
        model  = Team
        fields = ['id', 'name', 'slug', 'description', 'owner', 'members',
                  'members_count', 'projects_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'slug', 'owner', 'members', 'created_at', 'updated_at']

    def get_members(self, obj):
        users = User.objects.filter(team_memberships__team=obj).order_by('username')
        return UserPublicSerializer(users, many=True, read_only=True).data

    def get_members_count(self, obj):
        return obj.members.count()

    def get_projects_count(self, obj):
        return obj.projects.count()


class TransferOwnerSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()

    def validate_user_id(self, value):
        if not User.objects.filter(id=value).exists():
            raise serializers.ValidationError('Utilisateur introuvable.')
        return value


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT
# ─────────────────────────────────────────────────────────────────────────────
class AdminProjectSerializer(serializers.ModelSerializer):
    team_name     = serializers.CharField(source='team.name', read_only=True)
    team_slug     = serializers.CharField(source='team.slug', read_only=True)
    created_by    = UserPublicSerializer(read_only=True)
    stats         = serializers.SerializerMethodField()
    defects_count = serializers.SerializerMethodField()

    class Meta:
        model  = Project
        fields = ['id', 'name', 'slug', 'description',
                  'team_name', 'team_slug', 'created_by',
                  'visibility', 'status', 'color', 'stats', 'defects_count',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'slug', 'team_name', 'team_slug', 'created_by',
                            'created_at', 'updated_at']

    def get_stats(self, obj):
        return obj.stats

    def get_defects_count(self, obj):
        return obj.defects.count()


# ─────────────────────────────────────────────────────────────────────────────
# JOB
# ─────────────────────────────────────────────────────────────────────────────
class AdminJobSerializer(serializers.ModelSerializer):
    uploaded_by = UserPublicSerializer(read_only=True)
    project_name = serializers.SerializerMethodField()
    team_name    = serializers.SerializerMethodField()

    class Meta:
        model  = ConversionJob
        fields = ['id', 'source_filename', 'status', 'uploaded_by',
                  'project', 'project_name', 'team_name',
                  'use_cases_count', 'error_message', 'created_at', 'updated_at']
        read_only_fields = ['id', 'source_filename', 'status', 'uploaded_by',
                            'project', 'use_cases_count', 'created_at', 'updated_at']

    def get_project_name(self, obj):
        return obj.project.name if obj.project_id else None

    def get_team_name(self, obj):
        return obj.project.team.name if obj.project_id and obj.project.team_id else None


# ─────────────────────────────────────────────────────────────────────────────
# ACTIVITY
# ─────────────────────────────────────────────────────────────────────────────
class AdminActivitySerializer(ActivityLogSerializer):
    team_name      = serializers.SerializerMethodField()
    project_title  = serializers.SerializerMethodField()

    class Meta(ActivityLogSerializer.Meta):
        fields = ['id', 'project', 'project_title', 'team', 'team_name', 'actor',
                  'action_type', 'description', 'metadata', 'created_at', 'is_read']

    def get_team_name(self, obj):
        return obj.team.name if obj.team_id else None

    def get_project_title(self, obj):
        return obj.project.name if obj.project_id else None


# ─────────────────────────────────────────────────────────────────────────────
# USE CASES (cas de test extraits)
# ─────────────────────────────────────────────────────────────────────────────
class AdminUseCaseSerializer(serializers.ModelSerializer):
    source_filename = serializers.SerializerMethodField()
    project_id      = serializers.SerializerMethodField()
    project_name    = serializers.SerializerMethodField()
    team_name       = serializers.SerializerMethodField()
    assigned_users  = serializers.SerializerMethodField()

    class Meta:
        model  = ExtractedUseCase
        fields = ['id', 'order', 'use_case_text', 'description', 'status',
                  'is_automated', 'job', 'source_filename',
                  'project_id', 'project_name', 'team_name', 'assigned_users']
        read_only_fields = fields

    def get_source_filename(self, obj):
        return obj.job.source_filename if obj.job_id else None

    def get_project_id(self, obj):
        return obj.job.project_id if obj.job_id else None

    def get_project_name(self, obj):
        return obj.job.project.name if obj.job_id and obj.job.project_id else None

    def get_team_name(self, obj):
        if obj.job_id and obj.job.project_id and obj.job.project.team_id:
            return obj.job.project.team.name
        return None

    def get_assigned_users(self, obj):
        return [
            a.assigned_to.user.username
            for a in obj.assignments.select_related('assigned_to__user').order_by('assigned_at')
        ]


# ─────────────────────────────────────────────────────────────────────────────
# SPRINTS
# ─────────────────────────────────────────────────────────────────────────────
class AdminSprintSerializer(serializers.ModelSerializer):
    project_name  = serializers.CharField(source='project.name', read_only=True)
    project_slug  = serializers.CharField(source='project.slug', read_only=True)
    team_name     = serializers.CharField(source='project.team.name', read_only=True)
    duration_days = serializers.IntegerField(read_only=True)
    stats         = serializers.SerializerMethodField()

    class Meta:
        model  = Sprint
        fields = ['id', 'name', 'goal', 'status', 'project', 'project_name',
                  'project_slug', 'team_name', 'start_date', 'end_date',
                  'duration_days', 'stats', 'created_at']
        read_only_fields = fields

    def get_stats(self, obj):
        return obj.stats


# ─────────────────────────────────────────────────────────────────────────────
# DEFECTS (anomalies)
# ─────────────────────────────────────────────────────────────────────────────
class AdminDefectSerializer(serializers.ModelSerializer):
    project_name   = serializers.CharField(source='project.name', read_only=True)
    team_name      = serializers.CharField(source='project.team.name', read_only=True)
    use_case_order = serializers.SerializerMethodField()
    reported_by    = UserPublicSerializer(read_only=True)
    assigned_to    = UserPublicSerializer(read_only=True)

    class Meta:
        model  = Defect
        fields = ['id', 'title', 'status', 'severity', 'priority',
                  'project', 'project_name', 'team_name',
                  'use_case', 'use_case_order',
                  'reported_by', 'assigned_to', 'created_at', 'updated_at']
        read_only_fields = fields

    def get_use_case_order(self, obj):
        return obj.use_case.order if obj.use_case_id else None


# ─────────────────────────────────────────────────────────────────────────────
# WEEKLY REPORTS (rapports hebdomadaires)
# ─────────────────────────────────────────────────────────────────────────────
class AdminReportSerializer(serializers.ModelSerializer):
    user      = UserPublicSerializer(read_only=True)
    team_name = serializers.CharField(source='team.name', read_only=True)

    class Meta:
        model  = WeeklyReport
        fields = ['id', 'user', 'team', 'team_name', 'week_start', 'week_end',
                  'status', 'submitted_at', 'created_at']
        read_only_fields = fields