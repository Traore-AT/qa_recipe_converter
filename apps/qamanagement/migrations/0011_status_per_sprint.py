"""
Statut par sprint : l'affectation devient la source du résultat d'un cas dans un sprint ouvert.

Jusqu'ici un sprint ouvert affichait le statut global du cas ; on recopie ce statut dans ses
affectations pour que l'affichage soit identique au moment de la mise à jour. Les sprints
terminés/clôturés gardent leurs résultats figés.
"""
from django.db import migrations
from django.db.models import OuterRef, Subquery

OPEN = ('planned', 'active')


def sync_open_sprints(apps, schema_editor):
    Assignment = apps.get_model('qamanagement', 'UseCaseAssignment')
    Case = apps.get_model('core', 'ExtractedUseCase')
    Assignment.objects.filter(sprint__status__in=OPEN).update(
        status=Subquery(Case.objects.filter(pk=OuterRef('use_case_id')).values('status')[:1]))


class Migration(migrations.Migration):

    dependencies = [
        ('qamanagement', '0010_automation_and_integrations'),
        ('core', '0009_usecase_optional_project_created_at'),
    ]

    operations = [
        migrations.RunPython(sync_open_sprints, migrations.RunPython.noop),
    ]
