"""
Tests — rapports (projet / sprints), performance, évolution, anomalies, régressions,
édition de sprint, assignation des membres de l'équipe et correctifs associés.
"""
import io
from datetime import date, timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from pypdf import PdfReader
from rest_framework.test import APIClient

from apps.core.models import ConversionJob, ExtractedUseCase
from apps.qamanagement import analytics
from apps.qamanagement.models import (
    ActivityLog, Defect, DefectHistory, Sprint, UseCaseAssignment, UseCaseExecution, WeeklyReport,
)
from apps.teams.models import Project, ProjectMember, Team, TeamInvitation, TeamMember
from apps.teams.reports import COMPANY_FOOTER_LINES

pytestmark = pytest.mark.django_db


def make_user(name):
    return User.objects.create_user(name, f'{name}@test.com', 'pass1234')


@pytest.fixture
def env():
    """Équipe complète : owner, admin, testeur du projet, membre hors projet, lecteur, étranger."""
    class Env:
        pass
    e = Env()
    e.owner, e.admin, e.tester, e.member, e.viewer, e.outsider = (
        make_user(n) for n in ('owner', 'admin', 'tester', 'member', 'viewer', 'outsider'))
    e.team = Team.objects.create(name='Equipe QA', owner=e.owner)
    for user, role in ((e.owner, 'owner'), (e.admin, 'admin'), (e.tester, 'member'),
                       (e.member, 'member'), (e.viewer, 'viewer')):
        TeamMember.objects.create(team=e.team, user=user, role=role)
    e.project = Project.objects.create(team=e.team, name='Portail', created_by=e.owner)
    e.pm = ProjectMember.objects.create(project=e.project, user=e.tester, role='tester')
    e.cases = [ExtractedUseCase.objects.create(project=e.project, order=i, use_case_text=f'Cas é{i}')
               for i in range(1, 5)]
    today = timezone.localdate()
    e.s1 = Sprint.objects.create(project=e.project, name='Sprint 1', start_date=today - timedelta(days=20),
                                 end_date=today - timedelta(days=6), status='active', created_by=e.owner)
    e.s2 = Sprint.objects.create(project=e.project, name='Sprint 2', start_date=today - timedelta(days=5),
                                 end_date=today + timedelta(days=9), status='planned', created_by=e.owner)
    for case in e.cases[:2]:
        UseCaseAssignment.objects.create(use_case=case, sprint=e.s1, assigned_to=e.pm, assigned_by=e.owner)
    for case in e.cases[1:]:
        UseCaseAssignment.objects.create(use_case=case, sprint=e.s2, assigned_to=e.pm, assigned_by=e.owner)
    e.base = f'/api/teams/{e.team.slug}/projects/{e.project.slug}'
    return e


def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def set_status(case, status, **context):
    case.refresh_from_db()
    for key, value in context.items():
        setattr(case, f'_status_{key}', value)
    case.status = status
    case.save()


def pdf_pages(response):
    assert response.status_code == 200
    assert response['Content-Type'] == 'application/pdf'
    return PdfReader(io.BytesIO(response.content)).pages


# ─────────────────────────────────────────────────────────────────────────────
class TestExecutionTracking:
    def test_change_is_recorded_with_actor_and_sprint(self, env):
        resp = api(env.tester).patch(f'{env.base}/use-cases/{env.cases[0].id}/status/',
                                     {'status': 'Passé', 'sprint_id': str(env.s1.id)}, format='json')
        assert resp.status_code == 200
        execution = UseCaseExecution.objects.get()
        assert (execution.old_status, execution.new_status) == ('À tester', 'Passé')
        assert execution.executed_by == env.tester and execution.sprint == env.s1

    def test_same_status_and_other_fields_are_not_recorded(self, env):
        set_status(env.cases[0], 'À tester')
        env.cases[0].description = 'x'
        env.cases[0].save()
        assert not UseCaseExecution.objects.exists()

    def test_regression_survives_intermediate_statuses(self, env):
        for status in ('Passé', 'À tester', 'En cours', 'Échoué'):
            set_status(env.cases[0], status)
        assert UseCaseExecution.objects.get(is_regression=True).new_status == 'Échoué'

    def test_first_failure_is_not_a_regression(self, env):
        for status in ('Échoué', 'À tester', 'Échoué'):
            set_status(env.cases[0], status)
        assert not UseCaseExecution.objects.filter(is_regression=True).exists()

    def test_bulk_and_batch_updates_are_recorded(self, env):
        job = ConversionJob.objects.create(source_filename='a.docx', word_file='a.docx', project=env.project)
        case = ExtractedUseCase.objects.create(job=job, order=9)
        api(env.tester).post(f'/api/jobs/{job.id}/bulk-status/', {'uc_ids': [str(case.id)], 'status': 'Passé'}, format='json')
        api(env.tester).post(f'/api/jobs/{job.id}/save/', {'use_cases': [{'id': str(case.id), 'status': 'Échoué'}]}, format='json')
        assert list(case.executions.values_list('new_status', 'is_regression', 'executed_by__username')) == [
            ('Passé', False, 'tester'), ('Échoué', True, 'tester')]

    def test_batch_rejects_invalid_status(self, env):
        job = ConversionJob.objects.create(source_filename='a.docx', word_file='a.docx', project=env.project)
        case = ExtractedUseCase.objects.create(job=job, order=9)
        resp = api(env.tester).post(f'/api/jobs/{job.id}/save/',
                                    {'use_cases': [{'id': str(case.id), 'status': 'Nimp'}]}, format='json')
        assert resp.status_code == 400

    def test_completed_sprint_keeps_its_results(self, env):
        shared = env.cases[1]
        set_status(shared, 'Passé')
        env.s1.status = 'completed'
        env.s1.save()
        set_status(shared, 'Échoué')
        assert env.s1.stats['passed'] == 1 and env.s1.stats['failed'] == 0
        assert env.s2.stats['failed'] == 1
        board = api(env.owner).get(f'{env.base}/sprints/{env.s1.id}/board/').data
        assert [a['use_case'] for a in board['columns']['Passé']] == [shared.id]

    def test_completed_sprint_is_read_only_for_results(self, env):
        env.s1.status = 'closed'
        env.s1.save()
        assignment = env.s1.assignments.first()
        url = f'{env.base}/assignments/{assignment.id}/'
        assert api(env.owner).patch(url, {'status': 'Passé'}, format='json').status_code == 400
        # Renvoyer le même statut (formulaire complet) reste accepté.
        assert api(env.owner).patch(url, {'status': 'À tester', 'description': 'ok'}, format='json').status_code == 200
        resp = api(env.tester).patch(f'{env.base}/use-cases/{assignment.use_case_id}/status/',
                                     {'status': 'Passé', 'sprint_id': str(env.s1.id)}, format='json')
        assert resp.status_code == 400

    def test_viewer_cannot_change_status(self, env):
        resp = api(env.viewer).patch(f'{env.base}/use-cases/{env.cases[0].id}/status/', {'status': 'Passé'}, format='json')
        assert resp.status_code == 403

    def test_case_without_project_is_supported(self):
        job = ConversionJob.objects.create(source_filename='a.docx', word_file='a.docx')
        case = ExtractedUseCase.objects.create(job=job, order=3)
        case.status = 'Passé'
        case.save()
        assert case.ticket_number == 'CAS-003'
        assert not UseCaseExecution.objects.exists()


# ─────────────────────────────────────────────────────────────────────────────
class TestSprintEdition:
    def patch(self, env, data, user=None):
        return api(user or env.owner).patch(f'{env.base}/sprints/{env.s1.id}/', data, format='json')

    def test_rename_and_change_duration(self, env):
        resp = self.patch(env, {'name': 'Sprint Alpha', 'duration_days': 21})
        assert resp.status_code == 200
        env.s1.refresh_from_db()
        assert env.s1.name == 'Sprint Alpha' and env.s1.duration_days == 21
        log = ActivityLog.objects.get(action_type='sprint_updated')
        assert 'Sprint 1' in log.description and 'Sprint Alpha' in log.description

    def test_change_dates(self, env):
        resp = self.patch(env, {'start_date': '2026-01-01', 'end_date': '2026-01-15'})
        assert resp.status_code == 200 and resp.data['duration_days'] == 14

    @pytest.mark.parametrize('data', [
        {'name': ''}, {'name': '   '}, {'status': 'nimp'}, {'end_date': '2000-01-01'},
        {'start_date': 'pas-une-date'}, {'duration_days': 0}, {'duration_days': 9999},
    ])
    def test_invalid_values_are_rejected(self, env, data):
        assert self.patch(env, data).status_code == 400
        env.s1.refresh_from_db()
        assert env.s1.name == 'Sprint 1' and env.s1.status == 'active'

    def test_members_cannot_edit(self, env):
        assert self.patch(env, {'name': 'X'}, env.tester).status_code == 403

    def test_create_with_duration(self, env):
        resp = api(env.owner).post(f'{env.base}/sprints/', {'name': 'S3', 'start_date': '2026-02-01', 'duration_days': 10}, format='json')
        assert resp.status_code == 201 and resp.data['end_date'] == '2026-02-11'

    def test_create_needs_end_or_duration(self, env):
        assert api(env.owner).post(f'{env.base}/sprints/', {'name': 'S3', 'start_date': '2026-02-01'}, format='json').status_code == 400

    def test_deleting_sprints_sharing_assignments(self, env):
        for sprint in (env.s1, env.s2):
            assert api(env.owner).delete(f'{env.base}/sprints/{sprint.id}/').status_code == 204
        assert UseCaseAssignment.objects.filter(sprint__isnull=True).count() == 4


# ─────────────────────────────────────────────────────────────────────────────
class TestKanbanAssignment:
    def test_lists_every_team_member_except_viewers(self, env):
        resp = api(env.owner).get(f'{env.base}/assignable-members/')
        rows = {r['user']['username']: r for r in resp.data['results']}
        assert set(rows) == {'owner', 'admin', 'tester', 'member'}
        assert rows['tester']['in_project'] is True and rows['member']['in_project'] is False

    def test_member_created_by_owner_is_assignable(self, env):
        resp = api(env.owner).post('/api/teams/members/create/', {
            'username': 'nouveau', 'password': 'motdepasse1', 'role': 'member', 'team_slugs': [env.team.slug]}, format='json')
        assert resp.status_code == 201
        names = [r['user']['username'] for r in api(env.owner).get(f'{env.base}/assignable-members/').data['results']]
        assert 'nouveau' in names

    def test_assigning_team_member_adds_him_to_project(self, env):
        case = ExtractedUseCase.objects.create(project=env.project, use_case_text='Hors sprint')
        resp = api(env.owner).post(f'{env.base}/assignments/', {
            'use_case_ids': [str(case.id)], 'sprint_id': str(env.s1.id), 'user_id': env.member.id}, format='json')
        assert resp.status_code == 201 and resp.data['count'] == 1
        assert ProjectMember.objects.get(project=env.project, user=env.member).role == 'tester'

    @pytest.mark.parametrize('who', ['viewer', 'outsider'])
    def test_viewer_and_outsider_are_refused(self, env, who):
        resp = api(env.owner).post(f'{env.base}/assignments/', {
            'use_case_ids': [str(env.cases[3].id)], 'sprint_id': str(env.s1.id), 'user_id': getattr(env, who).id}, format='json')
        assert resp.status_code == 400

    def test_reassign_and_unassign_from_card(self, env):
        assignment = env.s1.assignments.first()
        url = f'{env.base}/assignments/{assignment.id}/'
        resp = api(env.owner).patch(url, {'assigned_user_id': env.member.id}, format='json')
        assert resp.status_code == 200 and resp.data['assigned_to_user']['username'] == 'member'
        assert ActivityLog.objects.filter(action_type='assignments_created', target_users=env.member).exists()
        resp = api(env.owner).patch(url, {'assigned_user_id': None}, format='json')
        assert resp.data['assigned_to_user'] is None
        assert api(env.owner).patch(url, {'assigned_user_id': env.outsider.id}, format='json').status_code == 400

    def test_create_case_assigned_to_team_member(self, env):
        resp = api(env.owner).post(f'{env.base}/sprints/{env.s1.id}/cases/', {
            'use_case_text': 'Nouveau', 'assigned_user_id': env.member.id, 'status': 'Passé'}, format='json')
        assert resp.status_code == 201 and resp.data['assigned_to_user']['username'] == 'member'
        assert UseCaseExecution.objects.get(use_case_id=resp.data['use_case']).sprint == env.s1

    def test_unassigned_case_is_completed_not_duplicated(self, env):
        assignment = env.s1.assignments.first()
        UseCaseAssignment.objects.filter(pk=assignment.pk).update(assigned_to=None)
        unassigned = api(env.owner).get(f'{env.base}/unassigned-ucs/?sprint={env.s1.id}').data['results']
        assert str(assignment.use_case_id) in [str(u['id']) for u in unassigned]
        api(env.owner).post(f'{env.base}/assignments/', {
            'use_case_ids': [str(assignment.use_case_id)], 'sprint_id': str(env.s1.id), 'user_id': env.member.id}, format='json')
        assert env.s1.assignments.filter(use_case=assignment.use_case).count() == 1

    def test_assign_to_finished_sprint_is_refused(self, env):
        env.s1.status = 'completed'
        env.s1.save()
        resp = api(env.owner).post(f'{env.base}/assignments/', {
            'use_case_ids': [str(env.cases[3].id)], 'sprint_id': str(env.s1.id), 'user_id': env.tester.id}, format='json')
        assert resp.status_code == 400

    def test_removing_member_keeps_cases(self, env):
        assert api(env.owner).delete(f'/api/teams/{env.team.slug}/members/{env.tester.id}/').status_code == 204
        assert not ProjectMember.objects.filter(project=env.project, user=env.tester).exists()
        assert env.s1.assignments.count() == 2 and env.s1.assignments.filter(assigned_to__isnull=True).count() == 2

    def test_removing_member_with_shared_case_leaves_no_duplicate(self, env):
        other = ProjectMember.objects.create(project=env.project, user=env.member)
        case = env.s1.assignments.first().use_case
        UseCaseAssignment.objects.create(use_case=case, sprint=env.s1, assigned_to=other)
        env.pm.delete()
        assert env.s1.assignments.get(use_case=case).assigned_to == other

    def test_case_with_two_assignees_is_reachable_by_case_id(self, env):
        other = ProjectMember.objects.create(project=env.project, user=env.member)
        UseCaseAssignment.objects.create(use_case=env.cases[0], sprint=env.s1, assigned_to=other)
        resp = api(env.owner).get(f'{env.base}/sprints/{env.s1.id}/cases/{env.cases[0].id}/comments/')
        assert resp.status_code == 200

    def test_bad_filters_return_400(self, env):
        client = api(env.owner)
        for query in ('assignments/?sprint=x', 'assignments/?user_id=x', 'unassigned-ucs/?sprint=x',
                      'defects/?sprint=x', 'defects/?assigned_to=x', 'report/?sprints=x'):
            assert client.get(f'{env.base}/{query}').status_code == 400, query


# ─────────────────────────────────────────────────────────────────────────────
class TestDefects:
    def create(self, env, user=None, **data):
        return api(user or env.tester).post(f'{env.base}/defects/', {'title': 'Bug', **data}, format='json')

    def test_link_to_cases_of_a_sprint(self, env):
        resp = self.create(env, sprint=str(env.s2.id), use_case=str(env.cases[1].id),
                           related_use_cases=[str(env.cases[1].id), str(env.cases[2].id)])
        assert resp.status_code == 201
        assert resp.data['sprint_name'] == 'Sprint 2'
        assert resp.data['use_case_detail']['case_number'] == 2
        assert [c['case_number'] for c in resp.data['related_cases']] == [3]

    def test_case_outside_sprint_is_refused(self, env):
        resp = self.create(env, sprint=str(env.s1.id), use_case=str(env.cases[3].id))
        assert resp.status_code == 400 and 'CAS-004' in str(resp.data)
        resp = self.create(env, sprint=str(env.s1.id), related_use_cases=[str(env.cases[3].id)])
        assert resp.status_code == 400

    def test_foreign_objects_are_refused(self, env):
        other = Project.objects.create(team=env.team, name='Autre')
        case = ExtractedUseCase.objects.create(project=other)
        sprint = Sprint.objects.create(project=other, name='X', start_date=date(2026, 1, 1), end_date=date(2026, 1, 9))
        defect = Defect.objects.create(project=other, title='Ailleurs')
        for data in ({'use_case': str(case.id)}, {'sprint': str(sprint.id)}, {'regression_of': str(defect.id)},
                     {'related_use_cases': [str(case.id)]}, {'assigned_to': env.outsider.id},
                     {'severity': 'nimp'}, {'title': '  '}):
            assert self.create(env, **data).status_code == 400, data

    def test_empty_strings_mean_no_link(self, env):
        resp = self.create(env, sprint='', use_case='', assigned_to='')
        assert resp.status_code == 201 and resp.data['sprint'] is None

    def test_multipart_creation_with_attachment(self, env):
        from django.core.files.uploadedfile import SimpleUploadedFile
        resp = api(env.tester).post(f'{env.base}/defects/', {
            'title': 'Avec pièce', 'related_use_cases': [str(env.cases[0].id), str(env.cases[1].id)],
            'attachment': SimpleUploadedFile('log.txt', b'trace', content_type='text/plain')}, format='multipart')
        assert resp.status_code == 201 and resp.data['attachment']
        assert len(resp.data['related_cases']) == 2 and resp.data['is_regression'] is False
        bad = SimpleUploadedFile('x.exe', b'x', content_type='application/x-msdownload')
        assert api(env.tester).post(f'{env.base}/defects/', {'title': 'X', 'attachment': bad}, format='multipart').status_code == 400

    def test_lifecycle_dates_reopen_and_history(self, env):
        defect_id = self.create(env).data['id']
        url = f'{env.base}/defects/{defect_id}/'
        client = api(env.tester)
        data = client.patch(url, {'status': 'resolved', 'resolution': 'Corrigé'}, format='json').data
        assert data['resolved_at'] and not data['closed_at']
        data = client.patch(url, {'status': 'closed'}, format='json').data
        assert data['closed_at']
        data = client.patch(url, {'status': 'reopened', 'severity': 'critical'}, format='json').data
        assert data['reopen_count'] == 1 and data['resolved_at'] is None and data['closed_at'] is None
        history = client.get(f'{url}history/').data['results']
        assert {h['field'] for h in history} == {'Statut', 'Sévérité', 'Résolution'}
        assert ('Fermé', 'Réouvert') in {(h['old_value'], h['new_value']) for h in history}

    def test_update_links(self, env):
        url = f'{env.base}/defects/{self.create(env).data["id"]}/'
        resp = api(env.tester).patch(url, {'sprint': str(env.s1.id), 'use_case': str(env.cases[0].id),
                                           'related_use_cases': [str(env.cases[1].id)]}, format='json')
        assert resp.status_code == 200 and resp.data['related_cases'][0]['case_number'] == 2
        assert DefectHistory.objects.filter(field='Autres cas impactés', new_value='CAS-002').exists()
        # Changer de sprint sans revoir les cas liés serait incohérent.
        assert api(env.tester).patch(url, {'sprint': str(env.s2.id)}, format='json').status_code == 400
        assert api(env.tester).patch(url, {'status': 'nimp'}, format='json').status_code == 400
        assert api(env.tester).patch(url, {'regression_of': url.split('/')[-2]}, format='json').status_code == 400

    def test_comments(self, env):
        url = f'{env.base}/defects/{self.create(env, user=env.admin).data["id"]}/comments/'
        assert api(env.tester).post(url, {'content': 'Reproduit'}, format='json').status_code == 201
        assert api(env.tester).post(url, {'content': ''}, format='json').status_code == 400
        assert api(env.viewer).post(url, {'content': 'non'}, format='json').status_code == 403
        assert api(env.viewer).get(url).data['count'] == 1
        assert ActivityLog.objects.filter(action_type='defect_comment', target_users=env.admin).exists()

    def test_viewer_is_read_only(self, env):
        defect_id = self.create(env).data['id']
        assert self.create(env, user=env.viewer).status_code == 403
        assert api(env.viewer).patch(f'{env.base}/defects/{defect_id}/', {'status': 'closed'}, format='json').status_code == 403
        assert api(env.viewer).get(f'{env.base}/defects/').data['count'] == 1
        assert api(env.tester).delete(f'{env.base}/defects/{defect_id}/').status_code == 403
        assert api(env.owner).delete(f'{env.base}/defects/{defect_id}/').status_code == 204

    def test_archived_project_is_read_only(self, env):
        env.project.status = 'archived'
        env.project.save()
        assert self.create(env).status_code == 403

    def test_filters_and_pagination(self, env):
        for i in range(22):
            Defect.objects.create(project=env.project, title=f'Bug {i}', sprint=env.s1 if i < 3 else None,
                                  is_regression=i == 0)
        defect = Defect.objects.create(project=env.project, title='Lié')
        defect.related_use_cases.add(env.cases[0])
        client = api(env.owner)
        get = lambda q: client.get(f'{env.base}/defects/?{q}').data  # noqa: E731
        assert get('')['count'] == 23 and len(get('')['results']) == 23
        assert get('page=2')['count'] == 23 and len(get('page=2')['results']) == 3
        assert get(f'sprint={env.s1.id}')['count'] == 3
        assert get('sprint=none')['count'] == 20
        assert get('regression=true')['count'] == 1
        assert get(f'use_case={env.cases[0].id}')['results'][0]['title'] == 'Lié'
        assert get('search=lié')['count'] + get('search=Li')['count'] >= 1

    def test_stats(self, env):
        Defect.objects.create(project=env.project, title='a', sprint=env.s1, severity='critical')
        Defect.objects.create(project=env.project, title='b', status='reopened', is_regression=True)
        data = api(env.owner).get(f'{env.base}/defects/stats/').data
        assert (data['total'], data['open'], data['reopened'], data['active'], data['regressions']) == (2, 1, 1, 2, 1)
        assert api(env.owner).get(f'{env.base}/defects/stats/?sprints={env.s1.id}').data['total'] == 1

    def test_board_exposes_linked_defects_of_the_sprint(self, env):
        Defect.objects.create(project=env.project, title='S1', sprint=env.s1, use_case=env.cases[1])
        Defect.objects.create(project=env.project, title='S2', sprint=env.s2, use_case=env.cases[1], status='closed')
        board = api(env.owner).get(f'{env.base}/sprints/{env.s1.id}/board/').data
        card = next(a for a in board['columns']['À tester'] if a['use_case'] == env.cases[1].id)
        assert [d['title'] for d in card['defects']] == ['S1'] and card['open_defects_count'] == 1


class TestRegressions:
    def test_list_with_linked_defects(self, env):
        case = env.cases[0]
        for status in ('Passé', 'Échoué', 'Passé', 'Échoué'):
            set_status(case, status, sprint=env.s1, actor=env.tester)
        resp = api(env.tester).post(f'{env.base}/defects/', {
            'title': 'Réapparue', 'use_case': str(case.id), 'sprint': str(env.s1.id)}, format='json')
        assert resp.data['is_regression'] is True  # détection automatique
        data = api(env.viewer).get(f'{env.base}/regressions/').data
        assert data['count'] == 1 and data['active'] == 1
        row = data['results'][0]
        assert row['occurrences'] == 2 and row['sprint_name'] == 'Sprint 1' and row['detected_by'] == 'tester'
        assert row['defects'][0]['title'] == 'Réapparue'
        assert api(env.viewer).get(f'{env.base}/regressions/?sprints={env.s2.id}').data['count'] == 0

    def test_explicit_flag_wins(self, env):
        for status in ('Passé', 'Échoué'):
            set_status(env.cases[0], status)
        resp = api(env.tester).post(f'{env.base}/defects/', {
            'title': 'x', 'use_case': str(env.cases[0].id), 'is_regression': False}, format='json')
        assert resp.data['is_regression'] is False

    def test_regression_of_marks_regression(self, env):
        origin = Defect.objects.create(project=env.project, title='Origine')
        resp = api(env.tester).post(f'{env.base}/defects/', {'title': 'x', 'regression_of': str(origin.id)}, format='json')
        assert resp.data['is_regression'] is True and resp.data['regression_of_ticket'] == origin.ticket_number

    def test_resolved_regression_is_inactive(self, env):
        for status in ('Passé', 'Échoué', 'Passé'):
            set_status(env.cases[0], status)
        data = api(env.owner).get(f'{env.base}/regressions/').data
        assert data['count'] == 1 and data['active'] == 0

    def test_card_flag(self, env):
        for status in ('Passé', 'Échoué'):
            set_status(env.cases[0], status)
        board = api(env.owner).get(f'{env.base}/sprints/{env.s1.id}/board/').data
        assert board['columns']['Échoué'][0]['is_regression'] is True


# ─────────────────────────────────────────────────────────────────────────────
class TestPerformance:
    def test_not_evaluated_without_execution(self, env):
        perf = analytics.project_performance(env.project)
        assert perf['score'] is None and perf['level'] == 'not_evaluated'

    def test_perfect_project(self, env):
        for case in env.cases:
            set_status(case, 'Passé')
        perf = analytics.project_performance(env.project)
        assert perf['score'] == 100 and perf['level'] == 'excellent'

    def test_weighted_score(self, env):
        set_status(env.cases[0], 'Passé')
        set_status(env.cases[1], 'Échoué')
        Defect.objects.create(project=env.project, title='a')
        perf = analytics.project_performance(env.project)
        # 40 % × 50 + 25 % × 50 + 20 % × 0 + 15 % × 100
        assert perf['score'] == 47.5 and perf['level'] == 'critical'
        assert perf['defect_density'] == 0.5

    def test_weights_sum_to_100(self):
        assert sum(analytics.SCORE_WEIGHTS.values()) == 100

    def test_scoped_to_sprints(self, env):
        set_status(env.cases[0], 'Passé', sprint=env.s1)
        data = api(env.owner).get(f'{env.base}/performance/?sprints={env.s1.id}').data
        assert data['stats']['total'] == 2 and data['stats']['passed'] == 1 and len(data['sprints']) == 1
        data = api(env.owner).get(f'{env.base}/performance/?sprints={env.s1.id},{env.s2.id}').data
        assert data['stats']['total'] == 4 and len(data['sprints']) == 2

    def test_team_overview_hides_private_projects(self, env):
        Project.objects.create(team=env.team, name='Secret', visibility='private')
        set_status(env.cases[0], 'Passé')
        owner_names = [r['project']['name'] for r in api(env.owner).get(f'/api/teams/{env.team.slug}/performance/').data['results']]
        assert owner_names == ['Portail', 'Secret']  # projets évalués d'abord
        assert api(env.member).get(f'/api/teams/{env.team.slug}/performance/').data['count'] == 1
        assert api(env.outsider).get(f'/api/teams/{env.team.slug}/performance/').status_code == 403

    def test_empty_project(self, env):
        empty = Project.objects.create(team=env.team, name='Vide')
        base = f'/api/teams/{env.team.slug}/projects/{empty.slug}'
        for path in ('performance', 'evolution', 'report', 'report/pdf', 'regressions'):
            assert api(env.owner).get(f'{base}/{path}/').status_code == 200, path


class TestEvolution:
    def history(self, env, case, *events):
        """events : (jours écoulés, ancien, nouveau)."""
        now = timezone.now()
        ExtractedUseCase.objects.filter(pk=case.pk).update(created_at=now - timedelta(days=10), status=events[-1][2])
        for days, old, new in events:
            UseCaseExecution.objects.create(use_case=case, project=env.project, old_status=old, new_status=new,
                                            executed_at=now - timedelta(days=days))

    def test_daily_replay(self, env):
        ExtractedUseCase.objects.exclude(pk=env.cases[0].pk).delete()
        self.history(env, env.cases[0], (5, 'À tester', 'Échoué'), (2, 'Échoué', 'Passé'))
        data = analytics.project_evolution(env.project)
        points = data['points']
        assert data['granularity'] == 'day' and len(points) == 11
        assert [p['success_rate'] for p in points] == [0.0] * 8 + [100.0] * 3
        assert points[4]['not_run'] == 1 and points[6]['failed'] == 1
        assert data['trend'] == 100.0

    def test_last_point_matches_current_state(self, env):
        set_status(env.cases[0], 'Passé')
        last = analytics.project_evolution(env.project)['points'][-1]
        assert last['date'] == timezone.localdate().isoformat()
        assert last['total'] == 4 and last['passed'] == 1

    def test_weekly_granularity_on_long_periods(self, env):
        ExtractedUseCase.objects.filter(pk=env.cases[0].pk).update(created_at=timezone.now() - timedelta(days=200))
        data = analytics.project_evolution(env.project)
        assert data['granularity'] == 'week' and len(data['points']) < 40
        assert data['points'][-1]['date'] == timezone.localdate().isoformat()

    def test_defects_curve(self, env):
        ExtractedUseCase.objects.filter(project=env.project).update(created_at=timezone.now() - timedelta(days=4))
        d = Defect.objects.create(project=env.project, title='a')
        Defect.objects.filter(pk=d.pk).update(created_at=timezone.now() - timedelta(days=3), status='resolved',
                                              resolved_at=timezone.now() - timedelta(days=1))
        points = analytics.project_evolution(env.project)['points']
        assert [p['defects_open'] for p in points] == [0, 1, 1, 0, 0]

    def test_sprint_scope(self, env):
        env.s1.status = 'completed'
        env.s1.save()
        data = analytics.project_evolution(env.project, [env.s1])
        assert data['start'] == env.s1.start_date.isoformat() and data['end'] == env.s1.end_date.isoformat()
        future = Sprint.objects.create(project=env.project, name='F', start_date=timezone.localdate() + timedelta(days=5),
                                       end_date=timezone.localdate() + timedelta(days=9))
        assert len(analytics.project_evolution(env.project, [future])['points']) >= 1


# ─────────────────────────────────────────────────────────────────────────────
class TestReports:
    def assert_branded(self, pages):
        for page in pages:
            text = ' '.join(page.extract_text().split())
            for line in COMPANY_FOOTER_LINES:
                assert ' '.join(line.split()) in text
            assert len(page.images) == 1  # logo en en-tête
        assert f'Page {len(pages)} / {len(pages)}' in pages[-1].extract_text()

    def test_project_pdf(self, env):
        for status in ('Passé', 'Échoué'):
            set_status(env.cases[0], status, sprint=env.s1)
        Defect.objects.create(project=env.project, title='Erreur <500> & « accents » 🚀 →', use_case=env.cases[0], sprint=env.s1)
        pages = pdf_pages(api(env.viewer).get(f'{env.base}/report/pdf/'))
        self.assert_branded(pages)
        text = ''.join(p.extract_text() for p in pages)
        for expected in ('Rapport de projet', 'Niveau de performance', "Courbe d'évolution", 'Sprint 1', 'Sprint 2',
                         'Erreur <500> &', 'Régressions (1)', 'CAS-001'):
            assert expected in text, expected

    def test_logo_is_centered(self, env):
        page = pdf_pages(api(env.owner).get(f'{env.base}/report/pdf/'))[0]
        found = []

        def visit(op, args, cm, tm):
            if op == b'Do':
                found.append(cm)
        page.extract_text(visitor_operand_before=visit)
        width, x = found[0][0], found[0][4]
        assert abs((x + width / 2) - float(page.mediabox.width) / 2) < 0.5

    def test_single_sprint_pdf(self, env):
        for url in (f'{env.base}/sprints/{env.s1.id}/report/pdf/', f'{env.base}/report/pdf/?sprints={env.s1.id}'):
            resp = api(env.owner).get(url)
            pages = pdf_pages(resp)
            self.assert_branded(pages)
            text = ''.join(p.extract_text() for p in pages)
            assert 'Rapport de sprint' in text and 'Sprint 2' not in text
            assert 'sprint-1' in resp['Content-Disposition']

    def test_multiple_sprints_pdf(self, env):
        resp = api(env.owner).get(f'{env.base}/report/pdf/?sprints={env.s1.id}&sprints={env.s2.id}')
        text = ''.join(p.extract_text() for p in pdf_pages(resp))
        assert 'Rapport multi-sprints' in text and '2 sprints sélectionnés' in text

    def test_json_report(self, env):
        data = api(env.owner).get(f'{env.base}/report/?sprints={env.s2.id}').data
        assert data['scope']['type'] == 'sprint' and len(data['cases']) == 3
        assert data['members'][0]['member'] == 'tester' and data['members'][0]['total'] == 3

    def test_foreign_sprint_is_refused(self, env):
        other = Project.objects.create(team=env.team, name='Autre')
        sprint = Sprint.objects.create(project=other, name='X', start_date=date(2026, 1, 1), end_date=date(2026, 1, 9))
        assert api(env.owner).get(f'{env.base}/report/pdf/?sprints={sprint.id}').status_code == 400
        assert api(env.owner).get(f'{env.base}/sprints/{sprint.id}/report/pdf/').status_code == 404

    def test_access_control(self, env):
        assert api(env.outsider).get(f'{env.base}/report/pdf/').status_code == 403
        assert APIClient().get(f'{env.base}/report/pdf/').status_code in (401, 403)

    @pytest.mark.parametrize('kind', ['daily-scrum', 'weekly'])
    def test_team_reports_are_branded_and_filtered(self, env, kind):
        Project.objects.create(team=env.team, name='TopSecret', visibility='private')
        url = f'/api/teams/{env.team.slug}/reports/{kind}/'
        pages = pdf_pages(api(env.member).get(url))
        self.assert_branded(pages)
        text = ''.join(p.extract_text() for p in pages)
        assert 'Portail' in text and 'Cas é1' in text and 'owner@test.com' in text
        assert 'TopSecret' not in text
        assert 'TopSecret' in ''.join(p.extract_text() for p in pdf_pages(api(env.owner).get(url)))

    def test_large_report(self, env):
        for i in range(120):
            ExtractedUseCase.objects.create(project=env.project, order=i, use_case_text='Long titre ' * 30)
        pages = pdf_pages(api(env.owner).get(f'{env.base}/report/pdf/'))
        assert len(pages) > 4
        self.assert_branded(pages)


# ─────────────────────────────────────────────────────────────────────────────
class TestBugFixes:
    def test_private_project_returns_403_not_500(self, env):
        private = Project.objects.create(team=env.team, name='Prive', visibility='private')
        assert api(env.member).get(f'/api/teams/{env.team.slug}/projects/{private.slug}/sprints/').status_code == 403

    def test_notifications_are_isolated_between_teams(self, env):
        other = Team.objects.create(name='Autre', owner=env.outsider)
        TeamMember.objects.create(team=other, user=env.outsider, role='owner')
        log = ActivityLog.objects.create(team=other, actor=env.outsider, action_type='x', description='secret')
        assert api(env.tester).get('/api/notifications/').data['results'] == []
        assert api(env.tester).patch(f'/api/notifications/{log.id}/read/').status_code == 404

    def test_private_project_activity_is_hidden(self, env):
        private = Project.objects.create(team=env.team, name='Prive', visibility='private')
        ActivityLog.objects.create(team=env.team, project=private, actor=env.owner, action_type='x', description='secret')
        assert api(env.member).get('/api/notifications/').data['unread_count'] == 0
        assert api(env.admin).get('/api/notifications/').data['unread_count'] == 1

    def test_read_state_is_per_user(self, env):
        ActivityLog.objects.create(team=env.team, actor=env.owner, action_type='x', description='y')
        api(env.tester).post('/api/notifications/mark-all-read/')
        assert api(env.tester).get('/api/notifications/').data['unread_count'] == 0
        assert api(env.member).get('/api/notifications/').data['unread_count'] == 1
        assert api(env.owner).get('/api/notifications/').data['unread_count'] == 0  # sa propre action

    def test_weekly_reports_of_others_are_private(self, env):
        url = f'/api/teams/{env.team.slug}/weekly-reports/'
        assert api(env.tester).get(f'{url}?user_id={env.member.id}').status_code == 403
        assert api(env.admin).get(f'{url}?user_id={env.member.id}').status_code == 200
        assert api(env.tester).post(url, {'week_start': 'nimp'}, format='json').status_code == 400

    def test_weekly_report_submitted_on_creation(self, env):
        resp = api(env.tester).post(f'/api/teams/{env.team.slug}/weekly-reports/', {'status': 'submitted'}, format='json')
        assert resp.status_code == 201 and resp.data['submitted_at']
        report = WeeklyReport.objects.get()
        url = f'/api/teams/{env.team.slug}/weekly-reports/{report.id}/'
        assert api(env.admin).patch(url, {'blockers': 'x'}, format='json').status_code == 403

    def test_owner_role_cannot_be_granted(self, env):
        url = f'/api/teams/{env.team.slug}/members/'
        assert api(env.admin).patch(f'{url}{env.member.id}/', {'role': 'owner'}, format='json').status_code == 400
        assert api(env.admin).post(url, {'email': 'a@b.com', 'role': 'owner'}, format='json').status_code == 400

    def test_invitation_cannot_be_rejected_by_someone_else(self, env):
        inv = TeamInvitation.objects.create(team=env.team, invited_by=env.owner, email='cible@test.com')
        assert api(env.outsider).post(f'/api/teams/invitations/{inv.token}/reject/').status_code == 403

    def test_project_update_is_validated_and_archive_reversible(self, env):
        client = api(env.owner)
        assert client.patch(f'{env.base}/', {'status': 'nimp'}, format='json').status_code == 400
        assert client.patch(f'{env.base}/', {'status': 'archived'}, format='json').status_code == 200
        assert client.patch(f'{env.base}/', {'name': 'X'}, format='json').status_code == 400
        assert client.patch(f'{env.base}/', {'status': 'active'}, format='json').status_code == 200
        assert client.patch(f'/api/teams/{env.team.slug}/', {'name': ''}, format='json').status_code == 400

    def test_project_exposes_effective_rights(self, env):
        rights = {u.username: api(u).get(f'{env.base}/').data for u in (env.owner, env.tester, env.viewer)}
        assert [rights[n]['can_manage'] for n in ('owner', 'tester', 'viewer')] == [True, False, False]
        assert [rights[n]['can_write'] for n in ('owner', 'tester', 'viewer')] == [True, True, False]

    def test_recipes_follow_project_rights(self, env):
        job = ConversionJob.objects.create(source_filename='a.docx', word_file='a.docx', status='DONE', project=env.project)
        case = ExtractedUseCase.objects.create(job=job, order=1)
        for client in (APIClient(), api(env.outsider)):
            assert client.get(f'/api/jobs/{job.id}/').status_code == 403
            assert client.get(f'/api/jobs/{job.id}/csv/').status_code == 403
            assert client.patch(f'/api/jobs/{job.id}/use-cases/{case.id}/', {'status': 'Passé'}, format='json').status_code == 403
        assert api(env.viewer).get(f'/api/jobs/{job.id}/').status_code == 200
        assert api(env.viewer).patch(f'/api/jobs/{job.id}/use-cases/{case.id}/', {'status': 'Passé'}, format='json').status_code == 403
        assert api(env.tester).patch(f'/api/jobs/{job.id}/use-cases/{case.id}/', {'status': 'Passé'}, format='json').status_code == 200

    def test_jobs_can_be_listed_by_project(self, env):
        ConversionJob.objects.create(source_filename='a.docx', word_file='a.docx', project=env.project, uploaded_by=env.owner)
        ConversionJob.objects.create(source_filename='b.docx', word_file='b.docx', uploaded_by=env.tester)
        assert api(env.tester).get(f'/api/jobs/?project={env.project.id}').data['results'][0]['source_filename'] == 'a.docx'
        assert api(env.outsider).get(f'/api/jobs/?project={env.project.id}').status_code == 403
        assert api(env.tester).get('/api/jobs/?project=nimp').status_code == 404

    def test_open_redirect_is_blocked(self, env, client):
        resp = client.post('/teams/login/?next=https://evil.example/', {'username': 'owner', 'password': 'pass1234'})
        assert resp.url == '/teams/'

    def test_screenshot_must_be_an_image(self, env):
        from django.core.files.uploadedfile import SimpleUploadedFile
        assignment = env.s1.assignments.first()
        resp = api(env.tester).post(f'{env.base}/assignments/{assignment.id}/screenshots/',
                                    {'image': SimpleUploadedFile('x.png', b'<script>')}, format='multipart')
        assert resp.status_code == 400

    def test_preview_and_result_pages_render(self, client):
        job = ConversionJob.objects.create(source_filename='a.docx', word_file='a.docx', status='DONE')
        ExtractedUseCase.objects.create(job=job, order=1, use_case_text='Cas')
        assert client.get(f'/preview/{job.id}/').status_code == 200
        assert client.get(f'/result/{job.id}/').status_code == 200

    def test_api_schema_is_generated(self, client):
        assert client.get('/api/schema/').status_code == 200


class TestBackfillMigration:
    def test_history_is_rebuilt_from_activity_log(self, env):
        from importlib import import_module
        from django.apps import apps
        case = env.cases[0]
        ExtractedUseCase.objects.filter(pk=case.pk).update(status='Échoué', created_at=None)
        ExtractedUseCase.objects.filter(pk=env.cases[1].pk).update(status='Bloqué')
        for i, (old, new) in enumerate([('À tester', 'Passé'), ('Passé', 'Échoué')]):
            log = ActivityLog.objects.create(project=env.project, team=env.team, actor=env.tester,
                                             action_type='use_case_status', description='x',
                                             metadata={'uc_id': str(case.id), 'old_status': old, 'new_status': new})
            ActivityLog.objects.filter(pk=log.pk).update(created_at=timezone.now() - timedelta(days=5 - i))
        read = ActivityLog.objects.create(team=env.team, actor=env.owner, action_type='x', description='x', is_read=True)
        read.target_users.add(env.member)
        defect = Defect.objects.create(project=env.project, title='a', status='closed')
        import_module('apps.qamanagement.migrations.0009_backfill_history').backfill(apps, None)

        assert list(case.executions.values_list('new_status', 'is_regression')) == [('Passé', False), ('Échoué', True)]
        assert env.cases[1].executions.get().new_status == 'Bloqué'
        case.refresh_from_db()
        defect.refresh_from_db()
        assert case.created_at and defect.resolved_at and defect.closed_at
        assert env.s1.assignments.get(use_case=case).status == 'Échoué'
        assert env.member in read.read_by.all()
