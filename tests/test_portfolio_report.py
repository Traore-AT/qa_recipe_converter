"""Rapport global d'une sélection de projets (bouton « Rapports » de l'équipe)."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import ExtractedUseCase
from apps.qamanagement import analytics
from apps.qamanagement.models import ActivityLog, Sprint, UseCaseAssignment
from apps.teams.models import Project, ProjectMember
from tests.test_report_narrative import campaign, pdf_text, set_status  # noqa: F401  (fixture)
from tests.test_sprint_separation_jira_import import api, env  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db


@pytest.fixture
def portfolio(campaign):
    """Portail (campagne mitigée) + Intranet (4 cas, 3 passés, 1 à tester) + Archives (sans cas)."""
    env = campaign
    env.intranet = Project.objects.create(team=env.team, name='Intranet', created_by=env.owner)
    sprint = Sprint.objects.create(project=env.intranet, name='Lot 1', start_date=timezone.localdate() - timedelta(days=3),
                                   end_date=timezone.localdate() + timedelta(days=10), status='active', created_by=env.owner)
    for i in range(4):
        case = ExtractedUseCase.objects.create(project=env.intranet, use_case_text=f'Page {i}')
        UseCaseAssignment.objects.create(use_case=case, sprint=sprint)
        if i < 3:
            set_status(case, 'Passé', sprint)
    env.archives = Project.objects.create(team=env.team, name='Archives', created_by=env.owner)
    env.url = f'/api/teams/{env.team.slug}/portfolio/'
    return env


def ids(*projects):
    return ','.join(str(p.id) for p in projects)


class TestPortfolioPerformance:
    def test_global_level_weights_projects_by_their_cases(self, portfolio):
        perfs = [analytics.project_performance(p) for p in (portfolio.project, portfolio.intranet)]
        overall = analytics.portfolio_performance(perfs)
        assert overall['stats']['total'] == sum(p['stats']['total'] for p in perfs)
        assert overall['stats']['passed'] == sum(p['stats']['passed'] for p in perfs)
        assert overall['stats']['success_rate'] == round(overall['stats']['passed'] / overall['stats']['total'] * 100, 1)
        assert overall['defects']['active'] == sum(p['defects']['active'] for p in perfs)
        assert overall['score'] is not None and overall['level_label']
        # Même formule qu'un projet : un portefeuille d'un seul projet a exactement son score.
        single = analytics.portfolio_performance([perfs[1]])
        assert single['score'] == perfs[1]['score'] and single['level'] == perfs[1]['level']

    def test_portfolio_without_executions_is_not_evaluated(self, env):
        empty = Project.objects.create(team=env.team, name='Vide', created_by=env.owner)
        overall = analytics.portfolio_performance([analytics.project_performance(empty)])
        assert overall['score'] is None and overall['level'] == 'not_evaluated'


class TestPortfolioAPI:
    def test_preview_of_the_selection(self, portfolio):
        resp = api(portfolio.owner).get(portfolio.url, {'projects': ids(portfolio.project, portfolio.intranet)})
        assert resp.status_code == 200, resp.data
        assert [p['name'] for p in resp.data['projects']] == ['Intranet', 'Portail']
        assert resp.data['performance']['stats']['total'] == sum(
            p['performance']['stats']['total'] for p in resp.data['projects'])
        assert resp.data['projects'][0]['sprints'] == 1

    def test_all_visible_projects_by_default(self, portfolio):
        names = [p['name'] for p in api(portfolio.owner).get(portfolio.url).data['projects']]
        assert names == ['Archives', 'Intranet', 'Portail']

    def test_invalid_or_foreign_projects(self, portfolio):
        from apps.teams.models import Team
        other_team = Team.objects.create(name='Autre', owner=portfolio.owner)
        foreign = Project.objects.create(team=other_team, name='Ailleurs', created_by=portfolio.owner)
        client = api(portfolio.owner)
        assert client.get(portfolio.url, {'projects': 'abc'}).status_code == 400
        assert client.get(portfolio.url, {'projects': ids(foreign)}).status_code == 400

    def test_private_project_of_others_is_hidden(self, portfolio):
        secret = Project.objects.create(team=portfolio.team, name='Secret', created_by=portfolio.owner, visibility='private')
        ProjectMember.objects.create(project=secret, user=portfolio.owner, role='lead')
        names = [p['name'] for p in api(portfolio.tester).get(portfolio.url).data['projects']]
        assert 'Secret' not in names
        assert api(portfolio.tester).get(portfolio.url, {'projects': ids(secret)}).status_code == 400

    def test_outsider_is_refused(self, portfolio):
        outsider = type(portfolio.owner).objects.create_user('x', 'x@test.com', 'pass1234')
        assert api(outsider).get(portfolio.url).status_code == 403
        assert api(outsider).get(portfolio.url + 'pdf/').status_code == 403


class TestPortfolioPDF:
    def test_pdf_reflects_each_project_and_the_global_level(self, portfolio):
        resp = api(portfolio.owner).get(portfolio.url + 'pdf/', {'projects': ids(portfolio.project, portfolio.intranet)})
        assert resp.status_code == 200 and resp['Content-Type'] == 'application/pdf'
        assert 'rapport-global-' in resp['Content-Disposition'] and '2-projets' in resp['Content-Disposition']
        text = pdf_text(resp.content)
        for heading in ('Rapport global des projets', 'Introduction', 'Synthèse globale', 'Score global', 'Niveau global',
                        'Niveau de performance par projet', 'Score de performance par projet',
                        'Réussite et avancement par projet', 'Détail par projet', 'Ce qui a été réalisé',
                        'Conclusion et recommandations'):
            assert heading in text, heading
        assert 'Intranet — Actif' in text and 'Portail — Actif' in text
        assert 'sélectionnés de l' in text and 'Niveau de performance global' in text
        assert 'Lot 1' in text and 'Sprint 2' in text          # sprints de chaque projet
        assert 'FP-102' in text                                # cas en échec de Portail
        assert 'Archives' not in text                          # projet non sélectionné
        assert ActivityLog.objects.filter(action_type='portfolio_report', team=portfolio.team).exists()

    def test_single_project_and_unevaluated_project(self, portfolio):
        resp = api(portfolio.owner).get(portfolio.url + 'pdf/', {'projects': ids(portfolio.archives)})
        assert resp.status_code == 200 and 'archives' in resp['Content-Disposition']
        text = pdf_text(resp.content)
        assert 'Non évalué' in text and 'Aucun cas de test' in text

    def test_no_selection_means_every_visible_project(self, portfolio):
        text = pdf_text(api(portfolio.owner).get(portfolio.url + 'pdf/').content)
        assert all(name in text for name in ('Archives', 'Intranet', 'Portail'))
