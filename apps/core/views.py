"""
Core views — QA Recipe Converter v2.1
"""
import json
import logging
import subprocess
import platform
import zipfile
import tempfile
import os

from django.shortcuts   import render, redirect, get_object_or_404
from django.http        import HttpResponse, JsonResponse
from django.views       import View
from django.contrib     import messages
from django.utils       import timezone

from .models  import ConversionJob, ExtractedUseCase
from .forms   import UploadForm
from apps.parser.docx_parser       import DocxParser
from apps.parser.excel_generator   import ExcelGenerator
from apps.parser.gherkin_generator import (
    generate_cypress_project_zip,
    generate_gherkin_only_zip,
)

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _uc_to_dict(uc):
    return {
        'id':               str(uc.id),
        'order':            uc.order,
        'use_case_text':    uc.use_case_text,
        'description':      uc.description,
        'preconditions':    uc.preconditions,
        'steps':            uc.steps,
        'expected_results': uc.expected_results,
        'observed_results': uc.observed_results,
        'is_automated':     uc.is_automated,
        'status':           uc.status,
    }


# ── IndexView ──────────────────────────────────────────────────────────────────

class IndexView(View):
    template_name = 'core/index.html'

    def get(self, request):
        form       = UploadForm()
        recent_jobs = ConversionJob.objects.filter(status=ConversionJob.Status.DONE)[:5]
        return render(request, self.template_name, {
            'form': form,
            'recent_jobs': recent_jobs,
        })

    def post(self, request):
        form = UploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {
                'form': form,
                'recent_jobs': ConversionJob.objects.filter(status=ConversionJob.Status.DONE)[:5],
            })

        word_file      = form.cleaned_data['word_file']
        excel_template = form.cleaned_data.get('excel_template')
        company_name   = form.cleaned_data.get('company_name', '').strip()
        excel_filename = form.cleaned_data.get('excel_filename', '').strip()
        company_logo   = form.cleaned_data.get('company_logo')

        job = ConversionJob.objects.create(
            source_filename = word_file.name,
            word_file       = word_file,
            excel_template  = excel_template,
            company_name    = company_name,
            excel_filename  = excel_filename,
            company_logo    = company_logo,
            status          = ConversionJob.Status.PROCESSING,
        )

        try:
            parser    = DocxParser(job.word_file.path)
            use_cases = parser.extract_use_cases()

            if not use_cases:
                job.status        = ConversionJob.Status.ERROR
                job.error_message = "Aucun tableau de cas de tests détecté dans le document."
                job.save()
                messages.error(request, job.error_message)
                return redirect('core:index')

            for idx, uc_data in enumerate(use_cases):
                ExtractedUseCase.objects.create(
                    job              = job,
                    order            = idx + 1,
                    use_case_text    = uc_data.get('use_case_text', ''),
                    description      = uc_data.get('description', ''),
                    preconditions    = uc_data.get('preconditions', ''),
                    steps            = uc_data.get('steps', ''),
                    expected_results = uc_data.get('expected_results', ''),
                    observed_results = uc_data.get('observed_results', ''),
                )

            job.use_cases_count = len(use_cases)
            job.status          = ConversionJob.Status.DONE
            job.save()
            return redirect('core:preview', job_id=job.id)

        except Exception as e:
            logger.exception(f"Erreur lors du traitement du fichier {word_file.name}")
            job.status        = ConversionJob.Status.ERROR
            job.error_message = str(e)
            job.save()
            messages.error(request, f"Erreur lors du traitement : {e}")
            return redirect('core:index')


# ── PreviewView ────────────────────────────────────────────────────────────────

class PreviewView(View):
    template_name = 'core/preview.html'

    def get(self, request, job_id):
        job       = get_object_or_404(ConversionJob, id=job_id)
        use_cases = job.use_cases.all()
        automated_count = use_cases.filter(is_automated=True).count()
        return render(request, self.template_name, {
            'job':            job,
            'use_cases':      use_cases,
            'automated_count': automated_count,
            'use_cases_json': json.dumps([_uc_to_dict(uc) for uc in use_cases]),
        })

    def post(self, request, job_id):
        """Save manual edits (JSON body)."""
        job = get_object_or_404(ConversionJob, id=job_id)
        try:
            data = json.loads(request.body)

            # ── Update header fields if provided ──────────────────────────
            if 'company_name' in data:
                job.company_name = data['company_name']
            if 'excel_filename' in data:
                job.excel_filename = data['excel_filename']
            job.save()

            # ── Update use cases ──────────────────────────────────────────
            for uc_data in data.get('use_cases', []):
                uc = get_object_or_404(ExtractedUseCase, id=uc_data['id'], job=job)
                uc.use_case_text    = uc_data.get('use_case_text',    uc.use_case_text)
                uc.description      = uc_data.get('description',      uc.description)
                uc.preconditions    = uc_data.get('preconditions',    uc.preconditions)
                uc.steps            = uc_data.get('steps',            uc.steps)
                uc.expected_results = uc_data.get('expected_results', uc.expected_results)
                uc.observed_results = uc_data.get('observed_results', uc.observed_results)
                uc.is_automated     = uc_data.get('is_automated',     uc.is_automated)
                uc.status           = uc_data.get('status',           uc.status)
                uc.save()

            automated_count = job.use_cases.filter(is_automated=True).count()
            return JsonResponse({'success': True, 'automated_count': automated_count})

        except Exception as e:
            logger.exception("Save error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)


# ── GenerateExcelView ──────────────────────────────────────────────────────────

class GenerateExcelView(View):
    def get(self, request, job_id):
        job       = get_object_or_404(ConversionJob, id=job_id, status=ConversionJob.Status.DONE)
        use_cases = job.use_cases.all()

        logo_path = None
        if job.company_logo:
            try:
                logo_path = job.company_logo.path
            except Exception:
                logo_path = None

        generator = ExcelGenerator(
            use_cases,
            source_filename = job.source_filename,
            company_name    = job.company_name,
            excel_filename  = job.effective_excel_filename,
            logo_path       = logo_path,
        )
        excel_buffer = generator.generate()

        filename = job.effective_excel_filename
        response = HttpResponse(
            excel_buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


# ── GenerateGherkinView ────────────────────────────────────────────────────────

class GenerateGherkinView(View):
    """Download Gherkin .feature files or full Cypress project as ZIP."""

    def get(self, request, job_id):
        job       = get_object_or_404(ConversionJob, id=job_id, status=ConversionJob.Status.DONE)
        use_cases = job.use_cases.all()
        mode      = request.GET.get('mode', 'gherkin')  # 'gherkin' | 'cypress'

        automated = use_cases.filter(is_automated=True)
        total_auto = automated.count()

        if mode == 'cypress':
            zip_buffer = generate_cypress_project_zip(
                use_cases,
                company_name = job.company_name,
            )
            base     = job.source_filename.rsplit('.', 1)[0]
            filename = f"cypress_project_{base}.zip"
        else:
            zip_buffer = generate_gherkin_only_zip(use_cases)
            base       = job.source_filename.rsplit('.', 1)[0]
            filename   = f"gherkin_features_{base}.zip"

        response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


# ── OpenVSCodeView ─────────────────────────────────────────────────────────────

class OpenVSCodeView(View):
    """
    Generate the Cypress project into a temp folder and open VS Code.
    Returns JSON with { success, vscode_opened, project_path, message }.
    """

    def post(self, request, job_id):
        job       = get_object_or_404(ConversionJob, id=job_id, status=ConversionJob.Status.DONE)
        use_cases = job.use_cases.all()

        # ── Generate ZIP ──────────────────────────────────────────────────
        zip_buffer = generate_cypress_project_zip(
            use_cases,
            company_name = job.company_name,
        )

        # ── Extract to temp dir ───────────────────────────────────────────
        project_dir = os.path.join(
            tempfile.gettempdir(),
            f"cypress_qa_{str(job.id)[:8]}"
        )
        os.makedirs(project_dir, exist_ok=True)
        with zipfile.ZipFile(zip_buffer, 'r') as zf:
            zf.extractall(project_dir)

        # ── Open VS Code ──────────────────────────────────────────────────
        vscode_opened = False
        try:
            if platform.system() == 'Windows':
                subprocess.Popen(['code', project_dir], shell=True)
            else:
                subprocess.Popen(['code', project_dir])
            vscode_opened = True
        except FileNotFoundError:
            vscode_opened = False
        except Exception as ex:
            logger.warning(f"VS Code open failed: {ex}")
            vscode_opened = False

        automated_count = use_cases.filter(is_automated=True).count()

        return JsonResponse({
            'success':        True,
            'vscode_opened':  vscode_opened,
            'project_path':   project_dir,
            'automated_count': automated_count,
            'message': (
                f'VS Code ouvert ! Projet extrait dans : {project_dir}'
                if vscode_opened
                else f'Projet Cypress généré dans :\n{project_dir}\n\nVS Code non détecté (commande "code" introuvable).'
            ),
        })


# ── ResultView ─────────────────────────────────────────────────────────────────

class ResultView(View):
    template_name = 'core/result.html'

    def get(self, request, job_id):
        job             = get_object_or_404(ConversionJob, id=job_id)
        automated_count = job.use_cases.filter(is_automated=True).count()
        return render(request, self.template_name, {
            'job':             job,
            'automated_count': automated_count,
        })
