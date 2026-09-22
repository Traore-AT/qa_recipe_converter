"""
Tests unitaires — Espace Super-Admin (apps.admin_api)

Couvre :
- Garde d'accès (seul un superuser accède aux endpoints /api/admin/)
- Résumé des KPIs globaux
- Gestion des utilisateurs (création, mise à jour, guards anti-last-superuser,
  désactivation, suppression, reset mot de passe)
- Gestion des équipes (liste, transfert de propriété, suppression)
- Gestion des projets / jobs
- Journal d'activité global
- Statistiques temporelles et état système
- Traçabilité des actions admin dans ActivityLog
"""
import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from apps.teams.models import Team, TeamMember, Project
from apps.core.models import ConversionJob, ExtractedUseCase
from apps.qamanagement.models import ActivityLog, Sprint, Defect, WeeklyReport


@pytest.fixture
def superadmin(db):
    return User.objects.create_superuser('root_admin', 'root@admin.com', 'pass1234')

@pytest.fixture
def staff_user(db):
    return User.objects.create_user('staff_u', 'staff@test.com', 'pass1234', is_staff=True)

@pytest.fixture
def regular_user(db):
    return User.objects.create_user('plain_u', 'plain@test.com', 'pass1234')

@pytest.fixture
def client():
    return APIClient()

def auth(client, user):
    client.force_authenticate(user=user)
    return client


# ─────────────────────────────────────────────────────────────────────────────
# GARDE D'ACCÈS
# ─────────────────────────────────────────────────────────────────────────────
class TestAccessGuard:
    @pytest.mark.parametrize('kind', ['anonymous', 'staff', 'regular'])
    def test_forbidden_for_non_superadmin(self, db, client, staff_user, regular_user, kind):
        if kind == 'staff':
            auth(client, staff_user)
        elif kind == 'regular':
            auth(client, regular_user)
        resp = client.get(reverse('admin_api:summary'))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_allowed_for_superadmin(self, db, client, superadmin):
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:summary'))
        assert resp.status_code == status.HTTP_200_OK

    def test_me_exposes_superuser_flag(self, db, client, superadmin):
        auth(client, superadmin)
        resp = client.get('/api/auth/me/')
        assert resp.status_code == 200
        assert resp.data['is_superuser'] is True


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY / STATISTICS / SYSTEM
# ─────────────────────────────────────────────────────────────────────────────
class TestSummary:
    def test_summary_shape(self, db, client, superadmin, regular_user):
        Team.objects.create(name='Alpha', owner=regular_user)
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:summary'))
        assert resp.status_code == 200
        data = resp.data
        assert data['users']['total'] >= 2
        assert 'teams' in data and data['teams']['total'] == 1
        assert 'projects' in data and 'jobs' in data and 'use_cases' in data
        assert 'defects' in data and 'sprints' in data and 'reports' in data
        assert 'storage' in data and 'media' in data['storage']
        assert isinstance(data['recent_activity'], list)

    def test_statistics_shape(self, db, client, superadmin):
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:statistics'))
        assert resp.status_code == 200
        data = resp.data
        for key in ['users', 'conversions', 'teams', 'projects']:
            assert isinstance(data[key], list)
        for key in ['jobs_by_status', 'uc_by_status', 'defects_by_status', 'defects_by_severity']:
            assert isinstance(data[key], list)

    def test_system_shape(self, db, client, superadmin):
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:system'))
        assert resp.status_code == 200
        assert 'media' in resp.data and 'database' in resp.data
        assert 'total_files' in resp.data['media'] and 'total_bytes' in resp.data['media']


# ─────────────────────────────────────────────────────────────────────────────
# USERS
# ─────────────────────────────────────────────────────────────────────────────
class TestUsers:
    def test_list_paginated(self, db, client, superadmin):
        for i in range(5):
            User.objects.create_user(f'user_{i}', f'user{i}@test.com', 'pass1234')
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:users'))
        assert resp.status_code == 200
        assert 'results' in resp.data and 'count' in resp.data
        assert resp.data['count'] >= 5

    def test_search_filter(self, db, client, superadmin, regular_user):
        User.objects.create_user('alice_dupont', 'alice@test.com', 'pass1234')
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:users'), {'search': 'alice'})
        assert resp.status_code == 200
        names = [u['username'] for u in resp.data['results']]
        assert 'alice_dupont' in names

    def test_active_filter(self, db, client, superadmin, regular_user):
        regular_user.is_active = False
        regular_user.save()
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:users'), {'active': 'false'})
        assert resp.status_code == 200
        assert all(u['is_active'] is False for u in resp.data['results'])

    def test_create_user(self, db, client, superadmin):
        auth(client, superadmin)
        resp = client.post(reverse('admin_api:users'), {
            'username': 'newbie', 'email': 'newbie@test.com',
            'password': 'Str0ngPass', 'first_name': 'New', 'last_name': 'Bie',
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert User.objects.filter(username='newbie').exists()
        assert ActivityLog.objects.filter(action_type='admin_user_created').exists()

    def test_update_user_inactivate(self, db, client, superadmin, regular_user):
        auth(client, superadmin)
        resp = client.patch(reverse('admin_api:user-detail', args=[regular_user.pk]),
                            {'is_active': False}, format='json')
        assert resp.status_code == 200
        regular_user.refresh_from_db()
        assert regular_user.is_active is False

    def test_promote_superuser(self, db, client, superadmin, regular_user):
        auth(client, superadmin)
        resp = client.patch(reverse('admin_api:user-detail', args=[regular_user.pk]),
                            {'is_staff': True, 'is_superuser': True}, format='json')
        assert resp.status_code == 200
        regular_user.refresh_from_db()
        assert regular_user.is_superuser is True

    def test_cannot_disable_self(self, db, client, superadmin):
        auth(client, superadmin)
        resp = client.patch(reverse('admin_api:user-detail', args=[superadmin.pk]),
                            {'is_active': False}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_demote_last_superuser(self, db, client, superadmin):
        auth(client, superadmin)
        resp = client.patch(reverse('admin_api:user-detail', args=[superadmin.pk]),
                            {'is_superuser': False}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_user_with_owned_team_blocked(self, db, client, superadmin, regular_user):
        Team.objects.create(name='Owned', owner=regular_user)
        auth(client, superadmin)
        resp = client.delete(reverse('admin_api:user-detail', args=[regular_user.pk]))
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_user_ok(self, db, client, superadmin, regular_user):
        auth(client, superadmin)
        resp = client.delete(reverse('admin_api:user-detail', args=[regular_user.pk]))
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not User.objects.filter(pk=regular_user.pk).exists()

    def test_delete_self_blocked(self, db, client, superadmin):
        auth(client, superadmin)
        resp = client.delete(reverse('admin_api:user-detail', args=[superadmin.pk]))
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_reset_password_generated(self, db, client, superadmin, regular_user):
        auth(client, superadmin)
        resp = client.post(reverse('admin_api:user-reset-password', args=[regular_user.pk]), {}, format='json')
        assert resp.status_code == 200
        assert 'temp_password' in resp.data
        regular_user.refresh_from_db()
        assert regular_user.check_password(resp.data['temp_password'])
        assert ActivityLog.objects.filter(action_type='admin_user_password_reset').exists()

    def test_reset_password_custom(self, db, client, superadmin, regular_user):
        auth(client, superadmin)
        resp = client.post(reverse('admin_api:user-reset-password', args=[regular_user.pk]),
                           {'password': 'MyNewPass1'}, format='json')
        assert resp.status_code == 200
        regular_user.refresh_from_db()
        assert regular_user.check_password('MyNewPass1')


# ─────────────────────────────────────────────────────────────────────────────
# TEAMS
# ─────────────────────────────────────────────────────────────────────────────
class TestTeams:
    def test_list(self, db, client, superadmin, regular_user):
        Team.objects.create(name='Alpha', owner=regular_user)
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:teams'))
        assert resp.status_code == 200
        assert resp.data['count'] >= 1
        assert 'members_count' in resp.data['results'][0]

    def test_patch_team(self, db, client, superadmin, regular_user):
        team = Team.objects.create(name='Alpha', owner=regular_user)
        auth(client, superadmin)
        resp = client.patch(reverse('admin_api:team-detail', args=[team.slug]),
                            {'description': 'Nouvelle description'}, format='json')
        assert resp.status_code == 200
        team.refresh_from_db()
        assert team.description == 'Nouvelle description'

    def test_transfer_ownership(self, db, client, superadmin, regular_user):
        other = User.objects.create_user('other_u', 'other@test.com', 'pass1234')
        team = Team.objects.create(name='Alpha', owner=regular_user)
        TeamMember.objects.create(team=team, user=regular_user, role='owner')
        TeamMember.objects.create(team=team, user=other, role='member')
        auth(client, superadmin)
        resp = client.post(reverse('admin_api:team-transfer', args=[team.slug]),
                           {'user_id': other.pk}, format='json')
        assert resp.status_code == 200
        team.refresh_from_db()
        assert team.owner == other
        assert TeamMember.objects.get(team=team, user=other).role == 'owner'
        assert TeamMember.objects.get(team=team, user=regular_user).role == 'admin'

    def test_transfer_requires_member(self, db, client, superadmin, regular_user):
        outsider = User.objects.create_user('out_u', 'out@test.com', 'pass1234')
        team = Team.objects.create(name='Alpha', owner=regular_user)
        auth(client, superadmin)
        resp = client.post(reverse('admin_api:team-transfer', args=[team.slug]),
                           {'user_id': outsider.pk}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_team(self, db, client, superadmin, regular_user):
        team = Team.objects.create(name='Alpha', owner=regular_user)
        auth(client, superadmin)
        resp = client.delete(reverse('admin_api:team-detail', args=[team.slug]))
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not Team.objects.filter(pk=team.pk).exists()


# ─────────────────────────────────────────────────────────────────────────────
# PROJECTS
# ─────────────────────────────────────────────────────────────────────────────
class TestProjects:
    def test_list_and_filter(self, db, client, superadmin, regular_user):
        team = Team.objects.create(name='Alpha', owner=regular_user)
        Project.objects.create(team=team, name='Avec SMOKE', status='active')
        Project.objects.create(team=team, name='Archive', status='archived', visibility='private')
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:projects'))
        assert resp.status_code == 200
        assert resp.data['count'] >= 2

        resp2 = client.get(reverse('admin_api:projects'), {'status': 'archived'})
        assert resp2.status_code == 200
        assert all(p['status'] == 'archived' for p in resp2.data['results'])

    def test_patch_and_delete(self, db, client, superadmin, regular_user):
        team = Team.objects.create(name='Alpha', owner=regular_user)
        project = Project.objects.create(team=team, name='Projet A')
        auth(client, superadmin)
        resp = client.patch(reverse('admin_api:project-detail', args=[project.pk]),
                            {'status': 'archived'}, format='json')
        assert resp.status_code == 200
        project.refresh_from_db()
        assert project.status == 'archived'

        resp2 = client.delete(reverse('admin_api:project-detail', args=[project.pk]))
        assert resp2.status_code == status.HTTP_204_NO_CONTENT
        assert not Project.objects.filter(pk=project.pk).exists()


# ─────────────────────────────────────────────────────────────────────────────
# JOBS & ACTIVITY
# ─────────────────────────────────────────────────────────────────────────────
class TestJobsAndActivity:
    def test_jobs_list(self, db, client, superadmin):
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:jobs'))
        assert resp.status_code == 200
        assert 'results' in resp.data

    def test_activity_list(self, db, client, superadmin, regular_user):
        ActivityLog.objects.create(actor=regular_user, action_type='test', description='Premier log')
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:activity'))
        assert resp.status_code == 200
        assert resp.data['count'] >= 1

    def test_activity_filter_action_type(self, db, client, superadmin, regular_user):
        ActivityLog.objects.create(actor=regular_user, action_type='sprint_created', description='A')
        ActivityLog.objects.create(actor=regular_user, action_type='defect_created', description='B')
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:activity'), {'action_type': 'sprint_created'})
        assert resp.status_code == 200
        assert all(a['action_type'] == 'sprint_created' for a in resp.data['results'])

    def test_admin_actions_are_logged(self, db, client, superadmin, regular_user):
        auth(client, superadmin)
        team = Team.objects.create(name='Alpha', owner=regular_user)
        client.delete(reverse('admin_api:team-detail', args=[team.slug]))
        assert ActivityLog.objects.filter(action_type='admin_team_deleted').exists()
        performed = set(ActivityLog.objects.filter(actor=superadmin)
                        .values_list('action_type', flat=True))
        assert 'admin_team_deleted' in performed


# ─────────────────────────────────────────────────────────────────────────────
# USE CASES / SPRINTS / DEFECTS / REPORTS (vue globale)
# ─────────────────────────────────────────────────────────────────────────────
class TestGlobalViews:
    @pytest.fixture
    def dataset(self, db, regular_user):
        user = User.objects.create_user('member_u', 'member@test.com', 'pass1234')
        team = Team.objects.create(name='Beta', owner=user)
        Project.objects.create(team=team, name='Projet Global')
        sprint = Sprint.objects.create(
            project=Project.objects.get(name='Projet Global'),
            name='Sprint G', start_date='2026-09-14', end_date='2026-09-21')
        Defect.objects.create(
            project=Project.objects.get(name='Projet Global'),
            title='Anomalie X', severity='critical', status='open')
        WeeklyReport.objects.create(
            user=regular_user, team=team,
            week_start='2026-09-14', week_end='2026-09-20', status='submitted')
        return {'team': team}

    def test_use_cases_list_and_filters(self, db, client, superadmin):
        job = ConversionJob.objects.create(
            source_filename='recette_test.docx', word_file='uploads/word/recette_test.docx')
        ExtractedUseCase.objects.create(
            job=job, order=1, use_case_text='Connecter un admin', status='Passé', is_automated=True)
        ExtractedUseCase.objects.create(
            job=job, order=2, use_case_text='Consulter profil', status='À tester', is_automated=False)
        auth(client, superadmin)

        resp = client.get(reverse('admin_api:use-cases'))
        assert resp.status_code == 200
        assert resp.data['count'] >= 2
        first = resp.data['results'][0]
        assert first['source_filename'] == 'recette_test.docx'

        resp2 = client.get(reverse('admin_api:use-cases'), {'status': 'Passé'})
        assert all(u['status'] == 'Passé' for u in resp2.data['results'])

        resp3 = client.get(reverse('admin_api:use-cases'), {'search': 'Connecter'})
        assert all('Connecter' in u['use_case_text'] for u in resp3.data['results'])

    def test_sprints_list(self, db, client, superadmin, dataset):
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:sprints'))
        assert resp.status_code == 200
        assert resp.data['count'] >= 1
        item = resp.data['results'][0]
        assert item['duration_days'] == 7
        assert 'stats' in item and 'project_name' in item

    def test_defects_list_and_filters(self, db, client, superadmin, dataset):
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:defects'))
        assert resp.status_code == 200
        assert resp.data['count'] >= 1
        first = resp.data['results'][0]
        assert first['title'] == 'Anomalie X'
        assert first['team_name'] == 'Beta'

        resp2 = client.get(reverse('admin_api:defects'), {'severity': 'critical'})
        assert all(d['severity'] == 'critical' for d in resp2.data['results'])

    def test_reports_list(self, db, client, superadmin, dataset):
        auth(client, superadmin)
        resp = client.get(reverse('admin_api:reports'))
        assert resp.status_code == 200
        assert resp.data['count'] >= 1
        first = resp.data['results'][0]
        assert first['user']['username'] == 'plain_u'
        assert first['team_name'] == 'Beta'
        assert first['status'] == 'submitted'

    def test_access_still_superuser_only(self, db, client, regular_user):
        auth(client, regular_user)
        assert client.get(reverse('admin_api:use-cases')).status_code == status.HTTP_403_FORBIDDEN