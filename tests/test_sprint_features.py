"""
Fonctionnalités QA :
1. badge « Aussi dans » (other_sprints) ;
2. assignation : recherche Jira et répartition équilibrée entre plusieurs testeurs ;
3. statut d'un cas indépendant par sprint ;
4. aperçu d'import Word ;
5. validation des tickets Jira (format et existence).
"""
import io
import urllib.error
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from docx import Document

from apps.core.models import ConversionJob, ExtractedUseCase
from apps.core.use_cases import invalid_jira_keys, is_valid_jira_key, split_jira_keys
from apps.qamanagement import jira
from apps.qamanagement.campaigns import carry_over
from apps.qamanagement.models import Sprint, UseCaseAssignment, UseCaseExecution
from apps.teams.models import ProjectMember, TeamMember
from tests.test_sprint_separation_jira_import import DOCX, api, env, make_docx  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db
JIRA = dict(JIRA_BASE_URL='https://acme.atlassian.net', JIRA_EMAIL='qa@acme.test', JIRA_API_TOKEN='token')


def board(env, sprint):
    data = api(env.owner).get(f'{env.base}/sprints/{sprint.id}/board/').data
    return {str(a['use_case']): a for column in data['columns'].values() for a in column}


def set_status(env, case, value, sprint):
    resp = api(env.owner).patch(f'{env.base}/use-cases/{case.id}/status/',
                                {'status': value, 'sprint_id': str(sprint.id)}, format='json')
    assert resp.status_code == 200, resp.data
    return resp


@pytest.fixture
def shared(env):
    """Cas 1 dans le sprint 2 (actif) ET un sprint 3 ouvert, par report explicite."""
    env.s1.status = 'active'
    env.s1.save()
    env.s3 = Sprint.objects.create(project=env.project, name='Sprint 3', start_date=env.s2.start_date,
                                   end_date=env.s2.end_date, status='planned', created_by=env.owner)
    carry_over(env.s1, env.s3, env.owner)
    return env


# ─────────────────────────────────────────────────────────────────────────────
# 1 — Badge « Aussi dans »
# ─────────────────────────────────────────────────────────────────────────────
class TestOtherSprints:
    def test_card_lists_the_other_sprints_of_the_case(self, shared):
        card = board(shared, shared.s3)[str(shared.cases[0].id)]
        assert card['other_sprints'] == [{'id': str(shared.s1.id), 'name': 'Sprint 1', 'status': 'active',
                                          'case_status': 'À tester'}]
        assert board(shared, shared.s2)[str(shared.cases[2].id)]['other_sprints'] == []

    def test_board_query_count_does_not_grow_with_cases(self, shared, django_assert_max_num_queries):
        for i in range(10):
            case = ExtractedUseCase.objects.create(project=shared.project, use_case_text=f'Extra {i}')
            UseCaseAssignment.objects.create(use_case=case, sprint=shared.s3, assigned_to=shared.pm)
        with django_assert_max_num_queries(25):
            board(shared, shared.s3)


# ─────────────────────────────────────────────────────────────────────────────
# 2 — Recherche et répartition
# ─────────────────────────────────────────────────────────────────────────────
class TestDistribution:
    def test_assignable_cases_expose_jira_ticket(self, env):
        ExtractedUseCase.objects.filter(pk=env.cases[3].pk).update(jira_ticket='QA-77')
        rows = api(env.owner).get(f'{env.base}/unassigned-ucs/', {'sprint': str(env.s2.id)}).data['results']
        assert {r['id']: r['jira_ticket'] for r in rows}[str(env.cases[3].id)] == 'QA-77'

    def test_cases_are_spread_evenly_and_balance_existing_load(self, env):
        extra = [ExtractedUseCase.objects.create(project=env.project, use_case_text=f'N{i}') for i in range(5)]
        # Le testeur a déjà 1 cas dans le sprint 2 : l'autre membre reçoit d'abord.
        resp = api(env.owner).post(f'{env.base}/assignments/', {
            'use_case_ids': [str(c.id) for c in extra], 'sprint_id': str(env.s2.id),
            'user_ids': [env.tester.id, env.other.id]}, format='json')
        assert resp.status_code == 201, resp.data
        counts = {row['username']: row['count'] for row in resp.data['distribution']}
        assert counts == {'tester': 2, 'other': 3}
        assert UseCaseAssignment.objects.filter(sprint=env.s2, assigned_to__user=env.tester).count() == 3
        assert UseCaseAssignment.objects.filter(sprint=env.s2, assigned_to__user=env.other).count() == 3
        assert ProjectMember.objects.filter(project=env.project, user=env.other).exists()

    def test_case_held_by_one_member_goes_to_another(self, env):
        resp = api(env.owner).post(f'{env.base}/assignments/', {
            'use_case_ids': [str(env.cases[2].id)], 'sprint_id': str(env.s2.id),
            'user_ids': [env.tester.id, env.other.id]}, format='json')
        assert resp.status_code == 201
        assert {r['username']: r['count'] for r in resp.data['distribution']} == {'tester': 0, 'other': 1}

    @pytest.mark.parametrize('payload', [{}, {'user_id': 1, 'user_ids': [1]}, {'user_ids': []}, {'user_ids': [999999]}])
    def test_invalid_member_selection(self, env, payload):
        resp = api(env.owner).post(f'{env.base}/assignments/', {
            'use_case_ids': [str(env.cases[3].id)], 'sprint_id': str(env.s2.id), **payload}, format='json')
        assert resp.status_code == 400

    def test_viewer_in_distribution_is_refused(self, env):
        viewer = type(env.owner).objects.create_user('viewer', 'v@test.com', 'pass1234')
        TeamMember.objects.create(team=env.team, user=viewer, role='viewer')
        resp = api(env.owner).post(f'{env.base}/assignments/', {
            'use_case_ids': [str(env.cases[3].id)], 'sprint_id': str(env.s2.id),
            'user_ids': [env.tester.id, viewer.id]}, format='json')
        assert resp.status_code == 400
        assert not UseCaseAssignment.objects.filter(use_case=env.cases[3]).exists()


# ─────────────────────────────────────────────────────────────────────────────
# 3 — Statut par sprint
# ─────────────────────────────────────────────────────────────────────────────
class TestStatusPerSprint:
    def test_status_change_only_affects_its_sprint(self, shared):
        case = shared.cases[0]
        set_status(shared, case, 'Passé', shared.s3)
        assert board(shared, shared.s3)[str(case.id)]['status'] == 'Passé'
        assert board(shared, shared.s1)[str(case.id)]['status'] == 'À tester'
        case.refresh_from_db()
        assert case.status == 'Passé'  # dernier résultat connu
        assert shared.s1.stats['passed'] == 0 and shared.s3.stats['passed'] == 1
        assert board(shared, shared.s1)[str(case.id)]['other_sprints'][0]['case_status'] == 'Passé'

    def test_same_status_in_another_sprint_is_still_recorded(self, shared):
        case = shared.cases[0]
        set_status(shared, case, 'Échoué', shared.s3)
        set_status(shared, case, 'Échoué', shared.s1)  # global déjà « Échoué », mais pas dans le sprint 1
        assert board(shared, shared.s1)[str(case.id)]['status'] == 'Échoué'
        assert list(UseCaseExecution.objects.filter(use_case=case).values_list('sprint__name', flat=True)) == \
            ['Sprint 3', 'Sprint 1']

    def test_unchanged_status_records_nothing(self, shared):
        set_status(shared, shared.cases[0], 'À tester', shared.s3)
        assert not UseCaseExecution.objects.filter(use_case=shared.cases[0]).exists()

    def test_completing_a_sprint_keeps_its_own_results(self, shared):
        case = shared.cases[0]
        set_status(shared, case, 'Échoué', shared.s1)
        set_status(shared, case, 'Passé', shared.s3)
        shared.s1.status = 'completed'
        shared.s1.save()
        assert board(shared, shared.s1)[str(case.id)]['status'] == 'Échoué'
        assert board(shared, shared.s3)[str(case.id)]['status'] == 'Passé'

    def test_editing_a_card_does_not_overwrite_the_latest_status(self, shared):
        case = shared.cases[0]
        set_status(shared, case, 'Passé', shared.s3)
        assignment = shared.s1.assignments.get(use_case=case)
        resp = api(shared.owner).patch(f'{shared.base}/assignments/{assignment.id}/',
                                       {'description': 'maj', 'status': 'À tester'}, format='json')
        assert resp.status_code == 200
        case.refresh_from_db()
        assert case.status == 'Passé' and case.description == 'maj'

    def test_recipe_change_follows_the_active_sprint_only(self, shared):
        case = shared.cases[0]
        case.status = 'Bloqué'
        case.save()
        assert board(shared, shared.s1)[str(case.id)]['status'] == 'Bloqué'  # sprint actif
        assert board(shared, shared.s3)[str(case.id)]['status'] == 'À tester'  # sprint planifié

    def test_carry_over_keeps_the_source_sprint_status(self, env):
        case = env.cases[0]
        # Résultat obtenu dans le sprint 1, puis cas remis à tester ailleurs : le report garde « Bloqué ».
        UseCaseAssignment.objects.filter(use_case=case, sprint=env.s1).update(status='Bloqué')
        ExtractedUseCase.objects.filter(pk=case.pk).update(status='Bloqué')
        carry_over(env.s1, env.s2, env.owner)
        assert env.s2.assignments.get(use_case=case).status == 'Bloqué'


# ─────────────────────────────────────────────────────────────────────────────
# 4 — Aperçu d'import Word
# ─────────────────────────────────────────────────────────────────────────────
def analyse(client, content, name='recette.docx'):
    return client.post('/api/upload/analyze/', {
        'word_file': SimpleUploadedFile(name, content, content_type=DOCX)}, format='multipart')


class TestImportAnalysis:
    def test_reports_columns_jira_and_sample_without_saving(self, env):
        content = make_docx(['Use Case', 'Tickets Jira', 'Remarque', 'Étapes'], [
            ['UC01', 'QA-1', 'x', 'Ouvrir'], ['', '', '', ''], ['UC02', 'qa 2', '', 'Fermer'], ['UC03', '', '', 'Voir']])
        resp = analyse(api(env.owner), content)
        assert resp.status_code == 200, resp.data
        data = resp.data
        assert data['total_cases'] == 3 and data['with_jira'] == 2 and data['jira_column'] is True
        assert data['fields'] == ['use_case_text', 'steps', 'jira_ticket']
        table = data['tables'][0]
        assert table['rows'] == 3 and table['skipped_rows'] == 1 and table['ignored'] == ''
        assert table['columns'][1] == {'header': 'Tickets Jira', 'field': 'jira_ticket', 'label': 'Tickets Jira'}
        assert table['columns'][2] == {'header': 'Remarque', 'field': None, 'label': None}
        assert data['invalid_jira'] == [{'row': 2, 'ticket': 'qa 2'}]
        assert any('format inattendu' in w for w in data['warnings'])
        assert data['sample'][0]['jira_ticket'] == 'QA-1'
        assert ConversionJob.objects.count() == 0 and ExtractedUseCase.objects.filter(job__isnull=False).count() == 0

    def test_warns_when_no_jira_column(self, client):
        resp = analyse(client, make_docx(['Use Case', 'Étapes'], [['UC01', 'Ouvrir']]))
        assert resp.status_code == 200 and resp.data['jira_column'] is False
        assert any('Tickets Jira' in w for w in resp.data['warnings'])

    def test_ignored_tables_are_explained(self, client):
        doc = Document()
        doc.add_table(rows=1, cols=1).rows[0].cells[0].text = 'Titre'
        table = doc.add_table(rows=2, cols=2)
        table.rows[0].cells[0].text, table.rows[0].cells[1].text = 'Auteur', 'Version'
        buffer = io.BytesIO()
        doc.save(buffer)
        resp = analyse(client, buffer.getvalue())
        assert resp.status_code == 200
        assert [t['ignored'] for t in resp.data['tables']] == ['Moins de 2 lignes (pas de cas de test)',
                                                              'Aucune colonne reconnue']
        assert resp.data['total_cases'] == 0 and any('Aucun cas' in w for w in resp.data['warnings'])

    @pytest.mark.parametrize('content,name,code', [(b'', 'r.docx', 400), (b'not a zip', 'r.docx', 400),
                                                   (b'x', 'r.pdf', 400)])
    def test_invalid_files(self, client, content, name, code):
        assert analyse(client, content, name).status_code == code

    def test_missing_file(self, client):
        assert client.post('/api/upload/analyze/', {}, format='multipart').status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 5 — Validation des tickets Jira
# ─────────────────────────────────────────────────────────────────────────────
class FakeJira:
    def __init__(self, existing=(), failure=None):
        self.existing, self.failure, self.calls = set(existing), failure, []

    def __call__(self, method, path, payload=None):
        self.calls.append(path)
        if self.failure:
            raise jira.JiraError('Jira est injoignable.') from self.failure
        key = path.split('/')[1].split('?')[0]
        if key in self.existing:
            return {'fields': {'summary': f'Titre {key}'}}
        raise jira.JiraError('absent') from urllib.error.HTTPError(path, 404, 'Not Found', {}, None)


class TestJiraValidation:
    def test_format_helpers(self):
        assert split_jira_keys('QA-1, QA-2\nQA-1') == ['QA-1', 'QA-2']
        assert is_valid_jira_key('PROJ_2-15') and not is_valid_jira_key('qa-1') and not is_valid_jira_key('QA1')
        assert invalid_jira_keys('QA-1; bad, 2-QA') == ['bad', '2-QA']

    def test_without_jira_only_format_is_checked(self, env):
        resp = api(env.owner).post(f'{env.base}/jira/validate-tickets/', {'tickets': ['QA-1', 'oops']}, format='json')
        assert resp.status_code == 200
        assert resp.data['jira_checked'] is False
        assert [(r['ticket'], r['valid_format'], r['exists']) for r in resp.data['results']] == \
            [('QA-1', True, None), ('oops', False, None)]

    @override_settings(**JIRA)
    def test_existence_is_checked_when_jira_is_configured(self, env, monkeypatch):
        fake = FakeJira(existing={'QA-1'})
        monkeypatch.setattr(jira, 'api', fake)
        resp = api(env.owner).post(f'{env.base}/jira/validate-tickets/',
                                   {'tickets': ['QA-1, QA-404', 'bad key']}, format='json')
        results = {r['ticket']: r for r in resp.data['results']}
        assert resp.data['jira_checked'] is True
        assert results['QA-1']['exists'] is True and results['QA-1']['summary'] == 'Titre QA-1'
        assert results['QA-404']['exists'] is False
        assert results['bad key']['valid_format'] is False and results['bad key']['exists'] is None
        assert len(fake.calls) == 2  # les références mal formées ne sont pas envoyées à Jira

    @override_settings(**JIRA)
    def test_unreachable_jira_is_reported(self, env, monkeypatch):
        monkeypatch.setattr(jira, 'api', FakeJira(failure=OSError('down')))
        resp = api(env.owner).post(f'{env.base}/jira/validate-tickets/', {'tickets': ['QA-1']}, format='json')
        assert resp.data['jira_checked'] is False and 'injoignable' in resp.data['error']
        assert resp.data['results'][0]['exists'] is None

    def test_bad_payload_and_permissions(self, env):
        url = f'{env.base}/jira/validate-tickets/'
        assert api(env.owner).post(url, {'tickets': 'QA-1'}, format='json').status_code == 400
        outsider = type(env.owner).objects.create_user('x', 'x@test.com', 'pass1234')
        assert api(outsider).post(url, {'tickets': ['QA-1']}, format='json').status_code == 403

    @override_settings(**JIRA)
    def test_job_validation_uses_the_recipe_tickets(self, env, monkeypatch):
        monkeypatch.setattr(jira, 'api', FakeJira(existing={'QA-1'}))
        job = ConversionJob.objects.create(source_filename='r.docx', word_file='uploads/word/r.docx',
                                           status='DONE', project=env.project, uploaded_by=env.owner)
        for i, ticket in enumerate(['QA-1', 'QA-9', ''], start=1):
            ExtractedUseCase.objects.create(job=job, order=i, use_case_text=f'UC{i}', jira_ticket=ticket)
        resp = api(env.owner).post(f'/api/jobs/{job.id}/jira/validate/', {}, format='json')
        assert resp.status_code == 200
        assert {r['ticket']: r['exists'] for r in resp.data['results']} == {'QA-1': True, 'QA-9': False}

    @override_settings(**JIRA)
    def test_anonymous_job_never_queries_jira(self, client, monkeypatch):
        fake = FakeJira(existing={'QA-1'})
        monkeypatch.setattr(jira, 'api', fake)
        job = ConversionJob.objects.create(source_filename='r.docx', word_file='uploads/word/r.docx', status='DONE')
        resp = client.post(f'/api/jobs/{job.id}/jira/validate/', {'tickets': ['QA-1', 'x']},
                           content_type='application/json')
        assert resp.status_code == 200 and resp.json()['jira_checked'] is False
        assert [r['valid_format'] for r in resp.json()['results']] == [True, False] and fake.calls == []

    def test_job_validation_respects_access(self, env):
        job = ConversionJob.objects.create(source_filename='r.docx', word_file='uploads/word/r.docx',
                                           status='DONE', uploaded_by=env.owner)
        assert api(env.tester).post(f'/api/jobs/{job.id}/jira/validate/', {}, format='json').status_code == 403
        assert api(env.owner).post(f'/api/jobs/{job.id}/jira/validate/', {'tickets': 3},
                                   format='json').status_code == 400


class TestStatusPerSprintMigration:
    def test_open_sprint_assignments_take_the_current_case_status(self, env):
        from importlib import import_module
        from django.apps import apps as global_apps
        migration = import_module('apps.qamanagement.migrations.0011_status_per_sprint')
        ExtractedUseCase.objects.filter(pk=env.cases[2].pk).update(status='Passé')    # sprint 2 (actif)
        ExtractedUseCase.objects.filter(pk=env.cases[0].pk).update(status='Échoué')   # sprint 1 (terminé)
        UseCaseAssignment.objects.filter(use_case=env.cases[0]).update(status='Passé')
        migration.sync_open_sprints(global_apps, None)
        assert env.s2.assignments.get(use_case=env.cases[2]).status == 'Passé'
        assert env.s1.assignments.get(use_case=env.cases[0]).status == 'Passé'  # résultat figé conservé
