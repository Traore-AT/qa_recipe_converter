from datetime import date, timedelta
from rest_framework import serializers
from django.contrib.auth.models import User
from apps.core.models import ExtractedUseCase
from .models import (
    Sprint, UseCaseAssignment, Defect, DefectComment, DefectHistory, WeeklyReport, ActivityLog,
    UseCaseComment, UseCaseScreenshot, ProjectQASettings,
)
from . import jira
from .services import resolve_assignee
from apps.teams.serializers import UserPublicSerializer, ProjectSerializer
from apps.teams.models import ProjectMember


class SprintSerializer(serializers.ModelSerializer):
    created_by = UserPublicSerializer(read_only=True)
    stats      = serializers.SerializerMethodField()

    class Meta:
        model = Sprint
        fields = ['id', 'project', 'name', 'goal', 'start_date', 'end_date',
                  'status', 'kind', 'report_sent_at', 'created_by', 'duration_days', 'stats',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'kind', 'report_sent_at', 'created_by', 'created_at', 'updated_at', 'duration_days', 'stats']

    def get_stats(self, obj):
        return obj.stats

    def validate(self, data):
        if data.get('start_date') and data.get('end_date'):
            if data['end_date'] <= data['start_date']:
                raise serializers.ValidationError('La date de fin doit être après la date de début.')
        return data


class SprintCreateSerializer(serializers.ModelSerializer):
    """Création et modification (partielle) d'un sprint : nom, objectif, dates ou durée, statut."""
    end_date = serializers.DateField(required=False)
    duration_days = serializers.IntegerField(required=False, min_value=1, max_value=365, write_only=True)

    class Meta:
        model = Sprint
        fields = ['name', 'goal', 'start_date', 'end_date', 'duration_days', 'status']

    def validate(self, data):
        duration = data.pop('duration_days', None)
        start = data.get('start_date') or getattr(self.instance, 'start_date', None)
        if duration and 'end_date' not in data and start:
            data['end_date'] = start + timedelta(days=duration)
        end = data.get('end_date') or getattr(self.instance, 'end_date', None)
        if end is None:
            raise serializers.ValidationError({'end_date': 'La date de fin ou la durée est obligatoire.'})
        if start and end <= start:
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
                  'assigned_by_user', 'assigned_at', 'status', 'defects', 'open_defects_count', 'is_regression',
                  'carried_from_name', 'other_sprints']
        read_only_fields = ['id', 'assigned_by', 'assigned_at', 'status']

    status             = serializers.CharField(source='effective_status', read_only=True)
    defects            = serializers.SerializerMethodField()
    open_defects_count = serializers.SerializerMethodField()
    is_regression      = serializers.SerializerMethodField()
    carried_from_name  = serializers.CharField(source='carried_from.name', read_only=True, default=None)
    other_sprints      = serializers.SerializerMethodField()

    def get_other_sprints(self, obj):
        """Autres sprints contenant ce cas, avec son résultat dans chacun (badge « Aussi dans »)."""
        others = {}
        for a in obj.use_case.assignments.all():
            if a.sprint_id and a.sprint_id != obj.sprint_id and a.sprint_id not in others:
                others[a.sprint_id] = {'id': str(a.sprint_id), 'name': a.sprint.name,
                                       'status': a.sprint.status, 'case_status': a.status}
        return sorted(others.values(), key=lambda s: s['name'])

    def _defects(self, obj):
        if not hasattr(obj, '_linked_defects'):
            case = obj.use_case
            linked = {d.id: d for d in (*case.defects.all(), *case.related_defects.all())}
            # Dans un sprint, seules ses anomalies (ou celles hors sprint) sont pertinentes.
            obj._linked_defects = [d for d in linked.values()
                                   if not obj.sprint_id or d.sprint_id in (None, obj.sprint_id)]
        return obj._linked_defects

    def get_defects(self, obj):
        return [{'id': str(d.id), 'ticket_number': d.ticket_number, 'title': d.title,
                 'severity': d.severity, 'status': d.status, 'is_regression': d.is_regression}
                for d in self._defects(obj)]

    def get_open_defects_count(self, obj):
        return sum(1 for d in self._defects(obj) if d.is_open)

    def get_is_regression(self, obj):
        if obj.effective_status != 'Échoué':
            return False
        failures = [e for e in obj.use_case.executions.all() if e.new_status == 'Échoué']
        return bool(failures) and max(failures, key=lambda e: e.executed_at).is_regression

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


class AssigneeFieldsMixin(serializers.Serializer):
    """
    L'assigné peut être un membre du projet (`assigned_to`) ou n'importe quel membre de
    l'équipe (`assigned_user_id`), alors ajouté automatiquement au projet comme testeur.
    """
    assigned_user_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)

    def apply_assignee(self, attrs, project):
        if 'assigned_user_id' in attrs:
            user_id = attrs.pop('assigned_user_id')
            if user_id is None:
                attrs['assigned_to'] = None
            else:
                member, error = resolve_assignee(project, user_id)
                if error:
                    raise serializers.ValidationError({'assigned_user_id': error})
                attrs['assigned_to'] = member
        return attrs


class SprintCaseUpdateSerializer(AssigneeFieldsMixin, serializers.ModelSerializer):
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=ProjectMember.objects.select_related('project'), required=False, allow_null=True
    )

    class Meta:
        model = ExtractedUseCase
        fields = ['use_case_text', 'jira_ticket', 'description', 'preconditions', 'steps',
                  'expected_results', 'observed_results', 'status', 'assigned_to', 'assigned_user_id']

    def validate_status(self, value):
        valid = [choice for choice, _ in ExtractedUseCase.UCStatus.choices]
        if value not in valid:
            raise serializers.ValidationError(f"Statut invalide. Valeurs : {', '.join(valid)}")
        return value

    def validate(self, attrs):
        legacy_title = self.initial_data.get('title') if hasattr(self, 'initial_data') else None
        if legacy_title is not None and 'use_case_text' not in attrs:
            attrs['use_case_text'] = legacy_title
        assignment = self.context.get('assignment')
        if 'status' in attrs and assignment and assignment.sprint_id and assignment.sprint.is_frozen:
            # Le statut global du cas ne doit pas être modifié depuis un sprint figé.
            if attrs.pop('status') != assignment.effective_status:
                raise serializers.ValidationError(
                    {'status': 'Ce sprint est terminé : rouvrez-le pour modifier les résultats.'})
        return self.apply_assignee(attrs, self.instance.project)

    def validate_assigned_to(self, value):
        project = self.instance.project
        if value and value.project_id != project.id:
            raise serializers.ValidationError('Le membre assigné doit appartenir à ce projet.')
        return value

    def update(self, instance, validated_data):
        assigned_to = validated_data.pop('assigned_to', serializers.empty)
        assignment = self.context.get('assignment')
        request = self.context.get('request')
        instance._status_actor = getattr(request, 'user', None)
        instance._status_sprint = assignment.sprint if assignment else None
        if assignment and assignment.sprint_id and validated_data.get('status') == assignment.status:
            # Statut inchangé dans ce sprint : ne pas écraser le dernier statut global avec celui-ci.
            validated_data.pop('status')
        case = super().update(instance, validated_data)
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
    """Assignation à un membre (`user_id`) ou répartition équilibrée entre plusieurs (`user_ids`)."""
    use_case_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    sprint_id    = serializers.UUIDField(required=False, allow_null=True)
    user_id      = serializers.IntegerField(required=False)
    user_ids     = serializers.ListField(child=serializers.IntegerField(), required=False, allow_empty=False,
                                         max_length=50)

    def validate_user_id(self, value):
        if not User.objects.filter(id=value).exists():
            raise serializers.ValidationError('Utilisateur introuvable.')
        return value

    def validate_user_ids(self, value):
        value = list(dict.fromkeys(value))
        if User.objects.filter(id__in=value).count() != len(value):
            raise serializers.ValidationError('Utilisateur introuvable.')
        return value

    def validate(self, attrs):
        if ('user_id' in attrs) == ('user_ids' in attrs):
            raise serializers.ValidationError({'user_id': 'Indiquez un membre (user_id) ou plusieurs (user_ids).'})
        return attrs


def _case_summary(case):
    return {
        'id': str(case.id), 'case_number': case.case_number,
        'title': case.use_case_text or case.description, 'status': case.status,
    }


class DefectSerializer(serializers.ModelSerializer):
    reported_by_user  = UserPublicSerializer(source='reported_by', read_only=True)
    assigned_to_user  = UserPublicSerializer(source='assigned_to', read_only=True)
    sprint_name       = serializers.CharField(source='sprint.name', read_only=True, default=None)
    use_case_detail   = serializers.SerializerMethodField()
    related_cases     = serializers.SerializerMethodField()
    regression_of_ticket = serializers.CharField(source='regression_of.ticket_number', read_only=True, default=None)
    comments_count    = serializers.SerializerMethodField()
    jira_url          = serializers.SerializerMethodField()

    class Meta:
        model = Defect
        fields = ['id', 'ticket_number', 'sprint', 'sprint_name', 'use_case', 'use_case_detail',
                  'related_use_cases', 'related_cases', 'project', 'title', 'description',
                  'severity', 'priority', 'status',
                  'is_regression', 'regression_of', 'regression_of_ticket', 'resolution',
                  'resolved_at', 'closed_at', 'reopen_count', 'comments_count',
                  'jira_key', 'jira_url', 'jira_synced_at', 'jira_sync_error',
                  'reported_by', 'reported_by_user',
                  'assigned_to', 'assigned_to_user',
                  'steps_to_reproduce', 'expected_behavior', 'actual_behavior',
                  'environment', 'attachment',
                  'created_at', 'updated_at']
        read_only_fields = fields

    def get_use_case_detail(self, obj):
        return _case_summary(obj.use_case) if obj.use_case_id else None

    def get_related_cases(self, obj):
        return [_case_summary(c) for c in obj.related_use_cases.all()]

    def get_jira_url(self, obj):
        return jira.issue_url(obj.jira_key)

    def get_comments_count(self, obj):
        return len(obj.comments.all())


class DefectWriteSerializer(serializers.ModelSerializer):
    """Création et modification d'une anomalie, avec contrôle de cohérence projet / sprint / cas."""
    MAX_ATTACHMENT_MB = 5
    ATTACHMENT_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'application/pdf', 'text/plain', 'video/mp4']

    is_regression = serializers.BooleanField(required=False, allow_null=True, default=serializers.empty)

    class Meta:
        model = Defect
        fields = ['sprint', 'use_case', 'related_use_cases', 'title', 'description', 'severity', 'priority',
                  'status', 'is_regression', 'regression_of', 'resolution',
                  'assigned_to', 'steps_to_reproduce', 'expected_behavior',
                  'actual_behavior', 'environment', 'attachment']

    def validate_attachment(self, value):
        if value:
            if value.size > self.MAX_ATTACHMENT_MB * 1024 * 1024:
                raise serializers.ValidationError(f'Fichier trop volumineux (max {self.MAX_ATTACHMENT_MB} MB).')
            if getattr(value, 'content_type', None) not in (None, *self.ATTACHMENT_TYPES):
                raise serializers.ValidationError('Format non supporté.')
        return value

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError('Le titre est obligatoire.')
        return value.strip()

    def validate_sprint(self, value):
        if value and value.project_id != self.context['project'].id:
            raise serializers.ValidationError('Le sprint doit appartenir à ce projet.')
        return value

    def validate_use_case(self, value):
        if value and value.project_id != self.context['project'].id:
            raise serializers.ValidationError('Le cas de test doit appartenir à ce projet.')
        return value

    def validate_related_use_cases(self, value):
        if any(c.project_id != self.context['project'].id for c in value):
            raise serializers.ValidationError('Tous les cas de test doivent appartenir à ce projet.')
        return value

    def validate_regression_of(self, value):
        if value and value.project_id != self.context['project'].id:
            raise serializers.ValidationError("L'anomalie d'origine doit appartenir à ce projet.")
        if value and self.instance and value.pk == self.instance.pk:
            raise serializers.ValidationError('Une anomalie ne peut pas être sa propre régression.')
        return value

    def validate_assigned_to(self, value):
        if value and not self.context['project'].team.is_member(value):
            raise serializers.ValidationError("Cet utilisateur ne fait pas partie de l'équipe du projet.")
        return value

    def validate(self, attrs):
        if attrs.get('is_regression', False) is None:
            attrs.pop('is_regression')
        # L'association à un sprint n'a de sens que pour des cas joués dans ce sprint.
        if {'sprint', 'use_case', 'related_use_cases'} & attrs.keys():
            sprint = attrs.get('sprint', getattr(self.instance, 'sprint', None))
            case = attrs.get('use_case', getattr(self.instance, 'use_case', None))
            related = attrs.get('related_use_cases')
            if related is None:
                related = list(self.instance.related_use_cases.all()) if self.instance else []
            if case and 'related_use_cases' in attrs:
                attrs['related_use_cases'] = related = [c for c in related if c.pk != case.pk]
            if sprint:
                wanted = {c.pk: c for c in [*related, *([case] if case else [])]}
                present = set(UseCaseAssignment.objects.filter(sprint=sprint, use_case__in=wanted)
                              .values_list('use_case_id', flat=True))
                missing = sorted(c.case_number for pk, c in wanted.items() if pk not in present)
                if missing:
                    labels = ', '.join(f'CAS-{n:03d}' for n in missing)
                    raise serializers.ValidationError(
                        {'use_case': f'Cas absent(s) du sprint « {sprint.name} » : {labels}.'})
        return attrs


class DefectCommentSerializer(serializers.ModelSerializer):
    author = UserPublicSerializer(read_only=True)

    class Meta:
        model = DefectComment
        fields = ['id', 'defect', 'author', 'external_author', 'content', 'created_at']
        read_only_fields = ['id', 'defect', 'author', 'external_author', 'created_at']


class ProjectQASettingsSerializer(serializers.ModelSerializer):
    JIRA_FIELDS = ('jira_enabled', 'jira_project_key', 'jira_issue_type', 'jira_import')

    jira_project_key = serializers.RegexField(r'^([A-Z][A-Z0-9_]{1,18})?$', required=False, allow_blank=True, error_messages={
        'invalid': 'Clé de projet Jira invalide (ex. : QA).'})
    jira_configured = serializers.SerializerMethodField()
    can_edit_jira = serializers.SerializerMethodField()
    recipients_count = serializers.SerializerMethodField()

    class Meta:
        model = ProjectQASettings
        fields = ['alert_on_regression', 'alert_on_critical_defect', 'auto_report_on_close',
                  'jira_enabled', 'jira_project_key', 'jira_issue_type', 'jira_import',
                  'jira_configured', 'can_edit_jira', 'recipients_count', 'updated_at']
        read_only_fields = ['updated_at']

    def get_jira_configured(self, obj):
        return jira.is_configured()

    def get_can_edit_jira(self, obj):
        return bool(self.context['request'].user.is_superuser)

    def get_recipients_count(self, obj):
        from .notifications import project_recipients
        return len(project_recipients(obj.project))

    def validate(self, attrs):
        # Le compte technique Jira est partagé : seul un super-admin choisit le projet accessible.
        changed = [f for f in self.JIRA_FIELDS if f in attrs and attrs[f] != getattr(self.instance, f)]
        if changed and not self.context['request'].user.is_superuser:
            raise serializers.ValidationError(
                {'jira_project_key': 'La liaison Jira est réservée aux super-administrateurs.'})
        key = attrs.get('jira_project_key', self.instance.jira_project_key)
        if attrs.get('jira_enabled', self.instance.jira_enabled) and not key:
            raise serializers.ValidationError({'jira_project_key': 'Indiquez la clé du projet Jira.'})
        if key and ProjectQASettings.objects.filter(jira_project_key=key).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError({'jira_project_key': 'Cette clé Jira est déjà liée à un autre projet.'})
        return attrs


class DefectHistorySerializer(serializers.ModelSerializer):
    actor = UserPublicSerializer(read_only=True)

    class Meta:
        model = DefectHistory
        fields = ['id', 'actor', 'field', 'old_value', 'new_value', 'created_at']


class SprintCaseCreateSerializer(AssigneeFieldsMixin, serializers.ModelSerializer):
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=ProjectMember.objects.select_related('project'), required=False, allow_null=True
    )
    use_case_text = serializers.CharField(required=False, allow_blank=True)
    comment = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = ExtractedUseCase
        fields = ['use_case_text', 'jira_ticket', 'description', 'preconditions', 'steps', 'expected_results',
                  'observed_results', 'status', 'assigned_to', 'assigned_user_id', 'comment']
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
        project = self.context.get('project')
        return self.apply_assignee(attrs, project) if project else attrs


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
    is_read = serializers.SerializerMethodField()

    def get_is_read(self, obj):
        """État de lecture propre à l'utilisateur connecté (ses propres actions sont déjà lues)."""
        user = getattr(self.context.get('request'), 'user', None)
        if user is None or not user.is_authenticated:
            return obj.is_read
        return obj.actor_id == user.id or any(u.id == user.id for u in obj.read_by.all())

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
