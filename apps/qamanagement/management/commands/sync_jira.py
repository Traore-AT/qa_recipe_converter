"""python manage.py sync_jira — synchronise tous les projets liés à Jira (tâche planifiée)."""
from django.core.management.base import BaseCommand

from apps.qamanagement.jira import JiraError, is_configured, sync_project
from apps.qamanagement.models import ProjectQASettings


class Command(BaseCommand):
    help = 'Synchronise les anomalies de tous les projets liés à Jira.'

    def handle(self, *args, **options):
        if not is_configured():
            self.stdout.write('Jira non configuré : rien à faire.')
            return
        failed = False
        for config in ProjectQASettings.objects.filter(jira_enabled=True).exclude(jira_project_key='').select_related('project__team'):
            try:
                result = sync_project(config.project)
                self.stdout.write(f'{config.project} : {result}')
            except JiraError as exc:
                failed = True
                self.stderr.write(f'{config.project} : {exc}')
        if failed:
            raise SystemExit(1)
