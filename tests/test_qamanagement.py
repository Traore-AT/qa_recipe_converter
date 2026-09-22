"""
Tests unitaires — Module QA Management (apps.qamanagement)

Couvre :
- Modèles : Sprint, UseCaseAssignment, Defect, WeeklyReport, ActivityLog, UseCaseComment
- API : Sprints CRUD, Assignments, UseCase status update, Defects CRUD,
        Weekly Reports, Member Progress, Notifications, Activity
- Permissions et règles métier
- Génération de notifications / ActivityLog
"""
import uuid
import pytest
from datetime import date, timedelta
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.core.models import ConversionJob, ExtractedUseCase
from apps.teams.models import Team, TeamMember, TeamInvitation, Project, ProjectMember
from apps.qamanagement.models import (
    Sprint, UseCaseAssignment, Defect, WeeklyReport, ActivityLog, UseCaseComment
)


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def owner(db):
    return User.objects.create_user('qa_owner', 'qa_owner@test.com', 'pass1234')

@pytest.fixture
def lead_user(db):
    return User.objects.create_user('qa_lead', 'qa_lead@test.com', 'pass1234')

@pytest.fixture
def tester_user(db):
    return User.objects.create_user('qa_tester', 'qa_tester@test.com', 'pass1234')

@pytest.fixture
def viewer_user(db):
    return User.objects.create_user('qa_viewer', 'qa_viewer@test.com', 'pass1234')

@pytest.fixture
def outsider(db):
    return User.objects.create_user('qa_outsider', 'qa_outsider@test.com', 'pass1234')

@pytest.fixture
def team(db, owner, lead_user, tester_user, viewer_user):
    t = Team.objects.create(name='QA Management Team', owner=owner)
    TeamMember.objects.create(team=t, user=owner, role='owner')
    TeamMember.objects.create(team=t, user=lead_user, role='admin')
    TeamMember.objects.create(team=t, user=tester_user, role='member')
    TeamMember.objects.create(team=t, user=viewer_user, role='viewer')
    return t

@pytest.fixture
def project(db, team, owner):
    p = Project.objects.create(
        team=team, name='QA Project', created_by=owner, visibility='team'
    )
    ProjectMember.objects.create(project=p, user=owner, role='lead')
    return p

@pytest.fixture
def lead_pm(db, project, lead_user):
    return ProjectMember.objects.create(project=project, user=lead_user, role='lead')

@pytest.fixture
def tester_pm(db, project, tester_user):
    return ProjectMember.objects.create(project=project, user=tester_user, role='tester')

@pytest.fixture
def done_job(db, project, owner):
    job = ConversionJob.objects.create(
        source_filename='recette_qa.docx',
        word_file='uploads/word/recette_qa.docx',
        status=ConversionJob.Status.DONE,
        use_cases_count=5,
        project=project,
        uploaded_by=owner,
    )
    ucs = []
    for i in range(5):
        uc = ExtractedUseCase.objects.create(
            job=job, order=i + 1,
            use_case_text=f'UC{i+1:03d}',
            description=f'Cas de test QA #{i+1}',
            steps=f'Étape {i+1}.1\nÉtape {i+1}.2',
            expected_results=f'Résultat attendu {i+1}',
            status='À tester',
        )
        ucs.append(uc)
    return job, ucs

@pytest.fixture
def sprint(db, project, owner):
    return Sprint.objects.create(
        project=project, name='Sprint 1',
        goal='Tester les fonctionnalités critiques',
        start_date=date.today(),
        end_date=date.today() + timedelta(days=14),
        status='active',
        created_by=owner,
    )

@pytest.fixture
def assignment(db, sprint, done_job, tester_pm, owner):
    uc = done_job[1][0]
    return UseCaseAssignment.objects.create(
        use_case=uc, sprint=sprint,
        assigned_to=tester_pm, assigned_by=owner,
        status='À tester',
    )

@pytest.fixture
def defect(db, project, done_job, owner):
    uc = done_job[1][0]
    return Defect.objects.create(
        use_case=uc, project=project,
        title='Bug de connexion',
        description='Impossible de se connecter avec des identifiants valides',
        severity='critical', priority='high',
        status='open', reported_by=owner,
        steps_to_reproduce='1. Aller sur la page de login\n2. Saisir identifiants valides\n3. Cliquer sur Connexion',
        expected_behavior='Connexion réussie',
        actual_behavior='Erreur 500',
    )

@pytest.fixture
def weekly_report(db, team, tester_user):
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    return WeeklyReport.objects.create(
        user=tester_user, team=team,
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        accomplishments='Test de 10 cas de connexion\nRédaction du rapport',
        blockers='Environnement de test instable',
        next_week_plans='Tester le module de recherche',
        status='submitted',
        submitted_at=timezone.now(),
    )

@pytest.fixture
def activity_log(db, project, team, owner):
    return ActivityLog.objects.create(
        project=project, team=team, actor=owner,
        action_type='use_case_status',
        description='UC#001 : À tester → En cours',
        metadata={'uc_id': str(uuid.uuid4()), 'old_status': 'À tester', 'new_status': 'En cours'},
    )

@pytest.fixture
def client():
    return APIClient()


# ═════════════════════════════════════════════════════════════════════════════
# TESTS MODÈLES
# ═════════════════════════════════════════════════════════════════════════════

class TestSprintModel:
    def test_create_sprint(self, db, project, owner):
        s = Sprint.objects.create(
            project=project, name='Sprint Test',
            start_date=date.today(), end_date=date.today() + timedelta(days=7),
            created_by=owner,
        )
        assert s.name == 'Sprint Test'
        assert s.status == 'planned'
        assert s.duration_days == 7
        assert str(s) == f'{project.name} / Sprint Test'

    def test_sprint_stats_empty(self, db, sprint):
        stats = sprint.stats
        assert stats['total'] == 0
        assert stats['progress_pct'] == 0.0

    def test_sprint_stats_with_assignments(self, db, sprint, done_job, tester_pm, owner):
        ucs = done_job[1]
        for i, uc in enumerate(ucs):
            UseCaseAssignment.objects.create(
                use_case=uc, sprint=sprint,
                assigned_to=tester_pm, assigned_by=owner,
                status='Passé' if i < 3 else 'Échoué' if i == 3 else 'À tester',
            )
        stats = sprint.stats
        assert stats['total'] == 5
        assert stats['passed'] == 3
        assert stats['failed'] == 1
        assert stats['progress_pct'] == 80.0

    def test_sprint_default_status(self, db, project, owner):
        s = Sprint.objects.create(
            project=project, name='New Sprint',
            start_date=date.today(), end_date=date.today() + timedelta(days=14),
            created_by=owner,
        )
        assert s.status == 'planned'

    def test_sprint_str(self, db, sprint):
        assert sprint.project.name in str(sprint)
        assert sprint.name in str(sprint)


class TestUseCaseAssignmentModel:
    def test_create_assignment(self, db, done_job, tester_pm, owner):
        uc = done_job[1][0]
        a = UseCaseAssignment.objects.create(
            use_case=uc, assigned_to=tester_pm,
            assigned_by=owner, status='À tester',
        )
        assert a.assigned_to.user.username == 'qa_tester'
        assert str(a).startswith('qa_tester')

    def test_assignment_unique_constraint(self, db, done_job, tester_pm, owner):
        uc = done_job[1][0]
        UseCaseAssignment.objects.create(use_case=uc, assigned_to=tester_pm, assigned_by=owner)
        with pytest.raises(Exception):
            UseCaseAssignment.objects.create(use_case=uc, assigned_to=tester_pm, assigned_by=owner)

    def test_assignment_ordering(self, db, done_job, tester_pm, lead_pm, owner):
        ucs = done_job[1]
        a1 = UseCaseAssignment.objects.create(use_case=ucs[0], assigned_to=tester_pm, assigned_by=owner)
        a2 = UseCaseAssignment.objects.create(use_case=ucs[1], assigned_to=lead_pm, assigned_by=owner)
        assert a1.assigned_at <= a2.assigned_at


class TestDefectModel:
    def test_create_defect(self, db, project, owner):
        d = Defect.objects.create(
            project=project, title='Bug #1',
            severity='major', priority='high',
            reported_by=owner,
        )
        assert d.status == 'open'
        assert d.severity == 'major'
        assert 'Bug' in str(d)

    def test_defect_default_status(self, db, project, owner):
        d = Defect.objects.create(project=project, title='Test Bug', reported_by=owner)
        assert d.status == 'open'

    def test_defect_severity_choices(self, db, project, owner):
        for sev in ['critical', 'major', 'minor', 'trivial']:
            d = Defect.objects.create(project=project, title=f'Bug {sev}', severity=sev, reported_by=owner)
            assert d.severity == sev

    def test_defect_str(self, db, defect):
        assert '[Critique]' in str(defect)
        assert 'Bug de connexion' in str(defect)


class TestWeeklyReportModel:
    def test_create_report(self, db, team, tester_user):
        today = date.today()
        ws = today - timedelta(days=today.weekday())
        r = WeeklyReport.objects.create(
            user=tester_user, team=team,
            week_start=ws, week_end=ws + timedelta(days=6),
        )
        assert r.status == 'draft'
        assert r.submitted_at is None

    def test_report_submit(self, db, team, tester_user):
        today = date.today()
        ws = today - timedelta(days=today.weekday())
        r = WeeklyReport.objects.create(
            user=tester_user, team=team,
            week_start=ws, week_end=ws + timedelta(days=6),
        )
        r.submit()
        assert r.status == 'submitted'
        assert r.submitted_at is not None

    def test_report_unique_per_week(self, db, team, tester_user):
        today = date.today()
        ws = today - timedelta(days=today.weekday())
        WeeklyReport.objects.create(user=tester_user, team=team, week_start=ws, week_end=ws + timedelta(days=6))
        with pytest.raises(Exception):
            WeeklyReport.objects.create(user=tester_user, team=team, week_start=ws, week_end=ws + timedelta(days=6))

    def test_report_str(self, db, weekly_report):
        assert weekly_report.user.username in str(weekly_report)


class TestActivityLogModel:
    def test_create_activity(self, db, project, team, owner):
        a = ActivityLog.objects.create(
            project=project, team=team, actor=owner,
            action_type='defect_created',
            description='Nouveau bug signalé',
        )
        assert a.is_read is False
        assert a.action_type == 'defect_created'

    def test_activity_target_users(self, db, activity_log, tester_user):
        activity_log.target_users.add(tester_user)
        assert tester_user in activity_log.target_users.all()

    def test_activity_ordering(self, db, project, team, owner):
        a1 = ActivityLog.objects.create(project=project, team=team, actor=owner, action_type='test', description='First')
        a2 = ActivityLog.objects.create(project=project, team=team, actor=owner, action_type='test', description='Second')
        logs = ActivityLog.objects.all()
        assert logs[0] == a2  # newest first


class TestUseCaseCommentModel:
    def test_create_comment(self, db, done_job, owner):
        uc = done_job[1][0]
        c = UseCaseComment.objects.create(
            use_case=uc, author=owner,
            content='Ce cas semble incorrect',
        )
        assert c.author.username == 'qa_owner'
        assert str(c).startswith('qa_owner')

    def test_comment_ordering(self, db, done_job, owner, tester_user):
        uc = done_job[1][0]
        c1 = UseCaseComment.objects.create(use_case=uc, author=owner, content='First')
        c2 = UseCaseComment.objects.create(use_case=uc, author=tester_user, content='Second')
        comments = UseCaseComment.objects.filter(use_case=uc)
        assert list(comments) == [c1, c2]  # chronological order


# ═════════════════════════════════════════════════════════════════════════════
# TESTS API — SPRINTS
# ═════════════════════════════════════════════════════════════════════════════

class TestSprintAPI:
    SPRINT_DATA = {
        'name': 'Sprint Test API',
        'goal': 'Valider le module X',
        'start_date': str(date.today()),
        'end_date': str(date.today() + timedelta(days=14)),
        'status': 'planned',
    }

    def test_list_sprints_unauthenticated(self, client, team, project):
        url = reverse('qamanagement:sprint-list', args=[team.slug, project.slug])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_list_sprints_authenticated(self, client, team, project, sprint, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:sprint-list', args=[team.slug, project.slug])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['count'] >= 1

    def test_create_sprint_as_lead(self, client, team, project, lead_user):
        client.force_authenticate(lead_user)
        url = reverse('qamanagement:sprint-list', args=[team.slug, project.slug])
        resp = client.post(url, self.SPRINT_DATA, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['name'] == 'Sprint Test API'
        assert resp.data['stats']['total'] == 0

    def test_create_sprint_as_tester_forbidden(self, client, team, project, tester_user):
        client.force_authenticate(tester_user)
        url = reverse('qamanagement:sprint-list', args=[team.slug, project.slug])
        resp = client.post(url, self.SPRINT_DATA, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_create_sprint_invalid_dates(self, client, team, project, lead_user):
        client.force_authenticate(lead_user)
        url = reverse('qamanagement:sprint-list', args=[team.slug, project.slug])
        data = self.SPRINT_DATA.copy()
        data['end_date'] = str(date.today() - timedelta(days=1))
        resp = client.post(url, data, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_sprint_detail(self, client, team, project, sprint, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:sprint-detail', args=[team.slug, project.slug, sprint.id])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['name'] == sprint.name

    def test_update_sprint_status(self, client, team, project, sprint, lead_user):
        client.force_authenticate(lead_user)
        url = reverse('qamanagement:sprint-detail', args=[team.slug, project.slug, sprint.id])
        resp = client.patch(url, {'status': 'completed'}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['status'] == 'completed'

    def test_delete_sprint(self, client, team, project, sprint, lead_user):
        client.force_authenticate(lead_user)
        url = reverse('qamanagement:sprint-detail', args=[team.slug, project.slug, sprint.id])
        resp = client.delete(url)
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_sprint_board(self, client, team, project, sprint, assignment, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:sprint-board', args=[team.slug, project.slug, sprint.id])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert 'columns' in resp.data
        assert 'À tester' in resp.data['columns']
        assert resp.data['stats']['total'] >= 1

    def test_sprint_board_empty(self, client, team, project, owner):
        s = Sprint.objects.create(
            project=project, name='Empty Sprint',
            start_date=date.today(), end_date=date.today() + timedelta(days=7),
            created_by=owner,
        )
        client.force_authenticate(owner)
        url = reverse('qamanagement:sprint-board', args=[team.slug, project.slug, s.id])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['stats']['total'] == 0


# ═════════════════════════════════════════════════════════════════════════════
# TESTS API — ASSIGNMENTS
# ═════════════════════════════════════════════════════════════════════════════

class TestAssignmentAPI:
    def test_list_assignments(self, client, team, project, assignment, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:assignment-list', args=[team.slug, project.slug])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['count'] >= 1

    def test_create_assignment(self, client, team, project, sprint, done_job, tester_user, tester_pm, lead_user):
        client.force_authenticate(lead_user)
        ucs = done_job[1]
        url = reverse('qamanagement:assignment-list', args=[team.slug, project.slug])
        resp = client.post(url, {
            'use_case_ids': [str(ucs[0].id), str(ucs[1].id)],
            'sprint_id': str(sprint.id),
            'user_id': tester_user.id,
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['count'] == 2

    def test_create_assignment_duplicate(self, client, team, project, sprint, done_job, tester_pm, tester_user, lead_user):
        uc = done_job[1][0]
        UseCaseAssignment.objects.create(use_case=uc, sprint=sprint, assigned_to=tester_pm, assigned_by=lead_user)
        client.force_authenticate(lead_user)
        url = reverse('qamanagement:assignment-list', args=[team.slug, project.slug])
        resp = client.post(url, {
            'use_case_ids': [str(uc.id)],
            'sprint_id': str(sprint.id),
            'user_id': tester_user.id,
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert len(resp.data['errors']) >= 1

    def test_create_assignment_non_member(self, client, team, project, done_job, outsider, lead_user):
        client.force_authenticate(lead_user)
        uc = done_job[1][0]
        url = reverse('qamanagement:assignment-list', args=[team.slug, project.slug])
        resp = client.post(url, {
            'use_case_ids': [str(uc.id)],
            'user_id': outsider.id,
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_assignment(self, client, team, project, assignment, lead_user):
        client.force_authenticate(lead_user)
        url = reverse('qamanagement:assignment-delete', args=[team.slug, project.slug, assignment.id])
        resp = client.delete(url)
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_unassigned_ucs(self, client, team, project, done_job, assignment, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:unassigned-ucs', args=[team.slug, project.slug])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['count'] == 4  # 5 total - 1 assigned


# ═════════════════════════════════════════════════════════════════════════════
# TESTS API — USE CASE EXECUTION
# ═════════════════════════════════════════════════════════════════════════════

class TestUseCaseExecutionAPI:
    def test_update_status(self, client, team, project, done_job, tester_user):
        client.force_authenticate(tester_user)
        uc = done_job[1][0]
        url = reverse('qamanagement:uc-status-update', args=[team.slug, project.slug, uc.id])
        resp = client.patch(url, {'status': 'En cours'}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['status'] == 'En cours'

    def test_update_status_with_observed_results(self, client, team, project, done_job, tester_user):
        client.force_authenticate(tester_user)
        uc = done_job[1][0]
        url = reverse('qamanagement:uc-status-update', args=[team.slug, project.slug, uc.id])
        resp = client.patch(url, {'status': 'Passé', 'observed_results': 'Test OK'}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['observed_results'] == 'Test OK'

    def test_update_status_invalid(self, client, team, project, done_job, tester_user):
        client.force_authenticate(tester_user)
        uc = done_job[1][0]
        url = reverse('qamanagement:uc-status-update', args=[team.slug, project.slug, uc.id])
        resp = client.patch(url, {'status': 'INVALID'}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_comments(self, client, team, project, done_job, owner):
        client.force_authenticate(owner)
        uc = done_job[1][0]
        UseCaseComment.objects.create(use_case=uc, author=owner, content='Test comment')
        url = reverse('qamanagement:uc-comments', args=[team.slug, project.slug, uc.id])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['count'] >= 1

    def test_create_comment(self, client, team, project, done_job, tester_user):
        client.force_authenticate(tester_user)
        uc = done_job[1][0]
        url = reverse('qamanagement:uc-comments', args=[team.slug, project.slug, uc.id])
        resp = client.post(url, {'use_case': str(uc.id), 'content': 'Nouveau commentaire'}, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['content'] == 'Nouveau commentaire'


# ═════════════════════════════════════════════════════════════════════════════
# TESTS API — DEFECTS
# ═════════════════════════════════════════════════════════════════════════════

class TestDefectAPI:
    DEFECT_DATA = {
        'title': 'API Bug Test',
        'description': 'Description du bug',
        'severity': 'major',
        'priority': 'high',
        'steps_to_reproduce': 'Step 1\nStep 2',
        'expected_behavior': 'Should work',
        'actual_behavior': 'Does not work',
    }

    def test_list_defects(self, client, team, project, defect, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:defect-list', args=[team.slug, project.slug])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['count'] >= 1

    def test_create_defect(self, client, team, project, tester_user):
        client.force_authenticate(tester_user)
        url = reverse('qamanagement:defect-list', args=[team.slug, project.slug])
        resp = client.post(url, self.DEFECT_DATA, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['title'] == 'API Bug Test'
        assert resp.data['status'] == 'open'

    def test_get_defect_detail(self, client, team, project, defect, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:defect-detail', args=[team.slug, project.slug, defect.id])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['title'] == defect.title

    def test_update_defect_status(self, client, team, project, defect, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:defect-detail', args=[team.slug, project.slug, defect.id])
        resp = client.patch(url, {'status': 'in_progress'}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['status'] == 'in_progress'

    def test_update_defect_assign(self, client, team, project, defect, tester_user, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:defect-detail', args=[team.slug, project.slug, defect.id])
        resp = client.patch(url, {'assigned_to': tester_user.id}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['assigned_to'] == tester_user.id

    def test_delete_defect(self, client, team, project, defect, lead_user):
        client.force_authenticate(lead_user)
        url = reverse('qamanagement:defect-detail', args=[team.slug, project.slug, defect.id])
        resp = client.delete(url)
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_defect_stats(self, client, team, project, defect, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:defect-stats', args=[team.slug, project.slug])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['total'] >= 1
        assert resp.data['open'] >= 1
        assert resp.data['critical'] >= 1

    def test_defect_filter_by_status(self, client, team, project, defect, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:defect-list', args=[team.slug, project.slug])
        resp = client.get(url, {'status': 'open'})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['count'] >= 1

        resp = client.get(url, {'status': 'closed'})
        assert resp.data['count'] == 0


# ═════════════════════════════════════════════════════════════════════════════
# TESTS API — WEEKLY REPORTS
# ═════════════════════════════════════════════════════════════════════════════

class TestWeeklyReportAPI:
    def test_list_reports(self, client, team, weekly_report, tester_user):
        client.force_authenticate(tester_user)
        url = reverse('qamanagement:weekly-report-list', args=[team.slug])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['count'] >= 1

    def test_create_report(self, client, team, tester_user):
        client.force_authenticate(tester_user)
        today = date.today()
        ws = today - timedelta(days=today.weekday())
        url = reverse('qamanagement:weekly-report-list', args=[team.slug])
        resp = client.post(url, {
            'accomplishments': 'Tested feature X',
            'blockers': 'No blockers',
            'next_week_plans': 'Test feature Y',
            'week_start': str(ws),
            'week_end': str(ws + timedelta(days=6)),
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['status'] == 'draft'

    def test_submit_report(self, client, team, tester_user):
        client.force_authenticate(tester_user)
        today = date.today()
        ws = today - timedelta(days=today.weekday())
        url = reverse('qamanagement:weekly-report-list', args=[team.slug])
        resp = client.post(url, {
            'accomplishments': 'Done',
            'status': 'submitted',
            'week_start': str(ws),
            'week_end': str(ws + timedelta(days=6)),
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['status'] == 'submitted'

    def test_get_current_report(self, client, team, weekly_report, tester_user):
        client.force_authenticate(tester_user)
        url = reverse('qamanagement:weekly-report-current', args=[team.slug])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['status'] == 'submitted'

    def test_update_report(self, client, team, weekly_report, tester_user):
        client.force_authenticate(tester_user)
        url = reverse('qamanagement:weekly-report-detail', args=[team.slug, weekly_report.id])
        resp = client.patch(url, {'accomplishments': 'Updated content'}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['accomplishments'] == 'Updated content'

    def test_lead_can_view_member_report(self, client, team, weekly_report, lead_user):
        client.force_authenticate(lead_user)
        url = reverse('qamanagement:weekly-report-list', args=[team.slug])
        resp = client.get(url, {'user_id': weekly_report.user.id})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['count'] >= 1

    def test_outsider_cannot_access(self, client, team, outsider):
        client.force_authenticate(outsider)
        url = reverse('qamanagement:weekly-report-list', args=[team.slug])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ═════════════════════════════════════════════════════════════════════════════
# TESTS API — MEMBER PROGRESS
# ═════════════════════════════════════════════════════════════════════════════

class TestMemberProgressAPI:
    def test_member_progress(self, client, team, project, done_job, tester_pm, lead_pm, owner):
        ucs = done_job[1]
        UseCaseAssignment.objects.create(use_case=ucs[0], assigned_to=tester_pm, assigned_by=owner, status='Passé')
        UseCaseAssignment.objects.create(use_case=ucs[1], assigned_to=tester_pm, assigned_by=owner, status='En cours')
        UseCaseAssignment.objects.create(use_case=ucs[2], assigned_to=lead_pm, assigned_by=owner, status='À tester')

        client.force_authenticate(owner)
        url = reverse('qamanagement:member-progress', args=[team.slug, project.slug])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data['results']) >= 2

        tester_progress = [r for r in resp.data['results'] if r['user']['username'] == 'qa_tester'][0]
        assert tester_progress['total_ucs'] == 2
        assert tester_progress['passed'] == 1
        assert tester_progress['in_progress'] == 1


# ═════════════════════════════════════════════════════════════════════════════
# TESTS API — NOTIFICATIONS / ACTIVITY
# ═════════════════════════════════════════════════════════════════════════════

class TestNotificationAPI:
    def test_list_notifications(self, client, activity_log, tester_user):
        activity_log.target_users.add(tester_user)
        client.force_authenticate(tester_user)
        url = reverse('qamanagement:notification-list')
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['unread_count'] >= 1

    def test_mark_notification_read(self, client, activity_log, tester_user):
        activity_log.target_users.add(tester_user)
        client.force_authenticate(tester_user)
        url = reverse('qamanagement:notification-read', args=[activity_log.id])
        resp = client.patch(url)
        assert resp.status_code == status.HTTP_200_OK
        activity_log.refresh_from_db()
        assert activity_log.is_read is True

    def test_mark_all_read(self, client, activity_log, tester_user):
        activity_log.target_users.add(tester_user)
        ActivityLog.objects.create(
            project=activity_log.project, team=activity_log.team, actor=activity_log.actor,
            action_type='test', description='Second notification',
        ).target_users.add(tester_user)
        client.force_authenticate(tester_user)
        url = reverse('qamanagement:notifications-mark-all-read')
        resp = client.post(url)
        assert resp.status_code == status.HTTP_200_OK
        unread = ActivityLog.objects.filter(target_users=tester_user, is_read=False).count()
        assert unread == 0

    def test_team_activity(self, client, team, activity_log, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:team-activity', args=[team.slug])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['count'] >= 1

    def test_project_activity(self, client, team, project, activity_log, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:project-activity', args=[team.slug, project.slug])
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['count'] >= 1


# ═════════════════════════════════════════════════════════════════════════════
# TESTS — INTÉGRATION (workflows complets)
# ═════════════════════════════════════════════════════════════════════════════

class TestFullWorkflow:
    def test_full_sprint_to_defect_workflow(self, client, team, project, done_job, owner, tester_user, tester_pm, lead_user):
        ucs = done_job[1]
        client.force_authenticate(lead_user)

        # 1. Create sprint
        sprint_url = reverse('qamanagement:sprint-list', args=[team.slug, project.slug])
        s_resp = client.post(sprint_url, {
            'name': 'Regression Sprint',
            'start_date': str(date.today()),
            'end_date': str(date.today() + timedelta(days=10)),
        }, format='json')
        assert s_resp.status_code == status.HTTP_201_CREATED
        sprint_id = s_resp.data['id']

        # 2. Assign use cases to tester
        assign_url = reverse('qamanagement:assignment-list', args=[team.slug, project.slug])
        a_resp = client.post(assign_url, {
            'use_case_ids': [str(ucs[0].id), str(ucs[1].id)],
            'sprint_id': sprint_id,
            'user_id': tester_user.id,
        }, format='json')
        assert a_resp.status_code == status.HTTP_201_CREATED
        assert a_resp.data['count'] == 2

        # 3. Tester executes use cases
        client.force_authenticate(tester_user)
        uc_url = reverse('qamanagement:uc-status-update', args=[team.slug, project.slug, ucs[0].id])
        uc_resp = client.patch(uc_url, {'status': 'Passé', 'observed_results': 'All OK'}, format='json')
        assert uc_resp.status_code == status.HTTP_200_OK
        assert uc_resp.data['status'] == 'Passé'

        # 4. Tester reports a defect on second UC
        defect_url = reverse('qamanagement:defect-list', args=[team.slug, project.slug])
        d_resp = client.post(defect_url, {
            'title': 'Regression on login',
            'severity': 'critical',
            'priority': 'high',
            'use_case': str(ucs[1].id),
        }, format='json')
        assert d_resp.status_code == status.HTTP_201_CREATED

        # 5. Sprint board reflects updated statuses
        client.force_authenticate(lead_user)
        board_url = reverse('qamanagement:sprint-board', args=[team.slug, project.slug, sprint_id])
        b_resp = client.get(board_url)
        assert b_resp.status_code == status.HTTP_200_OK
        assert b_resp.data['stats']['passed'] == 1
        assert len(b_resp.data['columns']['À tester']) >= 1

        # 6. Leader sees member progress
        progress_url = reverse('qamanagement:member-progress', args=[team.slug, project.slug])
        p_resp = client.get(progress_url)
        assert p_resp.status_code == status.HTTP_200_OK
        tester_progress = [r for r in p_resp.data['results'] if r['user']['username'] == 'qa_tester']
        assert len(tester_progress) >= 1
        assert tester_progress[0]['passed'] >= 1

        # 7. Activity logs were created
        act_url = reverse('qamanagement:project-activity', args=[team.slug, project.slug])
        act_resp = client.get(act_url)
        assert act_resp.status_code == status.HTTP_200_OK
        assert act_resp.data['count'] >= 3  # sprint_created + assignments_created + use_case_status

    def test_weekly_report_workflow(self, client, team, owner, tester_user):
        client.force_authenticate(tester_user)

        url = reverse('qamanagement:weekly-report-list', args=[team.slug])
        today = date.today()
        ws = today - timedelta(days=today.weekday())

        # 1. Create draft
        r1_resp = client.post(url, {
            'accomplishments': 'Week work',
            'week_start': str(ws),
            'week_end': str(ws + timedelta(days=6)),
        }, format='json')
        assert r1_resp.status_code == status.HTTP_201_CREATED
        report_id = r1_resp.data['id']

        # 2. Update draft
        detail_url = reverse('qamanagement:weekly-report-detail', args=[team.slug, report_id])
        r2_resp = client.patch(detail_url, {'accomplishments': 'Updated week work'}, format='json')
        assert r2_resp.status_code == status.HTTP_200_OK
        assert r2_resp.data['accomplishments'] == 'Updated week work'

        # 3. Submit
        r3_resp = client.patch(detail_url, {'status': 'submitted'}, format='json')
        assert r3_resp.status_code == status.HTTP_200_OK
        assert r3_resp.data['status'] == 'submitted'

        # 4. Leader can see it
        client.force_authenticate(owner)
        r4_resp = client.get(url, {'user_id': tester_user.id})
        assert r4_resp.status_code == status.HTTP_200_OK
        assert r4_resp.data['count'] >= 1

        # 5. Notification was created for the leader
        notif_url = reverse('qamanagement:notification-list')
        n_resp = client.get(notif_url)
        assert n_resp.status_code == status.HTTP_200_OK
        weekly_notifs = [n for n in n_resp.data['results'] if n['action_type'] == 'weekly_report_submitted']
        assert len(weekly_notifs) >= 1
