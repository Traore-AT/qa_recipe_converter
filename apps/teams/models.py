import uuid
import re
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta


def _slugify(text: str, max_len: int = 60) -> str:
    """Simple slug generator — no external dependency needed."""
    text = text.lower().strip()
    for a, b in [('à','a'),('â','a'),('ä','a'),('é','e'),('è','e'),('ê','e'),
                 ('ë','e'),('î','i'),('ï','i'),('ô','o'),('ö','o'),('ù','u'),
                 ('û','u'),('ü','u'),('ç','c')]:
        text = text.replace(a, b)
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text).strip('-')
    return text[:max_len] or 'item'


# ─────────────────────────────────────────────────────────────────────────────
# TEAM
# ─────────────────────────────────────────────────────────────────────────────
class Team(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name        = models.CharField(max_length=150, verbose_name="Nom de l'équipe")
    slug        = models.SlugField(max_length=80, unique=True, blank=True)
    description = models.TextField(blank=True)
    owner       = models.ForeignKey(User, on_delete=models.PROTECT, related_name='owned_teams')
    avatar      = models.ImageField(upload_to='teams/avatars/', blank=True, null=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering         = ['-created_at']
        verbose_name     = 'Équipe'
        verbose_name_plural = 'Équipes'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)

    def _generate_unique_slug(self):
        base = _slugify(self.name)
        slug = base
        n = 1
        while Team.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f'{base}-{n}'
            n += 1
        return slug

    def get_member(self, user):
        return TeamMember.objects.filter(team=self, user=user).first()

    def is_owner(self, user):
        return self.owner == user

    def is_admin(self, user):
        m = self.get_member(user)
        return m and m.role in ('owner', 'admin')

    def is_member(self, user):
        return TeamMember.objects.filter(team=self, user=user).exists()


# ─────────────────────────────────────────────────────────────────────────────
# TEAM MEMBER
# ─────────────────────────────────────────────────────────────────────────────
class TeamMember(models.Model):
    class Role(models.TextChoices):
        OWNER  = 'owner',  'Propriétaire'
        ADMIN  = 'admin',  'Administrateur'
        MEMBER = 'member', 'Membre'
        VIEWER = 'viewer', 'Lecteur'

    id        = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team      = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='members')
    user      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='team_memberships')
    role      = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('team', 'user')
        ordering        = ['joined_at']
        verbose_name     = 'Membre de l\'équipe'
        verbose_name_plural = 'Membres des équipes'

    def __str__(self):
        return f'{self.user.username} — {self.team.name} ({self.role})'

    @property
    def can_manage(self):
        return self.role in ('owner', 'admin')


# ─────────────────────────────────────────────────────────────────────────────
# TEAM INVITATION
# ─────────────────────────────────────────────────────────────────────────────
class TeamInvitation(models.Model):
    class Status(models.TextChoices):
        PENDING  = 'pending',  'En attente'
        ACCEPTED = 'accepted', 'Acceptée'
        REJECTED = 'rejected', 'Refusée'
        EXPIRED  = 'expired',  'Expirée'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token      = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    team       = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='invitations')
    invited_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_invitations')
    email      = models.EmailField()
    role       = models.CharField(max_length=10, choices=TeamMember.Role.choices,
                                  default=TeamMember.Role.MEMBER)
    status     = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering         = ['-created_at']
        verbose_name     = "Invitation d'équipe"
        verbose_name_plural = "Invitations d'équipe"

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=7)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Invitation {self.email} → {self.team.name} ({self.status})'

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_usable(self):
        return self.status == 'pending' and not self.is_expired


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT
# ─────────────────────────────────────────────────────────────────────────────
class Project(models.Model):
    class Visibility(models.TextChoices):
        PRIVATE = 'private', 'Privé'
        TEAM    = 'team',    'Équipe'
        PUBLIC  = 'public',  'Public'

    class Status(models.TextChoices):
        ACTIVE    = 'active',    'Actif'
        ARCHIVED  = 'archived',  'Archivé'
        COMPLETED = 'completed', 'Terminé'

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name        = models.CharField(max_length=150)
    slug        = models.SlugField(max_length=80, blank=True)
    description = models.TextField(blank=True)
    team        = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='projects')
    created_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                    related_name='created_projects')
    avatar      = models.ImageField(upload_to='projects/avatars/', blank=True, null=True)
    icon        = models.CharField(max_length=50, blank=True,
                                   help_text='Emoji ou nom d\'icône Lucide/FontAwesome')
    color       = models.CharField(max_length=7, blank=True,
                                   help_text='Code hex, ex: #3B82F6')
    visibility  = models.CharField(max_length=10, choices=Visibility.choices,
                                   default=Visibility.TEAM)
    status      = models.CharField(max_length=10, choices=Status.choices,
                                   default=Status.ACTIVE)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together     = ('team', 'slug')
        ordering            = ['-created_at']
        verbose_name        = 'Projet'
        verbose_name_plural = 'Projets'

    def __str__(self):
        return f'{self.team.name} / {self.name}'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)

    def _generate_unique_slug(self):
        base = _slugify(self.name)
        slug = base
        n = 1
        while Project.objects.filter(team=self.team, slug=slug).exclude(pk=self.pk).exists():
            slug = f'{base}-{n}'
            n += 1
        return slug

    @property
    def is_archived(self):
        return self.status == 'archived'

    @property
    def stats(self):
        """Compute UC stats from linked ConversionJobs."""
        from apps.core.models import ExtractedUseCase
        ucs = ExtractedUseCase.objects.filter(job__project=self)
        total    = ucs.count()
        passed   = ucs.filter(status='Passé').count()
        failed   = ucs.filter(status='Échoué').count()
        blocked  = ucs.filter(status='Bloqué').count()
        not_run  = ucs.filter(status='À tester').count()
        automated = ucs.filter(is_automated=True).count()
        rate     = round(passed / total * 100, 1) if total > 0 else 0
        return {
            'total': total,
            'passed': passed,
            'failed': failed,
            'blocked': blocked,
            'not_run': not_run,
            'automated': automated,
            'success_rate': rate,
        }


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT MEMBER
# ─────────────────────────────────────────────────────────────────────────────
class ProjectMember(models.Model):
    class Role(models.TextChoices):
        LEAD   = 'lead',   'Lead QA'
        TESTER = 'tester', 'Testeur'
        VIEWER = 'viewer', 'Lecteur'

    id       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project  = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='members')
    user     = models.ForeignKey(User, on_delete=models.CASCADE, related_name='project_memberships')
    role     = models.CharField(max_length=10, choices=Role.choices, default=Role.TESTER)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together     = ('project', 'user')
        ordering            = ['added_at']
        verbose_name        = 'Membre du projet'
        verbose_name_plural = 'Membres des projets'

    def __str__(self):
        return f'{self.user.username} — {self.project.name} ({self.role})'
