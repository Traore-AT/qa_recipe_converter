import uuid
from django.db import models
from django.contrib.auth.models import User


class ConversionJob(models.Model):
    class Status(models.TextChoices):
        PENDING    = 'PENDING',    'En attente'
        PROCESSING = 'PROCESSING', 'En cours'
        DONE       = 'DONE',       'Terminé'
        ERROR      = 'ERROR',      'Erreur'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)
    status           = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # Source / output
    source_filename  = models.CharField(max_length=255)
    word_file        = models.FileField(upload_to='uploads/word/')
    excel_template   = models.FileField(upload_to='uploads/templates/', blank=True, null=True)
    result_file      = models.FileField(upload_to='results/', blank=True, null=True)
    error_message    = models.TextField(blank=True, null=True)
    use_cases_count  = models.IntegerField(default=0)

    # ── En-tête personnalisée (v2.1) ───────────────────────────────────────
    company_name     = models.CharField(max_length=200, blank=True, default='',
                                        verbose_name="Nom de l'entreprise")
    excel_filename   = models.CharField(max_length=255, blank=True, default='',
                                        verbose_name='Nom du fichier Excel')
    company_logo     = models.FileField(upload_to='uploads/logos/', blank=True, null=True,
                                        verbose_name='Logo entreprise')

    # ── Liaison Teams / Projects (v2.2) ────────────────────────────────────
    project     = models.ForeignKey(
        'teams.Project', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='conversion_jobs',
        verbose_name='Projet'
    )
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='conversion_jobs',
        verbose_name='Uploadé par'
    )

    class Meta:
        ordering         = ['-created_at']
        verbose_name     = 'Tâche de conversion'
        verbose_name_plural = 'Tâches de conversion'

    def __str__(self):
        return f"{self.source_filename} — {self.get_status_display()} ({self.created_at:%d/%m/%Y %H:%M})"

    @property
    def effective_excel_filename(self):
        """Returns the user-defined filename or a sensible default."""
        if self.excel_filename:
            name = self.excel_filename.strip()
            if not name.lower().endswith('.xlsx'):
                name += '.xlsx'
            return name
        base = self.source_filename.rsplit('.', 1)[0]
        return f"recette_{base}.xlsx"


class ExtractedUseCase(models.Model):
    class UCStatus(models.TextChoices):
        TO_TEST     = 'À tester', 'À tester'
        IN_PROGRESS = 'En cours', 'En cours'
        PASSED      = 'Passé',    'Passé'
        FAILED      = 'Échoué',   'Échoué'
        BLOCKED     = 'Bloqué',   'Bloqué'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job              = models.ForeignKey(ConversionJob, on_delete=models.CASCADE, related_name='use_cases')
    order            = models.IntegerField(default=0)
    use_case_text    = models.TextField(blank=True)
    description      = models.TextField(blank=True)
    preconditions    = models.TextField(blank=True)
    steps            = models.TextField(blank=True)
    expected_results = models.TextField(blank=True)
    observed_results = models.TextField(blank=True)
    is_automated     = models.BooleanField(default=False)
    status           = models.CharField(max_length=20, choices=UCStatus.choices, default=UCStatus.TO_TEST)

    class Meta:
        ordering     = ['order']
        verbose_name = 'Use Case extrait'
        verbose_name_plural = 'Use Cases extraits'

    def __str__(self):
        return f"UC {self.order} — {self.use_case_text or 'Sans ID'}"
