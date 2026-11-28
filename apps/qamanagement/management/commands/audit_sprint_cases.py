"""
Détecte les cas de test présents dans plusieurs sprints sans y avoir été reportés.

Avant la séparation des cas par sprint, la fenêtre d'assignation proposait aussi les cas d'autres
sprints : ils apparaissaient alors dans plusieurs Kanban. Cette commande les liste et, avec --fix,
retire l'affectation « intruse » (la plus récente) lorsqu'elle est sûre à supprimer :
sprint ouvert, standard, ni report ni capture d'écran, et aucune exécution dans ce sprint.

    python manage.py audit_sprint_cases               # rapport seul
    python manage.py audit_sprint_cases --project <slug> --fix
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.qamanagement.models import Sprint, UseCaseAssignment, UseCaseExecution

OPEN = (Sprint.Status.PLANNED, Sprint.Status.ACTIVE)


class Command(BaseCommand):
    help = 'Liste (et corrige avec --fix) les cas assignés par erreur dans plusieurs sprints.'

    def add_arguments(self, parser):
        parser.add_argument('--project', help='Slug du projet à analyser (tous par défaut).')
        parser.add_argument('--fix', action='store_true', help='Supprimer les affectations intruses sûres.')

    def handle(self, *args, project=None, fix=False, **options):
        qs = (UseCaseAssignment.objects.filter(sprint__isnull=False)
              .select_related('use_case', 'sprint__project').order_by('assigned_at'))
        if project:
            qs = qs.filter(sprint__project__slug=project)
        by_case = defaultdict(list)
        for assignment in qs:
            by_case[assignment.use_case_id].append(assignment)

        found = removed = 0
        for assignments in by_case.values():
            first_sprint = assignments[0].sprint_id
            # Un report ou une campagne de non-régression partagent légitimement un cas.
            intruders = [a for a in assignments if a.sprint_id != first_sprint and not a.carried_from_id
                         and a.sprint.kind == Sprint.Kind.STANDARD]
            for a in intruders:
                found += 1
                case = a.use_case
                safe = (a.sprint.status in OPEN and not a.screenshots.exists()
                        and not UseCaseExecution.objects.filter(use_case=case, sprint=a.sprint).exists())
                label = (f'[{a.sprint.project.name}] CAS-{case.case_number:03d} : « {assignments[0].sprint.name} » '
                         f'et « {a.sprint.name} »')
                if fix and safe:
                    with transaction.atomic():
                        a.delete()
                    removed += 1
                    self.stdout.write(self.style.SUCCESS(f'Retiré  {label}'))
                else:
                    hint = '' if safe else ' (à vérifier manuellement : résultats ou captures présents)'
                    self.stdout.write(f'Doublon {label}{hint}')

        summary = f'{found} affectation(s) en double détectée(s)'
        if fix:
            summary += f', {removed} retirée(s)'
        self.stdout.write(self.style.WARNING(summary) if found else self.style.SUCCESS(summary))
