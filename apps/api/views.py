import csv
import io
import json
import logging
import subprocess
import platform
import zipfile
import tempfile
import os
import shutil
from contextlib import ExitStack
import django.core.files
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import PermissionDenied
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator

from apps.teams.permissions import can_manage_project, can_view_project, can_write_project

from apps.core.access import check_job_access
from apps.core.files import local_copy
from apps.core.models import ConversionJob, ExtractedUseCase
from apps.core.use_cases import clean_jira_ticket, save_extracted_use_cases
from apps.core.word_validation import WordFileValidationError, validate_word_upload
from apps.core.doc_converter import convert_doc_to_docx, is_doc_file, is_libreoffice_available
from apps.parser.docx_parser import DocxParser
from apps.qamanagement.notifications import grouped_alerts
from apps.parser.excel_generator import ExcelGenerator
from apps.parser.gherkin_generator import generate_gherkin_only_zip, generate_cypress_project_zip
from apps.parser.file_searcher import search_files
from .serializers import (
    ConversionJobSerializer,
    ConversionJobListSerializer,
    ExtractedUseCaseSerializer,
)

logger = logging.getLogger(__name__)


class HealthCheckView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({'status': 'healthy', 'service': 'qa-recipe-backend'})


@method_decorator(ensure_csrf_cookie, name='dispatch')
class CsrfTokenView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({'csrfToken': get_token(request)})


class UploadAPIView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        word_file = request.FILES.get('word_file')
        if not word_file:
            return Response(
                {'error': 'Le fichier Word est requis.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            validate_word_upload(word_file)
        except WordFileValidationError as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        excel_template = request.FILES.get('excel_template')
        company_logo   = request.FILES.get('company_logo')
        project_id     = request.data.get('project') or request.POST.get('project')

        job_kwargs = {
            'source_filename': word_file.name,
            'word_file': word_file,
            'excel_template': excel_template,
            'company_logo': company_logo,
            'company_name': request.data.get('company_name', '').strip(),
            'excel_filename': request.data.get('excel_filename', '').strip(),
            'status': ConversionJob.Status.PROCESSING,
        }

        if project_id:
            from apps.teams.models import Project
            try:
                project = Project.objects.get(id=project_id)
            except (Project.DoesNotExist, ValueError, ValidationError):
                return Response({'error': 'Projet introuvable.'}, status=status.HTTP_404_NOT_FOUND)
            if not (can_view_project(request.user, project) and can_write_project(request.user, project)):
                return Response({'error': "Vous ne pouvez pas importer de recette dans ce projet."},
                                status=status.HTTP_403_FORBIDDEN)
            job_kwargs['project'] = project

        if request.user.is_authenticated:
            job_kwargs['uploaded_by'] = request.user

        job = ConversionJob.objects.create(**job_kwargs)

        with local_copy(job.word_file) as file_path:
            return self._process(job, file_path)

    def _process(self, job, file_path):
        if is_doc_file(file_path):
            if not is_libreoffice_available():
                job.status = ConversionJob.Status.ERROR
                job.error_message = (
                    "Le format .doc (Word 97-2003) n'est pas supporté directement. "
                    "LibreOffice est requis pour la conversion automatique. "
                    "Veuillez enregistrer votre document au format .docx (Word 2007+) et réessayer."
                )
                job.save()
                return Response(
                    {'error': job.error_message, 'job_id': str(job.id)},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            converted_path = convert_doc_to_docx(file_path)
            if converted_path is None:
                job.status = ConversionJob.Status.ERROR
                job.error_message = (
                    "La conversion du fichier .doc (Word 97-2003) vers .docx a échoué. "
                    "Veuillez enregistrer votre document au format .docx (Word 2007+) et réessayer."
                )
                job.save()
                return Response(
                    {'error': job.error_message, 'job_id': str(job.id)},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            # Remplacer le fichier stocké par la version convertie
            docx_name = job.source_filename.rsplit('.', 1)[0] + '.docx'
            job.word_file.delete(save=False)
            with open(converted_path, 'rb') as f:
                job.word_file.save(docx_name, django.core.files.File(f), save=False)
            job.source_filename = docx_name
            job.save()
            try:
                return self._extract(job, converted_path)
            finally:
                shutil.rmtree(os.path.dirname(converted_path), ignore_errors=True)
        return self._extract(job, file_path)

    def _extract(self, job, file_path):
        try:
            parser = DocxParser(file_path)
            use_cases = parser.extract_use_cases()

            if not use_cases:
                job.status = ConversionJob.Status.ERROR
                job.error_message = "Aucun tableau de cas de tests détecté dans le document."
                job.save()
                return Response(
                    {'error': job.error_message, 'job_id': str(job.id)},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            save_extracted_use_cases(job, use_cases)

            job.use_cases_count = len(use_cases)
            job.status = ConversionJob.Status.DONE
            job.save()

            serializer = ConversionJobSerializer(job)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except ValueError as e:
            logger.warning("Invalid Word document for job %s: %s", job.id, e)
            job.status = ConversionJob.Status.ERROR
            job.error_message = str(e)
            job.save()
            return Response(
                {'error': str(e), 'job_id': str(job.id)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except Exception as e:
            logger.exception("API upload error")
            job.status = ConversionJob.Status.ERROR
            job.error_message = "Une erreur interne est survenue lors du traitement du fichier."
            job.save()
            return Response(
                {'error': job.error_message, 'job_id': str(job.id)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class JobDetailAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, job_id):
        job = get_object_or_404(ConversionJob, id=job_id)
        check_job_access(request.user, job)
        serializer = ConversionJobSerializer(job)
        return Response(serializer.data)

    def delete(self, request, job_id):
        job = get_object_or_404(ConversionJob, id=job_id)

        if job.project:
            if not request.user.is_authenticated or not can_manage_project(request.user, job.project):
                raise PermissionDenied(
                    "Seul le propriétaire ou un administrateur de l'équipe peut supprimer cette recette."
                )
        else:
            if not request.user.is_authenticated:
                raise PermissionDenied("Vous devez être connecté pour supprimer une recette.")
            if job.uploaded_by and job.uploaded_by != request.user:
                raise PermissionDenied("Vous n'avez pas la permission de supprimer cette recette.")

        logger.info("Job %s (%s) deleted by %s", job.id, job.source_filename, request.user.username)
        job.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


class JobListAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        project_id = request.query_params.get('project')
        if request.user.is_authenticated and project_id:
            from apps.teams.models import Project
            try:
                project = Project.objects.get(id=project_id)
            except (Project.DoesNotExist, ValueError, ValidationError):
                return Response({'error': 'Projet introuvable.'}, status=status.HTTP_404_NOT_FOUND)
            if not can_view_project(request.user, project):
                raise PermissionDenied('Accès refusé à ce projet.')
            jobs = ConversionJob.objects.filter(project=project)
        elif request.user.is_authenticated:
            jobs = ConversionJob.objects.filter(uploaded_by=request.user)
        else:
            jobs = ConversionJob.objects.none()
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(jobs, request)
        if page is not None:
            serializer = ConversionJobListSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        serializer = ConversionJobListSerializer(jobs, many=True)
        return Response({'results': serializer.data, 'count': len(serializer.data)})


class UseCaseUpdateAPIView(APIView):
    permission_classes = [AllowAny]

    def patch(self, request, job_id, uc_id):
        uc = get_object_or_404(ExtractedUseCase.objects.select_related('job__project'), id=uc_id, job_id=job_id)
        check_job_access(request.user, uc.job, write=True)
        if request.user.is_authenticated:
            uc._status_actor = request.user
        serializer = ExtractedUseCaseSerializer(uc, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


def _local_path(stack, field_file):
    """Chemin local d'un fichier optionnel ; None s'il est absent ou illisible."""
    if not field_file:
        return None
    try:
        return stack.enter_context(local_copy(field_file))
    except Exception:
        logger.warning('Fichier illisible ignoré : %s', field_file.name)
        return None


class GenerateExcelAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, job_id):
        job = get_object_or_404(ConversionJob, id=job_id, status=ConversionJob.Status.DONE)
        check_job_access(request.user, job)
        use_cases = job.use_cases.all()

        # Les fichiers distants sont copiés localement le temps de la génération.
        with ExitStack() as files:
            return self._generate(job, use_cases, lambda f: _local_path(files, f))

    def _generate(self, job, use_cases, local_path):
        logo_path = local_path(job.company_logo)

        # Build extras dict: comments + screenshots for each use case
        from apps.qamanagement.models import UseCaseComment, UseCaseScreenshot, UseCaseAssignment
        uc_ids = [str(uc.id) for uc in use_cases]

        comments_qs = UseCaseComment.objects.filter(use_case_id__in=uc_ids).select_related('author')
        comments_map = {}
        for c in comments_qs:
            uid = str(c.use_case_id)
            if uid not in comments_map:
                comments_map[uid] = []
            author = c.author
            author_name = f'{author.first_name} {author.last_name}'.strip() if (author.first_name or author.last_name) else author.username
            comments_map[uid].append({
                'id': str(c.id),
                'author_full_name': author_name,
                'content': c.content,
                'created_at': c.created_at.isoformat() if c.created_at else '',
            })

        assignments = UseCaseAssignment.objects.filter(use_case_id__in=uc_ids).values_list('id', 'use_case_id')
        assignment_ids = [a[0] for a in assignments]
        assignment_uc_map = {str(a[0]): str(a[1]) for a in assignments}

        screenshots_qs = UseCaseScreenshot.objects.filter(assignment_id__in=assignment_ids)
        screenshots_map = {}
        for s in screenshots_qs:
            uid = assignment_uc_map.get(str(s.assignment_id))
            if uid:
                if uid not in screenshots_map:
                    screenshots_map[uid] = []
                img_path = local_path(s.image)
                screenshots_map[uid].append({
                    'id': str(s.id),
                    'caption': s.caption or '',
                    'image_path': img_path,
                    'uploaded_at': s.uploaded_at.isoformat() if s.uploaded_at else '',
                })

        uc_extras = {}
        for uc_id in uc_ids:
            uc_extras[uc_id] = {
                'comments': comments_map.get(uc_id, []),
                'screenshots': screenshots_map.get(uc_id, []),
            }

        generator = ExcelGenerator(
            use_cases,
            source_filename=job.source_filename,
            company_name=job.company_name,
            excel_filename=job.effective_excel_filename,
            logo_path=logo_path,
            uc_extras=uc_extras,
        )
        excel_buffer = generator.generate()

        filename = job.effective_excel_filename

        response = HttpResponse(
            excel_buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class GenerateGherkinAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, job_id):
        job = get_object_or_404(ConversionJob, id=job_id, status=ConversionJob.Status.DONE)
        check_job_access(request.user, job)
        use_cases = job.use_cases.all()
        mode = request.query_params.get('mode', 'gherkin')

        automated = use_cases.filter(is_automated=True)
        base = job.source_filename.rsplit('.', 1)[0]

        if mode == 'cypress':
            zip_buffer = generate_cypress_project_zip(use_cases, company_name=job.company_name)
            filename = f"cypress_project_{base}.zip"
        else:
            zip_buffer = generate_gherkin_only_zip(use_cases)
            filename = f"gherkin_features_{base}.zip"

        response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class FileSearchAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        query = request.query_params.get('q', '').strip()
        ext_filter = request.query_params.getlist('ext')

        if not query or len(query) < 2:
            return Response({'results': [], 'error': 'Requête trop courte (min 2 caractères)'})

        extensions = ext_filter if ext_filter else None

        try:
            if not settings.FILE_SEARCH_ROOT:
                return Response({'results': [], 'count': 0, 'error': 'Recherche locale désactivée sur ce serveur.'})
            results = search_files(query=query, search_root=settings.FILE_SEARCH_ROOT, extensions=extensions, max_results=15)
            return Response({'results': results, 'count': len(results)})
        except Exception as e:
            logger.exception("File search error")
            return Response({'error': str(e), 'results': []}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BatchSaveAPIView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]

    def post(self, request, job_id):
        job = get_object_or_404(ConversionJob, id=job_id)
        check_job_access(request.user, job, write=True)
        actor = request.user if request.user.is_authenticated else None
        valid_statuses = ExtractedUseCase.UCStatus.values
        for uc_data in request.data.get('use_cases', []):
            if 'status' in uc_data and uc_data['status'] not in valid_statuses:
                return Response({'error': f'Statut invalide. Valides: {", ".join(valid_statuses)}'},
                                status=status.HTTP_400_BAD_REQUEST)

        if 'company_name' in request.data:
            job.company_name = request.data['company_name']
        if 'excel_filename' in request.data:
            job.excel_filename = request.data['excel_filename']
        job.save()

        with grouped_alerts():
            self._save_cases(request, job, actor)
        automated_count = job.use_cases.filter(is_automated=True).count()
        return Response({'success': True, 'automated_count': automated_count})

    def _save_cases(self, request, job, actor):
        for uc_data in request.data.get('use_cases', []):
            uc = get_object_or_404(ExtractedUseCase, id=uc_data['id'], job=job)
            uc._status_actor = actor
            uc.use_case_text    = uc_data.get('use_case_text', uc.use_case_text)
            uc.description      = uc_data.get('description', uc.description)
            uc.preconditions    = uc_data.get('preconditions', uc.preconditions)
            uc.steps            = uc_data.get('steps', uc.steps)
            uc.expected_results = uc_data.get('expected_results', uc.expected_results)
            uc.observed_results = uc_data.get('observed_results', uc.observed_results)
            uc.is_automated     = uc_data.get('is_automated', uc.is_automated)
            uc.status           = uc_data.get('status', uc.status)
            if 'jira_ticket' in uc_data:
                uc.jira_ticket = clean_jira_ticket(uc_data['jira_ticket'])
            uc.save()


class UseCaseBulkStatusAPIView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]

    def post(self, request, job_id):
        job = get_object_or_404(ConversionJob, id=job_id)
        check_job_access(request.user, job, write=True)
        uc_ids = request.data.get('uc_ids', [])
        new_status = request.data.get('status')
        valid_statuses = [s.value for s in ExtractedUseCase.UCStatus]

        if new_status not in valid_statuses:
            return Response({'error': f'Statut invalide. Valides: {", ".join(valid_statuses)}'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Sauvegarde cas par cas : chaque changement alimente l'historique et les sprints.
        updated = 0
        with grouped_alerts(), transaction.atomic():
            for uc in ExtractedUseCase.objects.filter(id__in=uc_ids, job=job):
                uc.status = new_status
                uc._status_actor = request.user if request.user.is_authenticated else None
                uc.save(update_fields=['status'])
                updated += 1
        return Response({'updated': updated, 'status': new_status})


class OpenVSCodeAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, job_id):
        job = get_object_or_404(ConversionJob, id=job_id, status=ConversionJob.Status.DONE)
        check_job_access(request.user, job)
        use_cases = job.use_cases.all()

        zip_buffer = generate_cypress_project_zip(
            use_cases,
            company_name=job.company_name,
        )

        project_dir = os.path.join(
            tempfile.gettempdir(),
            f"cypress_qa_{str(job.id)[:8]}"
        )
        os.makedirs(project_dir, exist_ok=True)
        with zipfile.ZipFile(zip_buffer, 'r') as zf:
            zf.extractall(project_dir)

        vscode_opened = False
        try:
            if platform.system() == 'Windows':
                subprocess.Popen(['code', project_dir], shell=True)
            else:
                subprocess.Popen(['code', project_dir])
            vscode_opened = True
        except FileNotFoundError:
            vscode_opened = False

        automated_count = job.use_cases.filter(is_automated=True).count()
        return Response({
            'success': True,
            'vscode_opened': vscode_opened,
            'project_path': project_dir,
            'automated_count': automated_count,
            'message': (
                'VS Code ouvert avec le projet Cypress.'
                if vscode_opened else
                'Projet généré. Commande "code" introuvable dans le PATH.'
            ),
        })


class GenerateCSVAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, job_id):
        job = get_object_or_404(ConversionJob, id=job_id, status=ConversionJob.Status.DONE)
        check_job_access(request.user, job)
        use_cases = job.use_cases.all().order_by('order')

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['N°', 'CAS', 'Use Case ID', 'Description', 'Préconditions',
                         'Étapes', 'Résultats Attendus', 'Résultats Observés',
                         'Statut', 'Automatisé'])

        for uc in use_cases:
            writer.writerow([
                uc.order,
                f"UC-{uc.order:03d}",
                uc.use_case_text,
                uc.description,
                uc.preconditions,
                uc.steps,
                uc.expected_results,
                uc.observed_results,
                uc.status,
                'Oui' if uc.is_automated else 'Non',
            ])

        filename = job.source_filename.rsplit('.', 1)[0] if job.source_filename else 'recettes'
        response = HttpResponse(output.getvalue(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="recettes_{filename}.csv"'
        return response


# ─────────────────────────────────────────────────────────────────────────────
# Aperçu d'import et validation des tickets Jira
# ─────────────────────────────────────────────────────────────────────────────

class UploadAnalyzeAPIView(APIView):
    """Aperçu avant import : rien n'est enregistré."""
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        from apps.core.word_import import analyse_word_document, uploaded_docx
        word_file = request.FILES.get('word_file')
        if not word_file:
            return Response({'error': 'Le fichier Word est requis.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_word_upload(word_file)
        except WordFileValidationError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        try:
            with uploaded_docx(word_file) as path:
                return Response(analyse_word_document(path))
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)


class JobJiraValidateAPIView(APIView):
    """
    Vérifie les tickets Jira d'une recette (format, puis existence si Jira est configuré).
    Corps optionnel : {"tickets": [...]} ; par défaut, les tickets de tous les cas de la recette.
    """
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]

    def post(self, request, job_id):
        from apps.qamanagement import jira
        job = get_object_or_404(ConversionJob, id=job_id)
        check_job_access(request.user, job)
        tickets = request.data.get('tickets')
        if tickets is None:
            tickets = list(job.use_cases.exclude(jira_ticket='').values_list('jira_ticket', flat=True))
        if not isinstance(tickets, list) or not all(isinstance(t, str) for t in tickets):
            return Response({'tickets': ['Liste de références attendue.']}, status=status.HTTP_400_BAD_REQUEST)
        if not request.user.is_authenticated:
            # Sans compte : contrôle de format uniquement (pas d'interrogation de Jira).
            from apps.core.use_cases import is_valid_jira_key, split_jira_keys
            keys = list(dict.fromkeys(k for t in tickets for k in split_jira_keys(t)))
            return Response({'jira_checked': False, 'error': '', 'results': [
                {'ticket': k, 'valid_format': is_valid_jira_key(k), 'exists': None, 'summary': ''} for k in keys]})
        return Response(jira.check_tickets(tickets))
