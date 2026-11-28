"""
Non-régression des constats utilisateurs :

1. Le Kanban d'un sprint n'affiche que les cas de ce sprint.
2. L'assignation ne propose (ni n'accepte) les cas déjà planifiés dans un autre sprint.
3. L'import Word conserve les numéros de la colonne « Tickets Jira ».
"""
import io
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from docx import Document
from rest_framework.test import APIClient

from apps.core.models import ConversionJob, ExtractedUseCase
from apps.core.use_cases import clean_jira_ticket
from apps.parser.docx_parser import DocxParser, detect_column
from apps.qamanagement.campaigns import carry_over
from apps.qamanagement.models import Sprint, UseCaseAssignment
from apps.teams.models import Project, ProjectMember, Team, TeamMember

DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
pytestmark = pytest.mark.django_db


def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def env():
    class Env:
        pass
    e = Env()
    e.owner = User.objects.create_user('owner', 'owner@test.com', 'pass1234')
    e.tester = User.objects.create_user('tester', 'tester@test.com', 'pass1234')
    e.other = User.objects.create_user('other', 'other@test.com', 'pass1234')
    e.team = Team.objects.create(name='Equipe', owner=e.owner)
    for user, role in ((e.owner, 'owner'), (e.tester, 'member'), (e.other, 'member')):
        TeamMember.objects.create(team=e.team, user=user, role=role)
    e.project = Project.objects.create(team=e.team, name='Portail', created_by=e.owner)
    e.pm = ProjectMember.objects.create(project=e.project, user=e.tester, role='tester')
    e.cases = [ExtractedUseCase.objects.create(project=e.project, order=i, use_case_text=f'Cas {i}')
               for i in range(1, 6)]
    today = timezone.localdate()
    e.s1 = Sprint.objects.create(project=e.project, name='Sprint 1', start_date=today - timedelta(days=20),
                                 end_date=today - timedelta(days=6), status='completed', created_by=e.owner)
    e.s2 = Sprint.objects.create(project=e.project, name='Sprint 2', start_date=today - timedelta(days=5),
                                 end_date=today + timedelta(days=9), status='active', created_by=e.owner)
    # Sprint 1 : cas 1-2 (déjà joués) · Sprint 2 : cas 3 · cas 4-5 : jamais planifiés.
    for case in e.cases[:2]:
        UseCaseAssignment.objects.create(use_case=case, sprint=e.s1, assigned_to=e.pm, assigned_by=e.owner)
    UseCaseAssignment.objects.create(use_case=e.cases[2], sprint=e.s2, assigned_to=e.pm, assigned_by=e.owner)
    e.base = f'/api/teams/{e.team.slug}/projects/{e.project.slug}'
    return e


def board_case_ids(env, sprint):
    resp = api(env.owner).get(f'{env.base}/sprints/{sprint.id}/board/')
    assert resp.status_code == 200
    return {str(a['use_case']) for column in resp.data['columns'].values() for a in column}


def unassigned(env, sprint):
    resp = api(env.owner).get(f'{env.base}/unassigned-ucs/', {'sprint': str(sprint.id)})
    assert resp.status_code == 200
    return resp


def assign(env, sprint, cases, user=None):
    return api(env.owner).post(f'{env.base}/assignments/', {
        'use_case_ids': [str(c.id) for c in cases], 'sprint_id': str(sprint.id),
        'user_id': (user or env.other).id}, format='json')


# ─────────────────────────────────────────────────────────────────────────────
# 1 & 2 — Séparation des cas par sprint
# ─────────────────────────────────────────────────────────────────────────────
class TestSprintSeparation:
    def test_board_only_shows_its_own_cases(self, env):
        assert board_case_ids(env, env.s1) == {str(c.id) for c in env.cases[:2]}
        assert board_case_ids(env, env.s2) == {str(env.cases[2].id)}

    def test_assignment_list_hides_cases_of_other_sprints(self, env):
        resp = unassigned(env, env.s2)
        ids = [r['id'] for r in resp.data['results']]
        # Cas 1-2 (Sprint 1) masqués, cas 3 déjà assigné ici : seuls les cas jamais planifiés restent.
        assert ids == [str(env.cases[3].id), str(env.cases[4].id)]
        assert resp.data['hidden_other_sprints'] == 2

    def test_new_sprint_does_not_offer_played_cases(self, env):
        s3 = Sprint.objects.create(project=env.project, name='Sprint 3', start_date=env.s2.end_date,
                                   end_date=env.s2.end_date + timedelta(days=7), created_by=env.owner)
        resp = unassigned(env, s3)
        assert {r['id'] for r in resp.data['results']} == {str(env.cases[3].id), str(env.cases[4].id)}
        assert resp.data['hidden_other_sprints'] == 3

    def test_list_is_sorted_by_case_number(self, env):
        ExtractedUseCase.objects.filter(pk=env.cases[4].pk).update(order=-1)
        numbers = [r['case_number'] for r in unassigned(env, env.s2).data['results']]
        assert numbers == sorted(numbers)

    def test_unassigned_case_waiting_in_sprint_stays_assignable(self, env):
        UseCaseAssignment.objects.create(use_case=env.cases[3], sprint=env.s2, assigned_by=env.owner)
        ids = {r['id'] for r in unassigned(env, env.s2).data['results']}
        assert str(env.cases[3].id) in ids
        resp = assign(env, env.s2, [env.cases[3]])
        assert resp.status_code == 201 and resp.data['count'] == 1
        assert env.s2.assignments.filter(use_case=env.cases[3]).count() == 1

    def test_api_refuses_case_planned_in_another_sprint(self, env):
        resp = assign(env, env.s2, [env.cases[0]])
        assert resp.status_code == 400
        assert 'Sprint 1' in resp.data['error'] and 'Reporter' in resp.data['error']
        assert not env.s2.assignments.filter(use_case=env.cases[0]).exists()
        # Le Kanban du sprint 2 reste inchangé.
        assert board_case_ids(env, env.s2) == {str(env.cases[2].id)}

    def test_mixed_selection_assigns_free_cases_and_reports_the_others(self, env):
        resp = assign(env, env.s2, [env.cases[0], env.cases[3]])
        assert resp.status_code == 201
        assert resp.data['created'] == [str(env.cases[3].id)]
        assert len(resp.data['errors']) == 1 and 'CAS-001' in resp.data['errors'][0]
        assert board_case_ids(env, env.s2) == {str(env.cases[2].id), str(env.cases[3].id)}

    def test_case_of_this_sprint_can_get_a_second_assignee(self, env):
        resp = assign(env, env.s2, [env.cases[2]])
        assert resp.status_code == 201 and resp.data['count'] == 1
        assert env.s2.assignments.filter(use_case=env.cases[2]).count() == 2

    def test_carried_over_case_is_shared_explicitly(self, env):
        """Le report reste le moyen explicite de rejouer un cas d'un autre sprint."""
        carry_over(env.s1, env.s2, env.owner, include_failed=True)
        assert board_case_ids(env, env.s2) == {str(c.id) for c in env.cases[:3]}
        # Un cas reporté appartient désormais aussi au sprint 2 : on peut l'y confier à un autre membre.
        assert assign(env, env.s2, [env.cases[0]]).status_code == 201

    def test_assignment_without_sprint_still_lists_never_assigned_cases(self, env):
        resp = api(env.owner).get(f'{env.base}/unassigned-ucs/')
        assert {r['id'] for r in resp.data['results']} == {str(env.cases[3].id), str(env.cases[4].id)}

    def test_unknown_sprint_is_404(self, env):
        other = Project.objects.create(team=env.team, name='Autre', created_by=env.owner)
        foreign = Sprint.objects.create(project=other, name='X', start_date=env.s2.start_date,
                                        end_date=env.s2.end_date, created_by=env.owner)
        assert api(env.owner).get(f'{env.base}/unassigned-ucs/', {'sprint': str(foreign.id)}).status_code == 404
        assert api(env.owner).get(f'{env.base}/unassigned-ucs/', {'sprint': 'abc'}).status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 3 — Numéros Jira à l'import Word
# ─────────────────────────────────────────────────────────────────────────────
def make_docx(headers, rows):
    doc = Document()
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    for i, header in enumerate(headers):
        table.rows[0].cells[i].text = header
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row):
            table.rows[r].cells[c].text = value
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


HEADERS = ['Use Case', 'Tikets jira', 'Description', 'Étapes']
ROWS = [
    ['UC01', 'QA-101', 'Connexion', 'Ouvrir la page'],
    ['UC02', '', 'Déconnexion', 'Cliquer sur quitter'],
    ['UC03', 'QA-103\nQA-104', 'Profil', 'Ouvrir le profil'],
]


class TestJiraImport:
    @pytest.mark.parametrize('header', ['Tickets Jira', 'Tikets jira', 'Ticket Jira', 'JIRA', 'N° Ticket',
                                        'Numéro Jira', 'Ticket(s) Jira', 'Réf. Jira'])
    def test_header_variants_are_detected(self, header):
        assert detect_column(header) == 'jira_ticket'

    @pytest.mark.parametrize('header', ['', '   ', '\n'])
    def test_empty_header_is_ignored(self, header):
        assert detect_column(header) is None

    def test_empty_first_header_does_not_steal_use_case_column(self, tmp_path):
        path = tmp_path / 'r.docx'
        path.write_bytes(make_docx(['', 'Use Case', 'Tickets Jira'], [['1', 'UC01', 'QA-1']]))
        rows = DocxParser(str(path)).extract_use_cases()
        assert rows[0]['use_case_text'] == 'UC01' and rows[0]['jira_ticket'] == 'QA-1'

    def test_parser_reads_jira_without_filling_blank_cells(self, tmp_path):
        path = tmp_path / 'r.docx'
        path.write_bytes(make_docx(HEADERS, ROWS))
        rows = DocxParser(str(path)).extract_use_cases()
        assert [r['jira_ticket'] for r in rows] == ['QA-101', '', 'QA-103\nQA-104']

    def test_clean_jira_ticket(self):
        assert clean_jira_ticket('  QA-1 ') == 'QA-1'
        assert clean_jira_ticket('QA-1\nQA-2; QA-1') == 'QA-1, QA-2'
        assert clean_jira_ticket(None) == ''
        assert len(clean_jira_ticket('X' * 300)) == 100

    def test_api_upload_keeps_jira_tickets(self, env):
        resp = api(env.owner).post('/api/upload/', {
            'word_file': SimpleUploadedFile('recette.docx', make_docx(HEADERS, ROWS), content_type=DOCX),
            'project': str(env.project.id),
        }, format='multipart')
        assert resp.status_code == 201, resp.data
        assert [uc['jira_ticket'] for uc in resp.data['use_cases']] == ['QA-101', '', 'QA-103, QA-104']
        job = ConversionJob.objects.get(pk=resp.data['id'])
        assert list(job.use_cases.order_by('order').values_list('jira_ticket', flat=True)) == \
            ['QA-101', '', 'QA-103, QA-104']

    def test_imported_jira_tickets_reach_the_kanban(self, env):
        resp = api(env.owner).post('/api/upload/', {
            'word_file': SimpleUploadedFile('recette.docx', make_docx(HEADERS, ROWS), content_type=DOCX),
            'project': str(env.project.id),
        }, format='multipart')
        imported = ExtractedUseCase.objects.filter(job_id=resp.data['id']).order_by('order')
        assert assign(env, env.s2, list(imported)).status_code == 201
        board = api(env.owner).get(f'{env.base}/sprints/{env.s2.id}/board/').data
        tickets = {str(a['use_case']): a['jira_ticket'] for col in board['columns'].values() for a in col}
        assert tickets[str(imported[0].id)] == 'QA-101'

    def test_web_upload_keeps_jira_tickets(self, client):
        resp = client.post('/', {
            'word_file': SimpleUploadedFile('recette.docx', make_docx(HEADERS, ROWS), content_type=DOCX)})
        assert resp.status_code == 302
        job = ConversionJob.objects.get()
        assert list(job.use_cases.order_by('order').values_list('jira_ticket', flat=True)) == \
            ['QA-101', '', 'QA-103, QA-104']

    def test_batch_save_persists_jira_ticket(self, env):
        job = ConversionJob.objects.create(source_filename='r.docx', word_file='uploads/word/r.docx',
                                           status='DONE', project=env.project, uploaded_by=env.owner)
        case = ExtractedUseCase.objects.create(job=job, order=1, use_case_text='UC')
        resp = api(env.owner).post(f'/api/jobs/{job.id}/save/', {
            'use_cases': [{'id': str(case.id), 'jira_ticket': ' QA-9 '}]}, format='json')
        assert resp.status_code == 200, resp.data
        case.refresh_from_db()
        assert case.jira_ticket == 'QA-9'
        # Un enregistrement sans le champ ne l'efface pas.
        api(env.owner).post(f'/api/jobs/{job.id}/save/', {
            'use_cases': [{'id': str(case.id), 'description': 'maj'}]}, format='json')
        case.refresh_from_db()
        assert case.jira_ticket == 'QA-9' and case.description == 'maj'


# ─────────────────────────────────────────────────────────────────────────────
# Audit des données existantes
# ─────────────────────────────────────────────────────────────────────────────
class TestAuditSprintCases:
    def run(self, *args):
        from django.core.management import call_command
        out = io.StringIO()
        call_command('audit_sprint_cases', *args, stdout=out)
        return out.getvalue()

    def test_reports_and_fixes_wrongly_shared_cases(self, env):
        # Ancienne anomalie : le cas 1 (sprint 1) assigné aussi dans le sprint 2.
        UseCaseAssignment.objects.create(use_case=env.cases[0], sprint=env.s2, assigned_to=env.pm, assigned_by=env.owner)
        out = self.run()
        assert 'CAS-001' in out and '1 affectation(s) en double' in out
        assert env.s2.assignments.filter(use_case=env.cases[0]).exists()  # rapport seul
        out = self.run('--fix')
        assert '1 retirée(s)' in out
        assert not env.s2.assignments.filter(use_case=env.cases[0]).exists()
        assert env.s1.assignments.filter(use_case=env.cases[0]).exists()

    def test_carried_over_and_executed_cases_are_kept(self, env):
        carry_over(env.s1, env.s2, env.owner, include_failed=True)
        assert '0 affectation(s) en double' in self.run('--fix')
        UseCaseAssignment.objects.create(use_case=env.cases[3], sprint=env.s1, assigned_to=env.pm, assigned_by=env.owner)
        UseCaseAssignment.objects.create(use_case=env.cases[3], sprint=env.s2, assigned_to=env.pm, assigned_by=env.owner)
        case = env.cases[3]
        case._status_sprint = env.s2
        case.status = 'Passé'
        case.save()
        out = self.run('--fix')
        assert 'à vérifier manuellement' in out and '0 retirée(s)' in out
        assert env.s2.assignments.filter(use_case=case).exists()
