import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Sprint(models.Model):
    class Status(models.TextChoices):
        PLANNED   = 'planned',   'Planifié'
        ACTIVE    = 'active',    'En cours'
        COMPLETED = 'completed', 'Terminé'
        CLOSED    = 'closed',    'Clôturé'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project    = models.ForeignKey('teams.Project', on_delete=models.CASCADE, related_name='sprints')
    name       = models.CharField(max_length=200)
    goal       = models.TextField(blank=True, help_text='Objectif du sprint')
    start_date = models.DateField()
    end_date   = models.DateField()
    status     = models.CharField(max_length=12, choices=Status.choices, default=Status.PLANNED)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_sprints')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Sprint'
        verbose_name_plural = 'Sprints'

    def __str__(self):
        return f'{self.project.name} / {self.name}'

    @property
    def duration_days(self):
        return (self.end_date - self.start_date).days

    @property
    def stats(self):
        assignments = UseCaseAssignment.objects.filter(sprint=self)
        total = assignments.count()
        passed = assignments.filter(status='Passé').count()
        failed = assignments.filter(status='Échoué').count()
        blocked = assignments.filter(status='Bloqué').count()
        in_progress = assignments.filter(status='En cours').count()
        not_run = assignments.filter(status='À tester').count()
        return {
            'total': total,
            'passed': passed,
            'failed': failed,
            'blocked': blocked,
            'in_progress': in_progress,
            'not_run': not_run,
            'progress_pct': round((passed + failed) / total * 100, 1) if total > 0 else 0,
        }


class UseCaseAssignment(models.Model):
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    use_case     = models.ForeignKey('core.ExtractedUseCase', on_delete=models.CASCADE, related_name='assignments')
    sprint       = models.ForeignKey(Sprint, on_delete=models.SET_NULL, null=True, blank=True, related_name='assignments')
    assigned_to  = models.ForeignKey('teams.ProjectMember', on_delete=models.CASCADE, related_name='uc_assignments')
    assigned_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_assignments')
    assigned_at  = models.DateTimeField(auto_now_add=True)
    # Copy of UC status for quick filtering (synced via signal or save)
    status       = models.CharField(max_length=20, default='À tester')

    class Meta:
        ordering = ['assigned_at']
        unique_together = ('use_case', 'assigned_to')
        verbose_name = 'Affectation de cas'
        verbose_name_plural = 'Affectations de cas'

    def __str__(self):
        return f'{self.assigned_to.user.username} → UC#{self.use_case.order}'


class Defect(models.Model):
    class Severity(models.TextChoices):
        CRITICAL = 'critical', 'Critique'
        MAJOR    = 'major',    'Majeure'
        MINOR    = 'minor',    'Mineure'
        TRIVIAL  = 'trivial',  'Triviale'

    class Priority(models.TextChoices):
        HIGH   = 'high',   'Haute'
        MEDIUM = 'medium', 'Moyenne'
        LOW    = 'low',    'Basse'

    class Status(models.TextChoices):
        OPEN       = 'open',       'Ouvert'
        IN_PROGRESS = 'in_progress', 'En cours'
        RESOLVED   = 'resolved',   'Résolu'
        CLOSED     = 'closed',     'Fermé'
        REOPENED   = 'reopened',   'Réouvert'

    id                  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    use_case            = models.ForeignKey('core.ExtractedUseCase', on_delete=models.SET_NULL, null=True, blank=True, related_name='defects')
    project             = models.ForeignKey('teams.Project', on_delete=models.CASCADE, related_name='defects')
    title               = models.CharField(max_length=300)
    description         = models.TextField(blank=True)
    severity            = models.CharField(max_length=10, choices=Severity.choices, default=Severity.MAJOR)
    priority            = models.CharField(max_length=8, choices=Priority.choices, default=Priority.MEDIUM)
    status              = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    reported_by         = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='reported_defects')
    assigned_to         = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_defects')
    steps_to_reproduce  = models.TextField(blank=True)
    expected_behavior   = models.TextField(blank=True)
    actual_behavior     = models.TextField(blank=True)
    environment         = models.CharField(max_length=200, blank=True, help_text='Navigateur, OS, version…')
    attachment          = models.FileField(upload_to='defects/', blank=True, null=True)
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Anomalie'
        verbose_name_plural = 'Anomalies'

    def __str__(self):
        return f'[{self.get_severity_display()}] {self.title}'


class WeeklyReport(models.Model):
    class Status(models.TextChoices):
        DRAFT    = 'draft',    'Brouillon'
        SUBMITTED = 'submitted', 'Soumis'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user             = models.ForeignKey(User, on_delete=models.CASCADE, related_name='weekly_reports')
    team             = models.ForeignKey('teams.Team', on_delete=models.CASCADE, related_name='weekly_reports')
    week_start       = models.DateField(help_text='Lundi de la semaine')
    week_end         = models.DateField(help_text='Dimanche de la semaine')
    accomplishments  = models.TextField(blank=True, help_text='Ce qui a été accompli cette semaine')
    blockers         = models.TextField(blank=True, help_text='Problèmes rencontrés / blocages')
    next_week_plans  = models.TextField(blank=True, help_text='Prévisions pour la semaine prochaine')
    additional_notes = models.TextField(blank=True)
    status           = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    submitted_at     = models.DateTimeField(null=True, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-week_start']
        unique_together = ('user', 'team', 'week_start')
        verbose_name = 'Rapport hebdomadaire'
        verbose_name_plural = 'Rapports hebdomadaires'

    def __str__(self):
        return f'{self.user.username} — Semaine du {self.week_start}'

    def submit(self):
        self.status = WeeklyReport.Status.SUBMITTED
        self.submitted_at = timezone.now()
        self.save()


class ActivityLog(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project     = models.ForeignKey('teams.Project', on_delete=models.CASCADE, null=True, blank=True, related_name='activities')
    team        = models.ForeignKey('teams.Team', on_delete=models.CASCADE, null=True, blank=True, related_name='activities')
    actor       = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='activities')
    action_type = models.CharField(max_length=50, help_text="Ex: 'use_case_status', 'defect_created', 'sprint_created', 'weekly_report_submitted'")
    description = models.TextField()
    metadata    = models.JSONField(default=dict, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    is_read     = models.BooleanField(default=False)
    target_users = models.ManyToManyField(User, blank=True, related_name='notifications')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Activité'
        verbose_name_plural = 'Activités'

    def __str__(self):
        return f'[{self.action_type}] {self.description[:60]}'


class UseCaseComment(models.Model):
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    use_case   = models.ForeignKey('core.ExtractedUseCase', on_delete=models.CASCADE, related_name='comments')
    author     = models.ForeignKey(User, on_delete=models.CASCADE, related_name='uc_comments')
    content    = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Commentaire'
        verbose_name_plural = 'Commentaires'

    def __str__(self):
        return f'{self.author.username} — {self.created_at:%d/%m/%Y %H:%M}'


class UseCaseScreenshot(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment  = models.ForeignKey(UseCaseAssignment, on_delete=models.CASCADE, related_name='screenshots')
    image       = models.ImageField(upload_to='screenshots/')
    caption     = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='uploaded_screenshots')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']
        verbose_name = 'Capture d\'écran'
        verbose_name_plural = 'Captures d\'écran'

    def __str__(self):
        return f'Screenshot for {self.assignment} — {self.uploaded_at:%d/%m/%Y %H:%M}'
