"""Import de cas directement dans un sprint depuis le Kanban."""
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.models import ConversionJob, ExtractedUseCase
from apps.qamanagement.campaigns import carry_over
from apps.qamanagement.models import ActivityLog, Sprint, UseCaseAssignment
from tests.test_sprint_separation_jira_import import DOCX, api, env, make_docx  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db

HEADERS = ['Use Case', 'Tickets Jira', 'Étapes']
ROWS = [
    ['Connexion', 'QA-1', 'Ouvrir'],
    ['Déconnexion', 'QA-2', 'Quitter'],
    ['Profil', 'qa 3', 'Voir'],
    ['Connexion', 'QA-1', 'Ouvrir'],      # ligne répétée dans le document
    ['Paiement', 'QA-1', 'Payer'],        # même story Jira, autre cas : légitime
]


def upload(rows=ROWS, name='recette.docx'):
    return SimpleUploadedFile(name, make_docx(HEADERS, rows), content_type=DOCX)


def post(env, sprint, user=None, rows=ROWS, **data):
    return api(user or env.owner).post(f'{env.base}/sprints/{sprint.id}/import-cases/',
                                       {'word_file': upload(rows), **data}, format='multipart')


def board_ids(env, sprint):
    data = api(env.owner).get(f'{env.base}/sprints/{sprint.id}/board/').data
    return {str(a['use_case']) for column in data['columns'].values() for a in column}


class TestPreview:
    def test_dry_run_reports_new_cases_and_duplicates_without_saving(self, env):
        ExtractedUseCase.objects.create(project=env.project, use_case_text='Déconnexion', jira_ticket='QA-2')
        before = ExtractedUseCase.objects.count()
        resp = post(env, env.s2, dry_run='true')
        assert resp.status_code == 200, resp.data
        assert resp.data['total_cases'] == 5 and resp.data['new_cases'] == 3 and resp.data['duplicates'] == 2
        rows = {r['row']: r for r in resp.data['rows']}
        assert rows[2]['duplicate_of'] == 6 and rows[4]['duplicate_row'] == 1
        assert rows[5]['duplicate_of'] is None and rows[5]['duplicate_row'] is None
        assert resp.data['invalid_jira'] == [{'row': 3, 'ticket': 'qa 3'}]
        assert resp.data['sprint'] == {'id': str(env.s2.id), 'name': 'Sprint 2'}
        assert ExtractedUseCase.objects.count() == before and ConversionJob.objects.count() == 0


class TestImport:
    def test_cases_belong_to_the_sprint_only(self, env):
        resp = post(env, env.s2)
        assert resp.status_code == 201, resp.data
        assert resp.data['imported'] == 4 and len(resp.data['skipped']) == 1
        job = ConversionJob.objects.get(pk=resp.data['job_id'])
        assert job.sprint == env.s2 and job.project == env.project and job.use_cases_count == 4
        cases = list(job.use_cases.order_by('order'))
        assert [c.jira_ticket for c in cases] == ['QA-1', 'QA-2', 'qa 3', 'QA-1']
        assert resp.data['case_numbers'] == [c.case_number for c in cases]
        assert {str(c.id) for c in cases} <= board_ids(env, env.s2)
        assert not ({str(c.id) for c in cases} & board_ids(env, env.s1))
        assert all(a.assigned_to is None for a in UseCaseAssignment.objects.filter(use_case__in=cases))
        # Jamais proposés ni acceptés dans un autre sprint.
        s3 = Sprint.objects.create(project=env.project, name='Sprint 3', start_date=env.s2.start_date,
                                   end_date=env.s2.end_date, created_by=env.owner)
        offered = {r['id'] for r in api(env.owner).get(f'{env.base}/unassigned-ucs/', {'sprint': str(s3.id)}).data['results']}
        assert not offered & {str(c.id) for c in cases}
        refused = api(env.owner).post(f'{env.base}/assignments/', {
            'use_case_ids': [str(cases[0].id)], 'sprint_id': str(s3.id), 'user_id': env.tester.id}, format='json')
        assert refused.status_code == 400 and 'Sprint 2' in refused.data['error']
        assert ActivityLog.objects.filter(action_type='sprint_cases_imported').exists()

    def test_status_cannot_be_reported_in_a_foreign_sprint(self, env):
        post(env, env.s2)
        case = ExtractedUseCase.objects.get(use_case_text='Profil')
        other = Sprint.objects.create(project=env.project, name='Sprint 3', start_date=env.s2.start_date,
                                      end_date=env.s2.end_date, status='active', created_by=env.owner)
        resp = api(env.owner).patch(f'{env.base}/use-cases/{case.id}/status/',
                                    {'status': 'Passé', 'sprint_id': str(other.id)}, format='json')
        assert resp.status_code == 400 and "n'appartient pas" in resp.data['error']
        assert api(env.owner).patch(f'{env.base}/use-cases/{case.id}/status/',
                                    {'status': 'Passé', 'sprint_id': str(env.s2.id)}, format='json').status_code == 200

    def test_imported_unassigned_cases_can_be_assigned_in_their_sprint(self, env):
        post(env, env.s2)
        case = ExtractedUseCase.objects.get(use_case_text='Profil')
        resp = api(env.owner).post(f'{env.base}/assignments/', {
            'use_case_ids': [str(case.id)], 'sprint_id': str(env.s2.id), 'user_id': env.other.id}, format='json')
        assert resp.status_code == 201
        assert env.s2.assignments.filter(use_case=case).count() == 1  # affectation en attente complétée

    def test_distribution_between_testers(self, env):
        with mock.patch('apps.teams.email.send_sprint_assignment_email') as send:
            resp = post(env, env.s2, user_ids=f'{env.tester.id},{env.other.id}')
        assert resp.status_code == 201, resp.data
        # Le testeur a déjà 1 cas dans le sprint 2 : l'autre reçoit le premier, puis alternance (totaux 3 / 2).
        counts = {d['username']: d['count'] for d in resp.data['distribution']}
        assert counts == {'tester': 2, 'other': 2}
        first = ExtractedUseCase.objects.get(use_case_text='Connexion')
        assert env.s2.assignments.get(use_case=first).assigned_to.user == env.other
        assert send.call_count == 4
        assert ActivityLog.objects.filter(action_type='sprint_cases_imported', target_users=env.other).exists()

    def test_reimporting_the_same_document_creates_nothing(self, env):
        assert post(env, env.s2).status_code == 201
        resp = post(env, env.s2)
        assert resp.status_code == 400 and 'Aucun nouveau cas' in resp.data['error']
        assert ConversionJob.objects.count() == 1

    def test_carry_over_remains_the_explicit_way_to_share(self, env):
        post(env, env.s2)
        s3 = Sprint.objects.create(project=env.project, name='Sprint 3', start_date=env.s2.start_date,
                                   end_date=env.s2.end_date, created_by=env.owner)
        carry_over(env.s2, s3, env.owner)
        case = ExtractedUseCase.objects.get(use_case_text='Profil')
        assert str(case.id) in board_ids(env, s3)

    def test_failure_rolls_everything_back(self, env):
        with mock.patch('apps.qamanagement.sprint_import.save_extracted_use_cases', side_effect=RuntimeError('boom')):
            with pytest.raises(RuntimeError):
                post(env, env.s2)
        assert ConversionJob.objects.count() == 0
        assert not ExtractedUseCase.objects.filter(use_case_text='Déconnexion').exists()


class TestRules:
    def test_frozen_sprint_is_refused(self, env):
        resp = post(env, env.s1)  # Sprint 1 terminé
        assert resp.status_code == 400 and 'terminé' in resp.data['error']
        assert post(env, env.s1, dry_run='true').status_code == 400

    def test_only_managers_can_import(self, env):
        assert post(env, env.s2, user=env.tester).status_code == 403

    def test_viewer_cannot_receive_cases(self, env):
        from apps.teams.models import TeamMember
        viewer = type(env.owner).objects.create_user('viewer', 'v@test.com', 'pass1234')
        TeamMember.objects.create(team=env.team, user=viewer, role='viewer')
        resp = post(env, env.s2, user_ids=str(viewer.id))
        assert resp.status_code == 400 and ExtractedUseCase.objects.filter(job__isnull=False).count() == 0

    @pytest.mark.parametrize('content,name', [(b'', 'r.docx'), (b'not a zip', 'r.docx'), (b'x', 'r.pdf')])
    def test_invalid_files(self, env, content, name):
        resp = api(env.owner).post(f'{env.base}/sprints/{env.s2.id}/import-cases/', {
            'word_file': SimpleUploadedFile(name, content, content_type=DOCX)}, format='multipart')
        assert resp.status_code == 400

    def test_missing_file_and_document_without_cases(self, env):
        url = f'{env.base}/sprints/{env.s2.id}/import-cases/'
        assert api(env.owner).post(url, {}, format='multipart').status_code == 400
        resp = api(env.owner).post(url, {'word_file': SimpleUploadedFile(
            'r.docx', make_docx(['Auteur', 'Version'], [['A', '1']]), content_type=DOCX)}, format='multipart')
        assert resp.status_code == 400 and 'Aucun nouveau cas' in resp.data['error']

    def test_unknown_sprint_and_bad_user_ids(self, env):
        assert api(env.owner).post(f'{env.base}/sprints/00000000-0000-0000-0000-000000000000/import-cases/',
                                   {'word_file': upload()}, format='multipart').status_code == 404
        assert post(env, env.s2, user_ids='abc').status_code == 400
