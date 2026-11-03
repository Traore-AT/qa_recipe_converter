from datetime import date
from rest_framework import serializers
from django.contrib.auth.models import User
from apps.core.models import ExtractedUseCase
from .models import Sprint, UseCaseAssignment, Defect, WeeklyReport, ActivityLog, UseCaseComment, UseCaseScreenshot
from apps.teams.serializers import UserPublicSerializer, ProjectSerializer
from apps.teams.models import ProjectMember


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
    ticket_number       = serializers.SerializerMethodField()
    assigned_to_user   = serializers.SerializerMethodField()
    assigned_by_user   = UserPublicSerializer(source='assigned_by', read_only=True)
    use_case_order     = serializers.SerializerMethodField()
    case_number        = serializers.SerializerMethodField()
    use_case_id_str    = serializers.SerializerMethodField()
    use_case_desc      = serializers.SerializerMethodField()
    use_case_text      = serializers.SerializerMethodField()
    title              = serializers.SerializerMethodField()
    jira_ticket        = serializers.SerializerMethodField()
    description        = serializers.SerializerMethodField()
    preconditions      = serializers.SerializerMethodField()
    steps              = serializers.SerializerMethodField()
    expected_results   = serializers.SerializerMethodField()
    observed_results   = serializers.SerializerMethodField()
    jira_url           = serializers.SerializerMethodField()

    class Meta:
        model = UseCaseAssignment
        fields = ['id', 'use_case', 'ticket_number', 'use_case_order', 'use_case_id_str', 'use_case_text', 'title', 'use_case_desc',
                  'jira_ticket', 'description', 'preconditions', 'steps', 'expected_results', 'observed_results', 'jira_url',
                  'case_number', 'sprint', 'assigned_to', 'assigned_to_user', 'assigned_by',
                  'assigned_by_user', 'assigned_at', 'status']
        read_only_fields = ['id', 'assigned_by', 'assigned_at', 'status']

    status = serializers.CharField(source='use_case.status', read_only=True)

    def get_jira_url(self, obj):
        from django.conf import settings
        base = (getattr(settings, 'JIRA_BASE_URL', '') or '').rstrip('/')
        ticket = (self.get_jira_ticket(obj) or '').strip()
        if base and ticket:
            return f'{base}/browse/{ticket}'
        return None

    def get_assigned_to_user(self, obj):
        return UserPublicSerializer(obj.assigned_to.user).data if obj.assigned_to_id else None

    def _case_field(self, obj, source): return getattr(obj.use_case, source)
    def get_use_case_order(self, obj): return obj.use_case.case_number
    def get_case_number(self, obj): return obj.use_case.case_number
    def get_ticket_number(self, obj): return obj.use_case.ticket_number
    def get_use_case_id_str(self, obj): return self._case_field(obj, 'use_case_text')
    def get_use_case_text(self, obj): return self.get_use_case_id_str(obj)
    def get_title(self, obj): return self.get_use_case_text(obj)
    def get_use_case_desc(self, obj): return self._case_field(obj, 'description')
    def get_jira_ticket(self, obj): return self._case_field(obj, 'jira_ticket')
    def get_description(self, obj): return self.get_use_case_desc(obj)
    def get_preconditions(self, obj): return self._case_field(obj, 'preconditions')
    def get_steps(self, obj): return self._case_field(obj, 'steps')
    def get_expected_results(self, obj): return self._case_field(obj, 'expected_results')
    def get_observed_results(self, obj): return self._case_field(obj, 'observed_results')


class SprintCaseUpdateSerializer(serializers.ModelSerializer):
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=ProjectMember.objects.select_related('project'), required=False, allow_null=True
    )
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=ProjectMember.objects.select_related('project'), required=False, allow_null=True
    )

    class Meta:
        model = ExtractedUseCase
        fields = ['use_case_text', 'jira_ticket', 'description', 'preconditions', 'steps',
                  'expected_results', 'observed_results', 'status', 'assigned_to']

    def validate_status(self, value):
        valid = [choice for choice, _ in ExtractedUseCase.UCStatus.choices]
        if value not in valid:
            raise serializers.ValidationError(f"Statut invalide. Valeurs : {', '.join(valid)}")
        return value

    def validate(self, attrs):
        legacy_title = self.initial_data.get('title') if hasattr(self, 'initial_data') else None
        if legacy_title is not None and 'use_case_text' not in attrs:
            attrs['use_case_text'] = legacy_title
        return attrs

    def validate_assigned_to(self, value):
        project = self.instance.project
        if value and value.project_id != project.id:
            raise serializers.ValidationError('Le membre assigné doit appartenir à ce projet.')
        return value

    def update(self, instance, validated_data):
        assigned_to = validated_data.pop('assigned_to', serializers.empty)
        case = super().update(instance, validated_data)
        if 'status' in validated_data:
            UseCaseAssignment.objects.filter(use_case=case).update(status=case.status)
        assignment = self.context.get('assignment')
        if assigned_to is not serializers.empty and assignment:
            siblings = UseCaseAssignment.objects.filter(use_case=case, sprint=assignment.sprint)
            if assigned_to is None:
                siblings.exclude(pk=assignment.pk).delete()
                assignment.assigned_to = None
                assignment.save(update_fields=['assigned_to'])
            else:
                assignment.assigned_to = assigned_to
                assignment.save(update_fields=['assigned_to'])
        return case


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
        fields = ['id', 'ticket_number', 'sprint', 'use_case', 'project', 'title', 'description',
                  'severity', 'priority', 'status',
                  'reported_by', 'reported_by_user',
                  'assigned_to', 'assigned_to_user',
                  'steps_to_reproduce', 'expected_behavior', 'actual_behavior',
                  'environment', 'attachment',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'ticket_number', 'project', 'reported_by', 'created_at', 'updated_at']

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
        fields = ['sprint', 'use_case', 'title', 'description', 'severity', 'priority',
                  'assigned_to', 'steps_to_reproduce', 'expected_behavior',
                  'actual_behavior', 'environment', 'attachment']


class SprintCaseCreateSerializer(serializers.ModelSerializer):
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=ProjectMember.objects.select_related('project'), required=False, allow_null=True
    )
    use_case_text = serializers.CharField(required=False, allow_blank=True)
    comment = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = ExtractedUseCase
        fields = ['use_case_text', 'jira_ticket', 'description', 'preconditions', 'steps', 'expected_results',
                  'observed_results', 'status', 'assigned_to', 'comment']
        read_only_fields = []

    def validate_assigned_to(self, value):
        project = self.context.get('project')
        if value and project and value.project_id != project.id:
            raise serializers.ValidationError('Le membre assigné doit appartenir au projet du sprint.')
        return value

    def validate_status(self, value):
        if value not in [choice for choice, _ in ExtractedUseCase.UCStatus.choices]:
            raise serializers.ValidationError('Statut de cas invalide.')
        return value

    def validate(self, attrs):
        legacy_title = self.initial_data.get('title') if hasattr(self, 'initial_data') else None
        if legacy_title is not None and not attrs.get('use_case_text'):
            attrs['use_case_text'] = legacy_title
        if self.instance is None and not (attrs.get('use_case_text') or '').strip():
            raise serializers.ValidationError({'use_case_text': 'Le titre du cas est obligatoire.'})
        return attrs


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
