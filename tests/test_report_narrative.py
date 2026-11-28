"""Phrases clés des rapports (sprint, multi-sprints, projet, rapport global) : conformes aux données."""
import io
from datetime import timedelta

import pytest
from django.utils import timezone
from pypdf import PdfReader

from apps.core.models import ExtractedUseCase
from apps.qamanagement import narrative
from apps.qamanagement.analytics import build_report
from apps.qamanagement.models import Defect, Sprint, UseCaseAssignment, UseCaseComment
from apps.teams.models import Project
from apps.teams.reports import generate_project_report, generate_weekly_report
from tests.test_sprint_separation_jira_import import api, env  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db


def pdf_text(content):
    return ' '.join(' '.join((page.extract_text() or '').split()) for page in PdfReader(io.BytesIO(content)).pages)


def set_status(case, status, sprint, actor=None, observed=''):
    case.refresh_from_db()
    case.status, case.observed_results = status, observed or case.observed_results
    case._status_sprint, case._status_actor = sprint, actor
    case.save()


@pytest.fixture
def campaign(env):
    """Sprint 2 actif : 6 cas — 3 passés, 2 échoués (dont un commenté), 1 bloqué ; 1 anomalie critique."""
    env.s2.goal = 'Valider le workflow de traitement des dossiers'
    env.s2.save()
    UseCaseAssignment.objects.filter(sprint=env.s2).delete()
    titles = ['Connexion', 'Déconnexion', 'Upload du justificatif', 'Validation du dossier', 'Export PDF', 'Profil']
    cases = [ExtractedUseCase.objects.create(project=env.project, use_case_text=t, jira_ticket=f'FP-{100 + i}')
             for i, t in enumerate(titles)]
    for case in cases:
        UseCaseAssignment.objects.create(use_case=case, sprint=env.s2, assigned_to=env.pm, assigned_by=env.owner)
    for case in cases[:3]:
        set_status(case, 'Passé', env.s2, env.tester)
    set_status(cases[2], 'Échoué', env.s2, env.tester, observed="L'upload d'un fichier de 6 Mo renvoie l'erreur 413.")
    set_status(cases[3], 'Échoué', env.s2, env.tester)
    UseCaseComment.objects.create(use_case=cases[3], author=env.tester,
                                  content='Le dossier est validé sans passer par le workflow de validation.')
    set_status(cases[4], 'Bloqué', env.s2, env.tester)
    set_status(cases[5], 'Passé', env.s2, env.tester)
    set_status(cases[0], 'Passé', env.s2, env.tester)
    set_status(cases[1], 'Passé', env.s2, env.tester)
    Defect.objects.create(project=env.project, sprint=env.s2, use_case=cases[3], title='Contournement du workflow',
                          severity='critical', assigned_to=env.other, reported_by=env.tester)
    env.campaign_cases = cases
    return env


class TestSprintNarrative:
    def test_sections_match_the_figures(self, campaign):
        n = build_report(campaign.project, [campaign.s2])['narrative']
        intro = ' '.join(n['introduction'])
        assert 'sprint « Sprint 2 »' in intro and 'Valider le workflow' in intro and '6 cas de test' in intro
        points = ' '.join(n['key_points'])
        assert 'Taux de réussite global : 50,0 % (3 / 6 cas validés)' in points
        assert 'Deux cas en échec et un cas bloqué nécessitent une action' in points
        assert n['satisfaction'] == {'code': 'insufficient', 'label': 'Insuffisant'}
        assert n['verdict'] == 'partielle'
        conclusion = ' '.join(n['conclusion'])
        assert 'La recette du sprint « Sprint 2 » est partielle : 3 cas sur 6 sont validés (50,0 %)' in conclusion
        assert 'CAS-' in conclusion and 'FP-102' in conclusion and 'Une anomalie demeure active' in conclusion
        assert 'conditionnée à la correction et au retest' in conclusion

    def test_failed_cases_carry_comments_owners_and_actions(self, campaign):
        n = build_report(campaign.project, [campaign.s2])['narrative']
        items = {i['jira_ticket']: i for i in n['attention_cases']}
        assert set(items) == {'FP-102', 'FP-103', 'FP-104'}
        assert '413' in items['FP-102']['observed'] and items['FP-102']['action'] == 'Corriger puis retester'
        assert 'sans passer par le workflow' in items['FP-103']['comment']
        assert items['FP-103']['defects'][0]['assigned_to'] == 'other'
        assert items['FP-104']['action'] == 'Lever le blocage puis exécuter'
        owners = {r['owner']: r['actions'] for r in n['responsibilities']}
        assert 'Corriger CAS-' in owners['other'] and 'FP-103' in owners['other']
        assert 'Retester' in owners['tester'] and 'FP-102' in owners['tester']
        assert 'À attribuer (développement)' in owners

    def test_recommendations_follow_the_comments(self, campaign):
        recos = build_report(campaign.project, [campaign.s2])['narrative']['recommendations']
        text = ' '.join(recos)
        assert recos[0].startswith('Corriger les deux cas en échec, CAS-') and 'retest' in recos[0]
        assert 'Lever le blocage de CAS-' in text and 'exécuter ce cas' in text
        assert 'Traiter en priorité les anomalies critiques et majeures' in text and 'Contournement du workflow' in text
        assert any(r.startswith("Vérifier la gestion des fichiers") and 'FP-102' in r for r in recos)
        assert any(r.startswith('Sécuriser le workflow métier') and 'FP-103' in r for r in recos)
        assert any('Compléter le résultat observé' in r and 'FP-104' in r for r in recos)
        assert any("Créer une anomalie pour" in r and 'FP-102' in r for r in recos)

    def test_full_success_is_qualified(self, campaign):
        for case in campaign.campaign_cases:
            set_status(case, 'Passé', campaign.s2, campaign.tester)
        Defect.objects.update(status='closed')
        n = build_report(campaign.project, [campaign.s2])['narrative']
        assert n['satisfaction']['label'] == 'Très satisfaisant' and n['verdict'] == 'validée'
        assert n['attention_cases'] == []
        assert any("sans garantir l'absence totale de défauts" in line for line in n['conclusion'])

    def test_empty_sprint(self, env):
        empty = Sprint.objects.create(project=env.project, name='Vide', start_date=env.s2.start_date,
                                      end_date=env.s2.end_date, created_by=env.owner)
        n = build_report(env.project, [empty])['narrative']
        assert n['satisfaction']['code'] == 'not_evaluated'
        assert "Aucun cas de test n'est encore rattaché" in ' '.join(n['introduction'])
        assert n['recommendations'] and n['conclusion']

    def test_pdf_contains_every_section(self, campaign):
        text = pdf_text(generate_project_report(campaign.project, [campaign.s2], 'QA'))
        for heading in ('Introduction', 'Points clés', "Cas en échec et plan d'action", 'Répartition des actions',
                        'Conclusion et recommandations', 'Recommandations', 'Niveau de satisfaction'):
            assert heading in text
        assert text.index('Introduction') < text.index('Points clés') < text.index('Conclusion et recommandations')
        assert 'erreur 413' in text and 'Insuffisant' in text

    def test_excel_has_the_narrative_sheet(self, campaign):
        from openpyxl import load_workbook
        resp = api(campaign.owner).get(f'{campaign.base}/report/xlsx/', {'sprints': str(campaign.s2.id)})
        ws = load_workbook(io.BytesIO(resp.content))['Rapport']
        values = [c.value for row in ws.iter_rows() for c in row if c.value]
        assert 'Introduction' in values and 'Niveau de satisfaction' in values and 'Recommandation 1' in values


class TestMultiSprintAndProject:
    def test_multi_sprint_compares_sprints(self, campaign):
        set_status(campaign.cases[2], 'Passé', campaign.s2, campaign.tester)
        n = build_report(campaign.project, [campaign.s1, campaign.s2])['narrative']
        assert n['introduction'][0].startswith('Ce rapport consolide les résultats de 2 sprints du projet Portail')
        assert ' '.join(n['conclusion']).startswith('Sur les 2 sprints analysés, la recette')

    def test_project_report_wording(self, campaign):
        n = build_report(campaign.project)['narrative']
        assert "synthèse de l'ensemble des activités de test du projet Portail" in n['introduction'][0]
        assert ' '.join(n['conclusion']).startswith('À ce stade, la recette du projet Portail')


class TestPortfolioNarrative:
    def test_global_report_lists_projects_at_risk(self, campaign):
        healthy = Project.objects.create(team=campaign.team, name='Intranet', created_by=campaign.owner)
        sprint = Sprint.objects.create(project=healthy, name='S1', start_date=timezone.localdate(),
                                       end_date=timezone.localdate() + timedelta(days=7), status='active')
        case = ExtractedUseCase.objects.create(project=healthy, use_case_text='Accueil')
        UseCaseAssignment.objects.create(use_case=case, sprint=sprint)
        set_status(case, 'Passé', sprint)
        Project.objects.create(team=campaign.team, name='Nouveau', created_by=campaign.owner)
        text = pdf_text(generate_weekly_report(campaign.team, 'QA', user=campaign.owner))
        assert 'Ce rapport consolide l' in text and 'Appréciation par projet' in text
        assert 'Projets à surveiller : Portail' in text
        assert 'Meilleur résultat : Intranet' in text
        assert 'anomalies critiques encore actives : une anomalie sur Portail' in text
        assert "Ce rapport consolide l'état de la qualité des 3 projets" in text
        assert 'Nouveau' in text and 'Conclusion et recommandations' in text

    def test_daily_scrum_wording(self, env):
        from apps.teams.reports import generate_daily_scrum
        text = pdf_text(generate_daily_scrum(env.team, 'QA', user=env.owner))
        assert 'Ce point quotidien présente' in text and 'Niveau de satisfaction' in text

    def test_formatting_helpers(self):
        assert narrative.pct(75) == '75,0 %' and narrative.num(78.5) == '78,5'
        assert narrative.join(['a', 'b', 'c']) == 'a, b et c' and narrative.join(['a']) == 'a'
        assert narrative.plural(1, 'cas bloqué', 'cas bloqués') == '1 cas bloqué'
        many = [{'case_number': i, 'jira_ticket': ''} for i in range(1, 11)]
        assert narrative.refs(many, 3) == 'CAS-001, CAS-002 et CAS-003 et 7 autres'
