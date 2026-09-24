from datetime import date
from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Sprint, UseCaseAssignment, Defect, WeeklyReport, ActivityLog, UseCaseComment, UseCaseScreenshot
from apps.teams.serializers import UserPublicSerializer, ProjectSerializer


class SprintSerializer(serializers.ModelSerializer):
    created_by = UserPublicSerializer(read_only=True)
    stats      = serializers.SerializerMethodField()

    class Meta:
        model = Sprint
        fields = ['id', 'project', 'name', 'goal', 'start_date', 'end_date',
                  'status', 'created_by', 'duration_days', 'stats',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at', 'duration_days', 'stats']

    def get_stats(self, obj):
        return obj.stats

    def validate(self, data):
        if data.get('start_date') and data.get('end_date'):
            if data['end_date'] <= data['start_date']:
                raise serializers.ValidationError('La date de fin doit être après la date de début.')
        return data


class SprintCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sprint
        fields = ['name', 'goal', 'start_date', 'end_date', 'status']

    def validate(self, data):
        if data.get('start_date') and data.get('end_date'):
            if data['end_date'] <= data['start_date']:
                raise serializers.ValidationError('La date de fin doit être après la date de début.')
        return data


class UseCaseAssignmentSerializer(serializers.ModelSerializer):
    assigned_to_user   = UserPublicSerializer(source='assigned_to.user', read_only=True)
    assigned_by_user   = UserPublicSerializer(source='assigned_by', read_only=True)
    use_case_order     = serializers.IntegerField(source='use_case.order', read_only=True)
    use_case_id_str    = serializers.CharField(source='use_case.use_case_text', read_only=True)
    use_case_desc      = serializers.CharField(source='use_case.description', read_only=True)
    jira_ticket        = serializers.CharField(source='use_case.jira_ticket', read_only=True)
    jira_url           = serializers.SerializerMethodField()

    class Meta:
        model = UseCaseAssignment
        fields = ['id', 'use_case', 'use_case_order', 'use_case_id_str', 'use_case_desc',
                  'jira_ticket', 'jira_url',
                  'sprint', 'assigned_to', 'assigned_to_user', 'assigned_by',
                  'assigned_by_user', 'assigned_at', 'status']
        read_only_fields = ['id', 'assigned_by', 'assigned_at', 'status']

    def get_jira_url(self, obj):
        from django.conf import settings
        base = (getattr(settings, 'JIRA_BASE_URL', '') or '').rstrip('/')
        ticket = (obj.use_case.jira_ticket or '').strip()
        if base and ticket:
            return f'{base}/browse/{ticket}'
        return None


class CreateAssignmentSerializer(serializers.Serializer):
    use_case_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    sprint_id    = serializers.UUIDField(required=False, allow_null=True)
    user_id      = serializers.IntegerField()

    def validate_user_id(self, value):
        if not User.objects.filter(id=value).exists():
            raise serializers.ValidationError('Utilisateur introuvable.')
        return value


class DefectSerializer(serializers.ModelSerializer):
    reported_by_user = UserPublicSerializer(source='reported_by', read_only=True)
    assigned_to_user = UserPublicSerializer(source='assigned_to', read_only=True)

    class Meta:
        model = Defect
        fields = ['id', 'use_case', 'project', 'title', 'description',
                  'severity', 'priority', 'status',
                  'reported_by', 'reported_by_user',
                  'assigned_to', 'assigned_to_user',
                  'steps_to_reproduce', 'expected_behavior', 'actual_behavior',
                  'environment', 'attachment',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'reported_by', 'created_at', 'updated_at']

    def validate_attachment(self, value):
        if value:
            if value.size > 5 * 1024 * 1024:
                raise serializers.ValidationError('Fichier trop volumineux (max 5 MB).')
            allowed = ['image/jpeg', 'image/png', 'image/webp', 'application/pdf',
                       'text/plain', 'video/mp4']
            if hasattr(value, 'content_type') and value.content_type not in allowed:
                raise serializers.ValidationError('Format non supporté.')
        return value


class DefectCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Defect
        fields = ['use_case', 'title', 'description', 'severity', 'priority',
                  'assigned_to', 'steps_to_reproduce', 'expected_behavior',
                  'actual_behavior', 'environment', 'attachment']


class WeeklyReportSerializer(serializers.ModelSerializer):
    user       = UserPublicSerializer(read_only=True)
    user_id    = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source='user', write_only=True, required=False
    )
    week_label = serializers.SerializerMethodField()

    class Meta:
        model = WeeklyReport
        fields = ['id', 'user', 'user_id', 'team', 'week_start', 'week_end',
                  'week_label', 'accomplishments', 'blockers', 'next_week_plans',
                  'additional_notes', 'status', 'submitted_at',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'submitted_at', 'created_at', 'updated_at']

    def get_week_label(self, obj):
        ws = obj.week_start
        we = obj.week_end
        if isinstance(ws, str):
            ws = date.fromisoformat(ws)
        if isinstance(we, str):
            we = date.fromisoformat(we)
        return f'Semaine du {ws:%d/%m} au {we:%d/%m/%Y}'


class ActivityLogSerializer(serializers.ModelSerializer):
    actor = UserPublicSerializer(read_only=True)

    class Meta:
        model = ActivityLog
        fields = ['id', 'project', 'team', 'actor', 'action_type',
                  'description', 'metadata', 'created_at', 'is_read']
        read_only_fields = ['id', 'created_at']


class UseCaseCommentSerializer(serializers.ModelSerializer):
    author = UserPublicSerializer(read_only=True)

    class Meta:
        model = UseCaseComment
        fields = ['id', 'use_case', 'author', 'content', 'created_at', 'updated_at']
        read_only_fields = ['id', 'use_case', 'author', 'created_at', 'updated_at']


class MemberProgressSerializer(serializers.Serializer):
    user        = UserPublicSerializer()
    total_ucs   = serializers.IntegerField()
    passed      = serializers.IntegerField()
    failed      = serializers.IntegerField()
    blocked     = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    not_run     = serializers.IntegerField()
    progress_pct = serializers.FloatField()
    assigned_ucs = UseCaseAssignmentSerializer(many=True)


class SprintBoardSerializer(serializers.Serializer):
    sprint  = SprintSerializer()
    columns = serializers.DictField(child=serializers.ListField(child=UseCaseAssignmentSerializer()))
    stats   = serializers.DictField()


class DefectStatsSerializer(serializers.Serializer):
    total        = serializers.IntegerField()
    open         = serializers.IntegerField()
    in_progress  = serializers.IntegerField()
    resolved     = serializers.IntegerField()
    closed       = serializers.IntegerField()
    critical     = serializers.IntegerField()
    major        = serializers.IntegerField()
    minor        = serializers.IntegerField()
    by_severity  = serializers.DictField()
    by_priority  = serializers.DictField()


class UseCaseScreenshotSerializer(serializers.ModelSerializer):
    uploaded_by_user = UserPublicSerializer(source='uploaded_by', read_only=True)

    class Meta:
        model = UseCaseScreenshot
        fields = ['id', 'assignment', 'image', 'caption', 'uploaded_by', 'uploaded_by_user', 'uploaded_at']
        read_only_fields = ['id', 'uploaded_by', 'uploaded_at']


class UseCaseDetailSerializer(serializers.Serializer):
    assignment    = UseCaseAssignmentSerializer()
    use_case      = serializers.SerializerMethodField()
    screenshots   = serializers.SerializerMethodField()
    comments      = serializers.SerializerMethodField()

    def get_use_case(self, obj):
        from apps.core.models import ExtractedUseCase
        uc = ExtractedUseCase.objects.get(id=obj.use_case_id)
        from apps.api.serializers import ExtractedUseCaseSerializer
        return ExtractedUseCaseSerializer(uc).data

    def get_screenshots(self, obj):
        screenshots = UseCaseScreenshot.objects.filter(assignment=obj)
        return UseCaseScreenshotSerializer(screenshots, many=True).data

    def get_comments(self, obj):
        comments = UseCaseComment.objects.filter(use_case=obj.use_case)
        return UseCaseCommentSerializer(comments, many=True).data
