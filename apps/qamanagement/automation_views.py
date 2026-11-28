"""API : réglages QA, report de cas, campagnes, import de résultats, exports Excel, Jira."""
import hashlib
import hmac
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.background import run_in_background
from apps.teams.permissions import can_manage_project
from . import campaigns, jira, test_results
from .models import Defect, ProjectQASettings, Sprint
from .notifications import project_recipients, send_sprint_report
from .serializers import DefectSerializer, ProjectQASettingsSerializer, SprintCreateSerializer, SprintSerializer
from .views import (
    _defects, _filter_defects, _get_team_and_project, _log_activity, _require_write, _sprints_param,
)

logger = logging.getLogger(__name__)
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
MAX_RESULT_FILES = 200


def _require_manage(user, project):
    if project.is_archived:
        raise PermissionDenied('Projet archivé — lecture seule.')
    if not can_manage_project(user, project):
        raise PermissionDenied('Action réservée aux responsables du projet.')


def _error(message, code=status.HTTP_400_BAD_REQUEST):
    return Response({'error': message}, status=code)


class ProjectAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def project(self):
        return _get_team_and_project(self.kwargs['slug'], self.kwargs['project_slug'], self.request.user)[1]

    def sprint(self, project):
        return get_object_or_404(Sprint, id=self.kwargs['sprint_id'], project=project)

    def log(self, project, action, description, **kwargs):
        _log_activity(project, project.team, self.request.user, action, description, **kwargs)

    @property
    def username(self):
        return self.request.user.get_full_name() or self.request.user.username


# ─────────────────────────────────────────────────────────────────────────────
# Réglages
# ─────────────────────────────────────────────────────────────────────────────

class QASettingsView(ProjectAPIView):
    @extend_schema(responses=ProjectQASettingsSerializer)
    def get(self, request, **kwargs):
        project = self.project()
        config = ProjectQASettings.for_project(project)
        return Response(ProjectQASettingsSerializer(config, context={'request': request}).data)

    @extend_schema(request=ProjectQASettingsSerializer, responses=ProjectQASettingsSerializer)
    def patch(self, request, **kwargs):
        project = self.project()
        _require_manage(request.user, project)
        config = ProjectQASettings.for_project(project)
        s = ProjectQASettingsSerializer(config, data=request.data, partial=True, context={'request': request})
        s.is_valid(raise_exception=True)
        s.save()
        self.log(project, 'qa_settings_updated', 'Réglages qualité et intégrations mis à jour')
        return Response(s.data)


# ─────────────────────────────────────────────────────────────────────────────
# Rapport de sprint par e-mail, exports Excel
# ─────────────────────────────────────────────────────────────────────────────

class SprintReportSendView(ProjectAPIView):
    """Envoie (ou renvoie) le rapport du sprint aux responsables du projet."""

    def post(self, request, **kwargs):
        project = self.project()
        _require_manage(request.user, project)
        sprint = self.sprint(project)
        recipients = project_recipients(project)
        if not recipients:
            return _error("Aucun responsable du projet n'a d'adresse e-mail.")
        run_in_background(send_sprint_report, sprint.pk, self.username, automatic=False)
        self.log(project, 'sprint_report_sent', f'Rapport du sprint "{sprint.name}" envoyé à {len(recipients)} responsable(s)')
        return Response({'recipients': len(recipients)}, status=status.HTTP_202_ACCEPTED)


def _xlsx(content, filename):
    response = HttpResponse(content, content_type=XLSX)
    response['Content-Disposition'] = f'attachment; filename="{filename}-{timezone.localdate():%Y%m%d}.xlsx"'
    return response


class ProjectReportExcelView(ProjectAPIView):
    def get(self, request, **kwargs):
        from apps.teams.excel_reports import generate_project_workbook
        project = self.project()
        sprints = _sprints_param(request, project)
        suffix = 'projet' if sprints is None else (
            slugify(sprints[0].name) or 'sprint' if len(sprints) == 1 else f'{len(sprints)}-sprints')
        return _xlsx(generate_project_workbook(project, sprints, self.username), f'rapport-{project.slug}-{suffix}')


class DefectExcelView(ProjectAPIView):
    """Export des anomalies, avec les mêmes filtres que la liste."""

    def get(self, request, **kwargs):
        from apps.teams.excel_reports import generate_defects_workbook
        project = self.project()
        defects = _filter_defects(_defects(project), request.query_params)
        return _xlsx(generate_defects_workbook(project, defects, self.username), f'anomalies-{project.slug}')


# ─────────────────────────────────────────────────────────────────────────────
# Report de cas
# ─────────────────────────────────────────────────────────────────────────────

class CarryOverSerializer(serializers.Serializer):
    target_sprint = serializers.UUIDField(required=False, allow_null=True)
    new_sprint = serializers.DictField(required=False, allow_null=True)
    case_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=False)
    include_failed = serializers.BooleanField(default=False)
    close_source = serializers.BooleanField(default=False)

    def validate(self, attrs):
        if bool(attrs.get('target_sprint')) == bool(attrs.get('new_sprint')):
            raise serializers.ValidationError('Indiquez un sprint existant ou un nouveau sprint.')
        return attrs


class CarryOverView(ProjectAPIView):
    """GET : cas reportables. POST : report vers un sprint existant ou nouveau."""

    def get(self, request, **kwargs):
        sprint = self.sprint(self.project())
        include_failed = request.query_params.get('include_failed') in ('1', 'true')
        rows, seen = [], set()
        for a in campaigns.carry_over_candidates(sprint, include_failed):
            if a.use_case_id in seen:
                continue
            seen.add(a.use_case_id)
            rows.append({
                'use_case': str(a.use_case_id), 'case_number': a.use_case.case_number,
                'title': a.use_case.use_case_text or a.use_case.description, 'status': a.effective_status,
                'assigned_to': (a.assigned_to.user.get_full_name() or a.assigned_to.user.username) if a.assigned_to_id else None,
            })
        return Response({'results': rows, 'count': len(rows)})

    @extend_schema(request=CarryOverSerializer)
    def post(self, request, **kwargs):
        project = self.project()
        _require_manage(request.user, project)
        source = self.sprint(project)
        s = CarryOverSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        try:
            with transaction.atomic():
                if data.get('new_sprint'):
                    form = SprintCreateSerializer(data=data['new_sprint'])
                    form.is_valid(raise_exception=True)
                    target = form.save(project=project, created_by=request.user)
                else:
                    target = get_object_or_404(Sprint, id=data['target_sprint'], project=project)
                result = campaigns.carry_over(source, target, request.user, data.get('case_ids'),
                                              data['include_failed'], data['close_source'])
                if not result['created']:
                    raise campaigns.CampaignError('Aucun cas à reporter vers ce sprint.')
        except campaigns.CampaignError as exc:
            return _error(str(exc))
        self.log(project, 'cases_carried_over', f'{result["created"]} cas reporté(s) de "{source.name}" vers "{target.name}"')
        return Response({**result, 'sprint': SprintSerializer(target, context={'request': request}).data},
                        status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# Campagne de non-régression
# ─────────────────────────────────────────────────────────────────────────────

class RegressionCampaignSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, max_length=200)
    start_date = serializers.DateField(required=False)
    duration_days = serializers.IntegerField(default=7, min_value=1, max_value=365)
    scope = serializers.ChoiceField(choices=['all', 'active'], default='all')
    reset_status = serializers.BooleanField(default=True)


class RegressionCampaignView(ProjectAPIView):
    """Crée en un appel un sprint contenant tous les cas ayant déjà régressé."""

    def get(self, request, **kwargs):
        project = self.project()
        return Response({'all': campaigns.regression_cases(project).count(),
                         'active': campaigns.regression_cases(project, 'active').count()})

    @extend_schema(request=RegressionCampaignSerializer)
    def post(self, request, **kwargs):
        project = self.project()
        _require_manage(request.user, project)
        s = RegressionCampaignSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        start = data.get('start_date') or timezone.localdate()
        sprint_data = {
            'name': data.get('name') or f'Non-régression du {start:%d/%m/%Y}',
            'goal': 'Rejouer les cas ayant déjà régressé.',
            'start_date': start, 'end_date': start + timedelta(days=data['duration_days']),
            'status': Sprint.Status.ACTIVE if start <= timezone.localdate() else Sprint.Status.PLANNED,
        }
        try:
            sprint, count = campaigns.create_regression_campaign(
                project, request.user, sprint_data, data['scope'], data['reset_status'])
        except campaigns.CampaignError as exc:
            return _error(str(exc))
        self.log(project, 'regression_campaign', f'Campagne "{sprint.name}" créée avec {count} cas')
        return Response({'sprint': SprintSerializer(sprint, context={'request': request}).data, 'cases': count},
                        status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# Import de résultats automatisés
# ─────────────────────────────────────────────────────────────────────────────

class ImportOptionsSerializer(serializers.Serializer):
    sprint = serializers.UUIDField(required=False, allow_null=True)
    create_defects = serializers.BooleanField(default=False)
    dry_run = serializers.BooleanField(default=False)


class TestResultsImportView(ProjectAPIView):
    """Importe un ou plusieurs rapports ; `dry_run` renvoie l'aperçu sans rien modifier."""
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(request={'multipart/form-data': ImportOptionsSerializer})
    def post(self, request, **kwargs):
        project = self.project()
        _require_write(request.user, project)
        options = ImportOptionsSerializer(data={k: v for k, v in request.data.items() if k in ('sprint', 'create_defects', 'dry_run') and v != ''})
        options.is_valid(raise_exception=True)
        options = options.validated_data
        files = request.FILES.getlist('files')
        if not files:
            return _error('Ajoutez au moins un fichier de résultats.')
        if len(files) > MAX_RESULT_FILES:
            return _error(f'Trop de fichiers ({MAX_RESULT_FILES} maximum).')

        sprint = None
        if options.get('sprint'):
            sprint = get_object_or_404(Sprint, id=options['sprint'], project=project)
            if sprint.is_frozen:
                return _error('Ce sprint est terminé : rouvrez-le pour y importer des résultats.')
        try:
            results = []
            for f in files:
                if f.size > test_results.MAX_FILE_BYTES:
                    raise test_results.ResultsError(f'{f.name} : fichier trop volumineux (10 Mo maximum).')
                results += test_results.parse_results(f.name, f.read())
        except test_results.ResultsError as exc:
            return _error(str(exc))

        outcome = test_results.build_plan(project, results, sprint)
        plan = outcome.pop('plan')
        body = {
            **outcome,
            'unmatched_count': len(outcome['unmatched']),
            'unmatched': outcome['unmatched'][:50],
            'passed': sum(e['new_status'] == 'Passé' for e in plan),
            'failed': sum(e['new_status'] == 'Échoué' for e in plan),
            'cases': [{'use_case': str(e['case'].id), 'case_number': e['case'].case_number,
                       'title': e['case'].use_case_text or e['case'].description,
                       'old_status': e['case'].status, 'new_status': e['new_status'], 'tests': e['tests'],
                       'message': e['failures'][0][:300] if e['failures'] else ''} for e in plan],
            'applied': False, 'updated': 0, 'defects_created': 0,
        }
        if options['dry_run']:
            return Response(body)
        if not plan:
            return Response({**body, 'error': 'Aucun test ne correspond à un cas de ce périmètre.'}, status=status.HTTP_400_BAD_REQUEST)
        applied = test_results.apply_plan(project, plan, request.user, sprint, options['create_defects'])
        for defect in applied['defects_created']:
            jira.queue_push(defect)
        self.log(project, 'test_results_imported',
                 f'{len(plan)} cas mis à jour par import automatique ({body["passed"]} passé(s), {body["failed"]} échoué(s))')
        return Response({**body, 'applied': True, 'updated': applied['updated'],
                         'defects_created': len(applied['defects_created'])})


# ─────────────────────────────────────────────────────────────────────────────
# Jira
# ─────────────────────────────────────────────────────────────────────────────

class JiraSyncView(ProjectAPIView):
    """Synchronisation complète, à la demande."""

    def post(self, request, **kwargs):
        project = self.project()
        _require_manage(request.user, project)
        try:
            result = jira.sync_project(project)
        except jira.JiraError as exc:
            return _error(str(exc))
        self.log(project, 'jira_sync', f'Synchronisation Jira : {result["pushed"]} envoyée(s), '
                                       f'{result["updated"]} mise(s) à jour, {result["imported"]} importée(s)')
        return Response(result)


class JiraTicketValidateView(ProjectAPIView):
    """Vérifie des références Jira (format PROJET-123, puis existence si Jira est configuré)."""

    def post(self, request, **kwargs):
        self.project()
        tickets = request.data.get('tickets')
        if not isinstance(tickets, list) or not all(isinstance(t, str) for t in tickets):
            return _error('Liste de références attendue (« tickets »).')
        return Response(jira.check_tickets(tickets))


class DefectJiraPushView(ProjectAPIView):
    """Envoie une anomalie vers Jira (création ou nouvelle tentative)."""

    def post(self, request, **kwargs):
        project = self.project()
        _require_write(request.user, project)
        defect = get_object_or_404(Defect, id=kwargs['defect_id'], project=project)
        if not jira.is_enabled(project):
            return _error('La synchronisation Jira n’est pas activée pour ce projet.')
        sent = jira.push_defect(defect.pk)
        defect = _defects(project).get(pk=defect.pk)
        if not sent:
            return _error(defect.jira_sync_error, status.HTTP_502_BAD_GATEWAY)
        return Response(DefectSerializer(defect, context={'request': request}).data)


@extend_schema(exclude=True)
class JiraWebhookView(APIView):
    """Point d'entrée des webhooks Jira, authentifié par signature HMAC-SHA256."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        secret = settings.JIRA_WEBHOOK_SECRET
        if not secret:
            return _error('Webhook non configuré.', status.HTTP_404_NOT_FOUND)
        # Corps brut lu avant toute interprétation : c'est lui qui est signé.
        body = request._request.body
        expected = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        received = request.headers.get('X-Hub-Signature', '')
        if not hmac.compare_digest(expected.encode(), received.encode('utf-8', 'replace')):
            return _error('Signature invalide.', status.HTTP_401_UNAUTHORIZED)
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return _error('Contenu invalide.')
        return Response({'result': jira.handle_webhook(payload)})

