import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


CASE_STATUSES = ['À tester', 'En cours', 'Passé', 'Échoué', 'Bloqué']


class Sprint(models.Model):
    class Status(models.TextChoices):
        PLANNED   = 'planned',   'Planifié'
        ACTIVE    = 'active',    'En cours'
        COMPLETED = 'completed', 'Terminé'
        CLOSED    = 'closed',    'Clôturé'

    class Kind(models.TextChoices):
        STANDARD   = 'standard',   'Standard'
        REGRESSION = 'regression', 'Non-régression'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project    = models.ForeignKey('teams.Project', on_delete=models.CASCADE, related_name='sprints')
    kind       = models.CharField(max_length=12, choices=Kind.choices, default=Kind.STANDARD)
    report_sent_at = models.DateTimeField(null=True, blank=True)
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
    def is_frozen(self):
        """Un sprint terminé/clôturé conserve les résultats obtenus pendant son exécution."""
        return self.status in (self.Status.COMPLETED, self.Status.CLOSED)

    def case_statuses(self):
        """Statut de chaque cas dans ce sprint : {use_case_id: statut} (un cas = une seule entrée)."""
        return dict(self.assignments.values_list('use_case_id', 'status'))

    @property
    def stats(self):
        statuses = list(self.case_statuses().values())
        total = len(statuses)
        passed = statuses.count('Passé')
        failed = statuses.count('Échoué')
        return {
            'total': total,
            'passed': passed,
            'failed': failed,
            'blocked': statuses.count('Bloqué'),
            'in_progress': statuses.count('En cours'),
            'not_run': statuses.count('À tester'),
            'progress_pct': round((passed + failed) / total * 100, 1) if total > 0 else 0,
        }


class UseCaseAssignment(models.Model):
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    use_case     = models.ForeignKey('core.ExtractedUseCase', on_delete=models.CASCADE, related_name='assignments')
    sprint       = models.ForeignKey(Sprint, on_delete=models.SET_NULL, null=True, blank=True, related_name='assignments')
    assigned_to  = models.ForeignKey('teams.ProjectMember', on_delete=models.SET_NULL, null=True, blank=True, related_name='uc_assignments')
    assigned_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_assignments')
    assigned_at  = models.DateTimeField(auto_now_add=True)
    # Résultat du cas dans CE sprint : deux sprints partageant un cas (report) ont chacun le leur.
    status       = models.CharField(max_length=20, default='À tester')
    carried_from = models.ForeignKey(Sprint, on_delete=models.SET_NULL, null=True, blank=True, related_name='carried_assignments',
                                     help_text='Sprint dont le cas a été reporté')

    class Meta:
        ordering = ['assigned_at']
        constraints = [
            models.UniqueConstraint(fields=['use_case', 'assigned_to', 'sprint'], name='uniq_case_assignee_sprint'),
            models.UniqueConstraint(fields=['use_case', 'assigned_to'], condition=models.Q(sprint__isnull=True),
                                    name='uniq_case_assignee_without_sprint'),
        ]
        verbose_name = 'Affectation de cas'
        verbose_name_plural = 'Affectations de cas'

    def __str__(self):
        assignee = self.assigned_to.user.username if self.assigned_to_id else 'Non assigné'
        return f'{assignee} → CAS-{self.use_case.case_number:03d}'

    @property
    def effective_status(self):
        """Statut à afficher : résultat du cas dans son sprint ; hors sprint, dernier statut du cas."""
        return self.status if self.sprint_id else self.use_case.status


class UseCaseExecution(models.Model):
    """Historique des changements de statut d'un cas (source des courbes et des régressions)."""
    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    use_case      = models.ForeignKey('core.ExtractedUseCase', on_delete=models.CASCADE, related_name='executions')
    project       = models.ForeignKey('teams.Project', on_delete=models.CASCADE, related_name='executions')
    sprint        = models.ForeignKey(Sprint, on_delete=models.SET_NULL, null=True, blank=True, related_name='executions')
    old_status    = models.CharField(max_length=20, blank=True, default='')
    new_status    = models.CharField(max_length=20)
    is_regression = models.BooleanField(default=False, help_text='Cas précédemment Passé, désormais Échoué')
    executed_by   = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='executions')
    executed_at   = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['executed_at']
        indexes = [
            models.Index(fields=['project', 'executed_at'], name='exec_project_date_idx'),
            models.Index(fields=['use_case', 'executed_at'], name='exec_case_date_idx'),
        ]
        verbose_name = 'Exécution de cas'
        verbose_name_plural = 'Exécutions de cas'

    def __str__(self):
        return f'{self.use_case_id} : {self.old_status or "—"} → {self.new_status}'


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
    ticket_number       = models.CharField(max_length=20, unique=True, blank=True)
    sprint              = models.ForeignKey(Sprint, on_delete=models.SET_NULL, null=True, blank=True, related_name='defects')
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
    related_use_cases   = models.ManyToManyField('core.ExtractedUseCase', blank=True, related_name='related_defects',
                                                 help_text='Autres cas de test impactés')
    is_regression       = models.BooleanField(default=False)
    regression_of       = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name='regressions', help_text="Anomalie d'origine réapparue")
    resolution          = models.TextField(blank=True, help_text='Correction apportée / cause racine')
    resolved_at         = models.DateTimeField(null=True, blank=True)
    closed_at           = models.DateTimeField(null=True, blank=True)
    reopen_count        = models.PositiveIntegerField(default=0)
    jira_key            = models.CharField(max_length=50, unique=True, null=True, blank=True)
    jira_synced_at      = models.DateTimeField(null=True, blank=True)
    jira_sync_error     = models.CharField(max_length=500, blank=True, default='')
    environment         = models.CharField(max_length=200, blank=True, help_text='Navigateur, OS, version…')
    attachment          = models.FileField(upload_to='defects/', blank=True, null=True)
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Anomalie'
        verbose_name_plural = 'Anomalies'

    def save(self, *args, **kwargs):
        if not self.ticket_number:
            # Stable human-readable identifier without relying on a racy per-project sequence.
            self.ticket_number = f'TKT-{self.pk.hex[:8].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'[{self.get_severity_display()}] {self.title}'

    DONE_STATUSES = ('resolved', 'closed')

    @property
    def is_open(self):
        return self.status not in self.DONE_STATUSES

    def apply_status(self, new_status):
        """Change le statut et maintient dates de résolution/fermeture et compteur de réouverture."""
        was_done = self.status in self.DONE_STATUSES
        is_done = new_status in self.DONE_STATUSES
        now = timezone.now()
        if was_done and not is_done:
            self.reopen_count += 1
            self.resolved_at = self.closed_at = None
        if is_done:
            self.resolved_at = self.resolved_at or now
            self.closed_at = (self.closed_at or now) if new_status == self.Status.CLOSED else None
        self.status = new_status


class DefectComment(models.Model):
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    defect     = models.ForeignKey(Defect, on_delete=models.CASCADE, related_name='comments')
    author     = models.ForeignKey(User, on_delete=models.CASCADE, related_name='defect_comments', null=True, blank=True)
    external_author = models.CharField(max_length=180, blank=True, help_text='Auteur hors application (Jira)')
    jira_comment_id = models.CharField(max_length=40, blank=True, default='')
    content    = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(fields=['defect', 'jira_comment_id'], condition=~models.Q(jira_comment_id=''),
                                    name='uniq_defect_jira_comment'),
        ]
        verbose_name = "Commentaire d'anomalie"
        verbose_name_plural = "Commentaires d'anomalie"

    def __str__(self):
        return f'{self.author.username if self.author else self.external_author} — {self.defect.ticket_number}'


class DefectHistory(models.Model):
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    defect     = models.ForeignKey(Defect, on_delete=models.CASCADE, related_name='history')
    actor      = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='defect_changes')
    field      = models.CharField(max_length=40)
    old_value  = models.TextField(blank=True)
    new_value  = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Historique d'anomalie"
        verbose_name_plural = "Historiques d'anomalie"

    def __str__(self):
        return f'{self.defect.ticket_number} — {self.field}'


class ProjectQASettings(models.Model):
    """Alertes, envois automatiques et intégration Jira d'un projet."""
    project = models.OneToOneField('teams.Project', on_delete=models.CASCADE, related_name='qa_settings')
    alert_on_regression = models.BooleanField(default=True)
    alert_on_critical_defect = models.BooleanField(default=True)
    auto_report_on_close = models.BooleanField(default=True)
    jira_enabled = models.BooleanField(default=False)
    jira_project_key = models.CharField(max_length=20, blank=True, default='')
    jira_issue_type = models.CharField(max_length=50, default='Bug')
    jira_import = models.BooleanField(default=False, help_text='Importer les nouveaux tickets Jira comme anomalies')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Une clé partagée exposerait les tickets d'un projet à un autre.
            models.UniqueConstraint(fields=['jira_project_key'], condition=~models.Q(jira_project_key=''),
                                    name='uniq_jira_project_key'),
        ]
        verbose_name = 'Réglages QA'
        verbose_name_plural = 'Réglages QA'

    def __str__(self):
        return f'Réglages QA — {self.project}'

    @classmethod
    def for_project(cls, project):
        return cls.objects.get_or_create(project=project)[0]

    @property
    def jira_active(self):
        return self.jira_enabled and bool(self.jira_project_key)


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
    read_by     = models.ManyToManyField(User, blank=True, related_name='read_notifications')

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
