"""
Tests unitaires — Nouvelles fonctionnalités

Couvre :
- UseCaseBulkStatusAPIView (changement de statut en masse)
- ProjectProgressView (progression détaillée d'un projet)
- ProjectMemberAssignView (assignation multiple de membres)
- DailyScrumReportView / WeeklyReportView (génération PDF)
"""
import uuid
import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APIClient


def _weasyprint_available():
    try:
        import weasyprint  # noqa: F401
        return True
    except OSError:
        return False

from apps.core.models import ConversionJob, ExtractedUseCase
from apps.teams.models import Team, TeamMember, Project, ProjectMember


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def owner(db):
    return User.objects.create_user('owner_new', 'owner_new@test.com', 'pass1234')

@pytest.fixture
def admin_user(db):
    return User.objects.create_user('admin_new', 'admin_new@test.com', 'pass1234')

@pytest.fixture
def member_user(db):
    return User.objects.create_user('member_new', 'member_new@test.com', 'pass1234')

@pytest.fixture
def team(db, owner, admin_user, member_user):
    t = Team.objects.create(name='New Features Team', owner=owner)
    TeamMember.objects.create(team=t, user=owner, role='owner')
    TeamMember.objects.create(team=t, user=admin_user, role='admin')
    TeamMember.objects.create(team=t, user=member_user, role='member')
    return t

@pytest.fixture
def project(db, team, owner):
    p = Project.objects.create(team=team, name='Sprint 42', created_by=owner, visibility='team')
    ProjectMember.objects.create(project=p, user=owner, role='lead')
    return p

@pytest.fixture
def done_job_with_ucs(db, project, owner):
    job = ConversionJob.objects.create(
        source_filename='recette.docx',
        word_file='uploads/word/recette.docx',
        status=ConversionJob.Status.DONE,
        use_cases_count=4,
        project=project,
        uploaded_by=owner,
    )
    ucs = []
    for i in range(4):
        uc = ExtractedUseCase.objects.create(
            job=job, order=i + 1,
            use_case_text=f'TC{i+1:03d}',
            description=f'Test case {i+1}',
            status='À tester',
        )
        ucs.append(uc)
    return job, ucs

@pytest.fixture
def client():
    return APIClient()

def auth(client, user):
    client.force_authenticate(user=user)
    return client


# ─────────────────────────────────────────────────────────────────────────────
# BULK STATUS UPDATE
# ─────────────────────────────────────────────────────────────────────────────
class TestUseCaseBulkStatusAPI:
    def test_bulk_update_status(self, client, done_job_with_ucs):
        job, ucs = done_job_with_ucs
        uc_ids = [str(uc.id) for uc in ucs[:2]]

        resp = client.post(f'/api/jobs/{job.id}/bulk-status/', {
            'uc_ids': uc_ids,
            'status': 'Passé',
        }, format='json')

        assert resp.status_code == 200
        assert resp.data['updated'] == 2
        assert resp.data['status'] == 'Passé'

        ucs[0].refresh_from_db()
        ucs[1].refresh_from_db()
        assert ucs[0].status == 'Passé'
        assert ucs[1].status == 'Passé'
        assert ucs[2].status == 'À tester'

    def test_bulk_update_invalid_status(self, client, done_job_with_ucs):
        job, ucs = done_job_with_ucs
        uc_ids = [str(uc.id) for uc in ucs[:2]]

        resp = client.post(f'/api/jobs/{job.id}/bulk-status/', {
            'uc_ids': uc_ids,
            'status': 'INVALID',
        }, format='json')

        assert resp.status_code == 400

    def test_bulk_update_empty_uc_ids(self, client, done_job_with_ucs):
        job, _ = done_job_with_ucs

        resp = client.post(f'/api/jobs/{job.id}/bulk-status/', {
            'uc_ids': [],
            'status': 'Passé',
        }, format='json')

        assert resp.status_code == 200
        assert resp.data['updated'] == 0

    def test_bulk_update_job_not_found(self, client, db):
        resp = client.post(f'/api/jobs/{uuid.uuid4()}/bulk-status/', {
            'uc_ids': [],
            'status': 'Passé',
        }, format='json')

        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT PROGRESS
# ─────────────────────────────────────────────────────────────────────────────
class TestProjectProgressAPI:
    def test_project_progress_returns_all_data(self, client, owner, team, project, done_job_with_ucs):
        auth(client, owner)
        resp = client.get(f'/api/teams/{team.slug}/projects/{project.slug}/progress/')

        assert resp.status_code == 200
        assert 'project' in resp.data
        assert 'use_cases' in resp.data
        assert 'status_counts' in resp.data
        assert 'members' in resp.data

        counts = resp.data['status_counts']
        assert counts['total'] == 4
        assert counts['À tester'] == 4
        assert counts['Passé'] == 0
        assert 'automated' in counts

    def test_project_progress_outside_team_fails(self, client, team, project):
        stranger = User.objects.create_user('stranger', 's@t.com', 'pass1234')
        auth(client, stranger)
        resp = client.get(f'/api/teams/{team.slug}/projects/{project.slug}/progress/')
        assert resp.status_code == 403

    def test_project_progress_returns_sorted_ucs(self, client, owner, team, project, done_job_with_ucs):
        auth(client, owner)
        resp = client.get(f'/api/teams/{team.slug}/projects/{project.slug}/progress/')
        orders = [uc['order'] for uc in resp.data['use_cases']]
        assert orders == sorted(orders), 'UCs should be ordered by order field'


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT MEMBER ASSIGN
# ─────────────────────────────────────────────────────────────────────────────
class TestProjectMemberAssignAPI:
    def test_assign_multiple_members(self, client, admin_user, team, project, member_user):
        auth(client, admin_user)
        resp = client.post(
            f'/api/teams/{team.slug}/projects/{project.slug}/assign-members/',
            {'members': [{'user_id': member_user.id, 'role': 'tester'}]},
            format='json',
        )
        assert resp.status_code == 200
        assert resp.data['count'] == 1
        assert ProjectMember.objects.filter(project=project, user=member_user).exists()

    def test_assign_member_not_in_team_skipped(self, client, admin_user, team, project):
        stranger = User.objects.create_user('stranger2', 's2@t.com', 'pass1234')
        auth(client, admin_user)
        resp = client.post(
            f'/api/teams/{team.slug}/projects/{project.slug}/assign-members/',
            {'members': [{'user_id': stranger.id, 'role': 'tester'}]},
            format='json',
        )
        assert resp.status_code == 200
        assert resp.data['count'] == 0

    def test_assign_requires_admin(self, client, member_user, team, project):
        auth(client, member_user)
        resp = client.post(
            f'/api/teams/{team.slug}/projects/{project.slug}/assign-members/',
            {'members': []},
            format='json',
        )
        assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# PDF REPORTS
# ─────────────────────────────────────────────────────────────────────────────
class TestDailyScrumReportAPI:
    @pytest.mark.skipif(not _weasyprint_available(), reason='WeasyPrint requires GTK3 runtime on Windows')
    def test_daily_scrum_returns_pdf(self, client, owner, team):
        auth(client, owner)
        resp = client.get(f'/api/teams/{team.slug}/reports/daily-scrum/')
        assert resp.status_code == 200
        assert resp['Content-Type'] == 'application/pdf'
        assert resp['Content-Disposition'].startswith('attachment; filename="daily-scrum-')
        assert len(resp.content) > 0
        assert resp.content[:4] == b'%PDF'

    def test_daily_scrum_outsider_fails(self, client, team):
        stranger = User.objects.create_user('stranger3', 's3@t.com', 'pass1234')
        auth(client, stranger)
        resp = client.get(f'/api/teams/{team.slug}/reports/daily-scrum/')
        assert resp.status_code == 403


class TestWeeklyReportAPI:
    @pytest.mark.skipif(not _weasyprint_available(), reason='WeasyPrint requires GTK3 runtime on Windows')
    def test_weekly_report_returns_pdf(self, client, owner, team):
        auth(client, owner)
        resp = client.get(f'/api/teams/{team.slug}/reports/weekly/')
        assert resp.status_code == 200
        assert resp['Content-Type'] == 'application/pdf'
        assert resp['Content-Disposition'].startswith('attachment; filename="weekly-report-')
        assert len(resp.content) > 0
        assert resp.content[:4] == b'%PDF'

    def test_weekly_report_outsider_fails(self, client, team):
        stranger = User.objects.create_user('stranger4', 's4@t.com', 'pass1234')
        auth(client, stranger)
        resp = client.get(f'/api/teams/{team.slug}/reports/weekly/')
        assert resp.status_code == 403
