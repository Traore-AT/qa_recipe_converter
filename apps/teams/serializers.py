from django.contrib.auth.models import User
from rest_framework import serializers
from .models import Team, TeamMember, TeamInvitation, Project, ProjectMember


# ─────────────────────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────────────────────
class UserPublicSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = ['id', 'username', 'email', 'full_name', 'date_joined',
                  'is_active', 'is_staff', 'is_superuser']

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class RegisterSerializer(serializers.Serializer):
    username   = serializers.CharField(max_length=150)
    email      = serializers.EmailField()
    password   = serializers.CharField(min_length=8, write_only=True)
    first_name = serializers.CharField(max_length=150, required=False, default='')
    last_name  = serializers.CharField(max_length=150, required=False, default='')

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Ce nom d'utilisateur est déjà pris.")
        return value

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Cet email est déjà utilisé.")
        return value

    def create(self, validated_data):
        return User.objects.create_user(
            username   = validated_data['username'],
            email      = validated_data['email'],
            password   = validated_data['password'],
            first_name = validated_data.get('first_name', ''),
            last_name  = validated_data.get('last_name', ''),
        )


# ─────────────────────────────────────────────────────────────────────────────
# TEAM
# ─────────────────────────────────────────────────────────────────────────────
class TeamMemberSerializer(serializers.ModelSerializer):
    user     = UserPublicSerializer(read_only=True)
    user_id  = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source='user', write_only=True, required=False
    )

    class Meta:
        model  = TeamMember
        fields = ['id', 'user', 'user_id', 'role', 'joined_at']
        read_only_fields = ['id', 'joined_at']


class TeamSerializer(serializers.ModelSerializer):
    owner         = UserPublicSerializer(read_only=True)
    members_count = serializers.SerializerMethodField()
    my_role       = serializers.SerializerMethodField()
    avatar_url    = serializers.SerializerMethodField()

    class Meta:
        model  = Team
        fields = ['id', 'name', 'slug', 'description', 'owner',
                  'avatar_url', 'members_count', 'my_role',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'slug', 'owner', 'created_at', 'updated_at']

    def get_members_count(self, obj):
        return obj.members.count()

    def get_my_role(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            m = obj.get_member(request.user)
            if m:
                return m.role
            if obj.owner == request.user:
                return 'owner'
        return None

    def get_avatar_url(self, obj):
        request = self.context.get('request')
        if obj.avatar and request:
            return request.build_absolute_uri(obj.avatar.url)
        return None


class TeamCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Team
        fields = ['name', 'description', 'avatar']

    def create(self, validated_data):
        user = self.context['request'].user
        team = Team.objects.create(owner=user, **validated_data)
        # Auto-add owner as member with 'owner' role
        TeamMember.objects.create(team=team, user=user, role=TeamMember.Role.OWNER)
        return team


class TeamInvitationSerializer(serializers.ModelSerializer):
    invited_by = UserPublicSerializer(read_only=True)
    team_name  = serializers.CharField(source='team.name', read_only=True)
    team_slug  = serializers.CharField(source='team.slug', read_only=True)

    class Meta:
        model  = TeamInvitation
        fields = ['id', 'token', 'team_name', 'team_slug', 'invited_by',
                  'email', 'role', 'status', 'created_at', 'expires_at']
        read_only_fields = ['id', 'token', 'invited_by', 'status', 'created_at', 'expires_at']


class InviteMemberSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role  = serializers.ChoiceField(
        choices=TeamMember.Role.choices,
        default=TeamMember.Role.MEMBER
    )


class UpdateMemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=TeamMember.Role.choices)


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT
# ─────────────────────────────────────────────────────────────────────────────
class ProjectMemberSerializer(serializers.ModelSerializer):
    user    = UserPublicSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source='user', write_only=True, required=False
    )

    class Meta:
        model  = ProjectMember
        fields = ['id', 'user', 'user_id', 'role', 'added_at']
        read_only_fields = ['id', 'added_at']


class ProjectSerializer(serializers.ModelSerializer):
    created_by   = UserPublicSerializer(read_only=True)
    team_slug    = serializers.CharField(source='team.slug', read_only=True)
    team_name    = serializers.CharField(source='team.name', read_only=True)
    avatar_url   = serializers.SerializerMethodField()
    stats        = serializers.SerializerMethodField()
    my_role      = serializers.SerializerMethodField()

    class Meta:
        model  = Project
        fields = ['id', 'name', 'slug', 'description',
                  'team_slug', 'team_name', 'created_by',
                  'avatar_url', 'icon', 'color',
                  'visibility', 'status',
                  'stats', 'my_role',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'slug', 'created_by', 'team_slug', 'team_name',
                            'created_at', 'updated_at']

    def get_avatar_url(self, obj):
        request = self.context.get('request')
        if obj.avatar and request:
            return request.build_absolute_uri(obj.avatar.url)
        return None

    def get_stats(self, obj):
        return obj.stats

    def get_my_role(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            m = ProjectMember.objects.filter(project=obj, user=request.user).first()
            return m.role if m else None
        return None


class ProjectCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Project
        fields = ['name', 'description', 'icon', 'color', 'visibility', 'avatar']

    def validate_avatar(self, value):
        if value:
            allowed = ['image/jpeg', 'image/png', 'image/webp']
            if hasattr(value, 'content_type') and value.content_type not in allowed:
                raise serializers.ValidationError("Format non supporté. JPG, PNG, WEBP uniquement.")
            if value.size > 2 * 1024 * 1024:
                raise serializers.ValidationError("Avatar : taille maximale 2 MB.")
        return value

    def create(self, validated_data):
        user = self.context['request'].user
        team = self.context['team']
        project = Project.objects.create(team=team, created_by=user, **validated_data)
        ProjectMember.objects.create(project=project, user=user, role=ProjectMember.Role.LEAD)
        return project


class AddProjectMemberSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    role    = serializers.ChoiceField(choices=ProjectMember.Role.choices,
                                      default=ProjectMember.Role.TESTER)


class AssignProjectMembersSerializer(serializers.Serializer):
    members = serializers.ListField(child=serializers.DictField(), allow_empty=True)


class ProjectProgressSerializer(serializers.Serializer):
    project       = ProjectSerializer()
    use_cases     = serializers.ListField()
    status_counts = serializers.DictField()
    members       = ProjectMemberSerializer(many=True)


class ExtractedUseCaseSerializerBasic(serializers.Serializer):
    """Lightweight UC serializer for progress views."""
    id = serializers.UUIDField()
    order = serializers.IntegerField()
    use_case_text = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField()
    is_automated = serializers.BooleanField()


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
class TeamDashboardSerializer(serializers.Serializer):
    team            = TeamSerializer()
    stats           = serializers.DictField()
    projects        = ProjectSerializer(many=True)
    members         = TeamMemberSerializer(many=True)
    recent_activity = serializers.ListField()
