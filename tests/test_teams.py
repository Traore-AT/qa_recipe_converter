"""
Tests unitaires — Module Teams

Couvre :
- Modèles Team / TeamMember / TeamInvitation / Project / ProjectMember
- Permissions (matrice complète)
- API endpoints (via APIClient DRF)
- Slug auto-génération
- Règles métier (owner ne peut pas quitter, archivé = readonly, etc.)
"""
import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from apps.teams.models import Team, TeamMember, TeamInvitation, Project, ProjectMember
from apps.teams.permissions import (
    can_view_project, can_write_project, can_manage_project, can_manage_team
)


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def owner(db):
    return User.objects.create_user('owner_u', 'owner@test.com', 'pass1234')

@pytest.fixture
def admin_user(db):
    return User.objects.create_user('admin_u', 'admin@test.com', 'pass1234')

@pytest.fixture
def member_user(db):
    return User.objects.create_user('member_u', 'member@test.com', 'pass1234')

@pytest.fixture
def viewer_user(db):
    return User.objects.create_user('viewer_u', 'viewer@test.com', 'pass1234')

@pytest.fixture
def outsider(db):
    return User.objects.create_user('outsider_u', 'outsider@test.com', 'pass1234')

@pytest.fixture
def team(db, owner, admin_user, member_user, viewer_user):
    t = Team.objects.create(name='Alpha QA Team', owner=owner)
    TeamMember.objects.create(team=t, user=owner, role='owner')
    TeamMember.objects.create(team=t, user=admin_user, role='admin')
    TeamMember.objects.create(team=t, user=member_user, role='member')
    TeamMember.objects.create(team=t, user=viewer_user, role='viewer')
    return t

@pytest.fixture
def public_project(db, team, owner):
    p = Project.objects.create(
        team=team, name='Public Sprint', created_by=owner, visibility='public'
    )
    ProjectMember.objects.create(project=p, user=owner, role='lead')
    return p

@pytest.fixture
def team_project(db, team, owner):
    p = Project.objects.create(
        team=team, name='Team Sprint', created_by=owner, visibility='team'
    )
    ProjectMember.objects.create(project=p, user=owner, role='lead')
    return p

@pytest.fixture
def private_project(db, team, owner):
    p = Project.objects.create(
        team=team, name='Private Sprint', created_by=owner, visibility='private'
    )
    ProjectMember.objects.create(project=p, user=owner, role='lead')
    return p

@pytest.fixture
def client():
    return APIClient()

def auth(client, user):
    client.force_authenticate(user=user)
    return client


# ─────────────────────────────────────────────────────────────────────────────
# TEAM MODEL TESTS
# ─────────────────────────────────────────────────────────────────────────────
class TestTeamModel:
    def test_slug_auto_generated(self, db, owner):
        t = Team.objects.create(name='My QA Team', owner=owner)
        assert t.slug == 'my-qa-team'

    def test_slug_unique_collision(self, db, owner):
        Team.objects.create(name='My Team', owner=owner)
        t2 = Team.objects.create(name='My Team', owner=owner)
        assert t2.slug == 'my-team-1'

    def test_slug_handles_accents(self, db, owner):
        t = Team.objects.create(name='Équipe Qualité', owner=owner)
        assert 'equipe' in t.slug

    def test_is_owner(self, team, owner, member_user):
        assert team.is_owner(owner) is True
        assert team.is_owner(member_user) is False

    def test_is_admin(self, team, owner, admin_user, member_user):
        assert team.is_admin(owner) is True
        assert team.is_admin(admin_user) is True
        assert team.is_admin(member_user) is False

    def test_is_member(self, team, owner, outsider):
        assert team.is_member(owner) is True
        assert team.is_member(outsider) is False

    def test_str(self, team):
        assert str(team) == 'Alpha QA Team'


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT MODEL TESTS
# ─────────────────────────────────────────────────────────────────────────────
class TestProjectModel:
    def test_slug_auto_generated(self, team_project):
        assert team_project.slug == 'team-sprint'

    def test_is_archived(self, team_project):
        assert team_project.is_archived is False
        team_project.status = 'archived'
        team_project.save()
        assert team_project.is_archived is True

    def test_stats_empty(self, team_project):
        s = team_project.stats
        assert s['total'] == 0
        assert s['success_rate'] == 0

    def test_str(self, team_project, team):
        assert 'Alpha QA Team' in str(team_project)
        assert 'Team Sprint' in str(team_project)


# ─────────────────────────────────────────────────────────────────────────────
# INVITATION MODEL TESTS
# ─────────────────────────────────────────────────────────────────────────────
class TestInvitationModel:
    def test_usable_when_pending(self, team, owner):
        inv = TeamInvitation.objects.create(team=team, invited_by=owner, email='x@x.com', role='member')
        assert inv.is_usable is True

    def test_not_usable_when_accepted(self, team, owner):
        inv = TeamInvitation.objects.create(team=team, invited_by=owner, email='y@y.com', role='member')
        inv.status = 'accepted'
        inv.save()
        assert inv.is_usable is False

    def test_expires_at_auto_set(self, team, owner):
        inv = TeamInvitation.objects.create(team=team, invited_by=owner, email='z@z.com', role='member')
        assert inv.expires_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# PERMISSION MATRIX TESTS
# ─────────────────────────────────────────────────────────────────────────────
class TestPermissions:
    # ── PUBLIC project ─────────────────────────────────────────────────────
    def test_public_owner_can_view(self, owner, public_project):
        assert can_view_project(owner, public_project) is True

    def test_public_member_can_view(self, member_user, public_project):
        assert can_view_project(member_user, public_project) is True

    def test_public_viewer_can_view(self, viewer_user, public_project):
        assert can_view_project(viewer_user, public_project) is True

    def test_public_outsider_cannot_view(self, outsider, public_project):
        # outsider not in team → False
        assert can_view_project(outsider, public_project) is False

    # ── TEAM project ───────────────────────────────────────────────────────
    def test_team_project_member_can_view(self, member_user, team_project):
        assert can_view_project(member_user, team_project) is True

    def test_team_project_viewer_can_view(self, viewer_user, team_project):
        assert can_view_project(viewer_user, team_project) is True

    def test_team_project_outsider_cannot_view(self, outsider, team_project):
        assert can_view_project(outsider, team_project) is False

    # ── PRIVATE project ────────────────────────────────────────────────────
    def test_private_owner_can_view(self, owner, private_project):
        assert can_view_project(owner, private_project) is True

    def test_private_admin_can_view(self, admin_user, private_project):
        assert can_view_project(admin_user, private_project) is True

    def test_private_member_cannot_view(self, member_user, private_project):
        # member not in ProjectMember → cannot view private
        assert can_view_project(member_user, private_project) is False

    def test_private_viewer_cannot_view(self, viewer_user, private_project):
        assert can_view_project(viewer_user, private_project) is False

    # ── WRITE ──────────────────────────────────────────────────────────────
    def test_owner_can_write(self, owner, team_project):
        assert can_write_project(owner, team_project) is True

    def test_admin_can_write(self, admin_user, team_project):
        assert can_write_project(admin_user, team_project) is True

    def test_member_can_write(self, member_user, team_project):
        assert can_write_project(member_user, team_project) is True

    def test_viewer_cannot_write(self, viewer_user, team_project):
        assert can_write_project(viewer_user, team_project) is False

    def test_outsider_cannot_write(self, outsider, team_project):
        assert can_write_project(outsider, team_project) is False

    def test_archived_blocks_write(self, owner, team_project):
        team_project.status = 'archived'; team_project.save()
        assert can_write_project(owner, team_project) is False

    # ── MANAGE ─────────────────────────────────────────────────────────────
    def test_owner_can_manage(self, owner, team_project):
        assert can_manage_project(owner, team_project) is True

    def test_admin_can_manage(self, admin_user, team_project):
        assert can_manage_project(admin_user, team_project) is True

    def test_member_cannot_manage(self, member_user, team_project):
        assert can_manage_project(member_user, team_project) is False

    # ── TEAM MANAGEMENT ────────────────────────────────────────────────────
    def test_owner_can_manage_team(self, owner, team):
        assert can_manage_team(owner, team) is True

    def test_admin_cannot_manage_team(self, admin_user, team):
        assert can_manage_team(admin_user, team) is False


# ─────────────────────────────────────────────────────────────────────────────
# API ENDPOINT TESTS
# ─────────────────────────────────────────────────────────────────────────────
class TestAuthAPI:
    def test_register_success(self, db, client):
        resp = client.post('/api/auth/register/', {
            'username': 'newuser', 'email': 'new@test.com',
            'password': 'securepass1'
        }, format='json')
        assert resp.status_code == 201
        assert resp.data['username'] == 'newuser'

    def test_register_duplicate_username(self, db, owner, client):
        resp = client.post('/api/auth/register/', {
            'username': 'owner_u', 'email': 'other@test.com',
            'password': 'pass1234'
        }, format='json')
        assert resp.status_code == 400

    def test_login_success(self, db, owner, client):
        resp = client.post('/api/auth/login/', {
            'username': 'owner_u', 'password': 'pass1234'
        }, format='json')
        assert resp.status_code == 200
        assert 'username' in resp.data

    def test_login_by_email(self, db, owner, client):
        resp = client.post('/api/auth/login/', {
            'username': 'owner@test.com', 'password': 'pass1234'
        }, format='json')
        assert resp.status_code == 200

    def test_login_bad_password(self, db, owner, client):
        resp = client.post('/api/auth/login/', {
            'username': 'owner_u', 'password': 'wrongpass'
        }, format='json')
        assert resp.status_code == 401

    def test_me_authenticated(self, db, owner, client):
        auth(client, owner)
        resp = client.get('/api/auth/me/')
        assert resp.status_code == 200
        assert resp.data['username'] == 'owner_u'

    def test_me_unauthenticated(self, db, client):
        resp = client.get('/api/auth/me/')
        assert resp.status_code in (401, 403)


class TestTeamAPI:
    def test_list_teams_for_member(self, client, owner, team):
        auth(client, owner)
        resp = client.get('/api/teams/')
        assert resp.status_code == 200
        assert resp.data['count'] >= 1
        slugs = [t['slug'] for t in resp.data['results']]
        assert 'alpha-qa-team' in slugs

    def test_list_teams_unauthenticated(self, client, team):
        resp = client.get('/api/teams/')
        assert resp.status_code in (401, 403)

    def test_create_team(self, db, client, owner):
        auth(client, owner)
        resp = client.post('/api/teams/', {'name': 'New Team', 'description': 'Desc'}, format='json')
        assert resp.status_code == 201
        assert resp.data['slug'] == 'new-team'
        # Owner auto-added as member
        t = Team.objects.get(slug='new-team')
        assert TeamMember.objects.filter(team=t, user=owner, role='owner').exists()

    def test_get_team_detail(self, client, owner, team):
        auth(client, owner)
        resp = client.get(f'/api/teams/{team.slug}/')
        assert resp.status_code == 200
        assert resp.data['name'] == 'Alpha QA Team'

    def test_outsider_cannot_access_team(self, client, outsider, team):
        auth(client, outsider)
        resp = client.get(f'/api/teams/{team.slug}/')
        assert resp.status_code == 403

    def test_patch_team_as_admin(self, client, admin_user, team):
        auth(client, admin_user)
        resp = client.patch(f'/api/teams/{team.slug}/', {'name': 'Updated Name'}, format='json')
        assert resp.status_code == 200
        assert resp.data['name'] == 'Updated Name'

    def test_member_cannot_patch_team(self, client, member_user, team):
        auth(client, member_user)
        resp = client.patch(f'/api/teams/{team.slug}/', {'name': 'Hacked'}, format='json')
        assert resp.status_code == 403

    def test_delete_team_as_owner(self, client, owner, team):
        auth(client, owner)
        resp = client.delete(f'/api/teams/{team.slug}/')
        assert resp.status_code == 204

    def test_delete_team_as_admin_forbidden(self, client, admin_user, team):
        auth(client, admin_user)
        resp = client.delete(f'/api/teams/{team.slug}/')
        assert resp.status_code == 403


class TestTeamMemberAPI:
    def test_list_members(self, client, owner, team):
        auth(client, owner)
        resp = client.get(f'/api/teams/{team.slug}/members/')
        assert resp.status_code == 200
        assert resp.data['count'] == 4  # owner, admin, member, viewer

    def test_invite_member(self, client, owner, team, outsider):
        auth(client, owner)
        resp = client.post(f'/api/teams/{team.slug}/members/', {
            'email': 'charlie_new@test.com', 'role': 'member'
        }, format='json')
        assert resp.status_code == 201
        assert resp.data['email'] == 'charlie_new@test.com'

    def test_invite_existing_member_fails(self, client, owner, team, member_user):
        auth(client, owner)
        resp = client.post(f'/api/teams/{team.slug}/members/', {
            'email': 'member@test.com', 'role': 'member'
        }, format='json')
        assert resp.status_code == 400

    def test_member_cannot_invite(self, client, member_user, team):
        auth(client, member_user)
        resp = client.post(f'/api/teams/{team.slug}/members/', {
            'email': 'new@test.com', 'role': 'viewer'
        }, format='json')
        assert resp.status_code == 403

    def test_change_role(self, client, owner, team, member_user):
        auth(client, owner)
        resp = client.patch(f'/api/teams/{team.slug}/members/{member_user.id}/',
                            {'role': 'admin'}, format='json')
        assert resp.status_code == 200
        assert resp.data['role'] == 'admin'

    def test_cannot_change_owner_role(self, client, owner, team):
        auth(client, owner)
        resp = client.patch(f'/api/teams/{team.slug}/members/{owner.id}/',
                            {'role': 'member'}, format='json')
        assert resp.status_code == 400

    def test_remove_member(self, client, owner, team, viewer_user):
        auth(client, owner)
        resp = client.delete(f'/api/teams/{team.slug}/members/{viewer_user.id}/')
        assert resp.status_code == 204

    def test_owner_cannot_leave(self, client, owner, team):
        auth(client, owner)
        resp = client.delete(f'/api/teams/{team.slug}/members/{owner.id}/')
        assert resp.status_code == 400


class TestProjectAPI:
    def test_create_project_as_owner(self, client, owner, team):
        auth(client, owner)
        resp = client.post(f'/api/teams/{team.slug}/projects/', {
            'name': 'New Project', 'visibility': 'team', 'icon': '🚀', 'color': '#3B82F6'
        }, format='json')
        assert resp.status_code == 201
        assert resp.data['slug'] == 'new-project'
        # Creator auto-added as lead
        p = Project.objects.get(slug='new-project')
        assert ProjectMember.objects.filter(project=p, user=owner, role='lead').exists()

    def test_member_cannot_create_project(self, client, member_user, team):
        auth(client, member_user)
        resp = client.post(f'/api/teams/{team.slug}/projects/', {
            'name': 'Forbidden', 'visibility': 'team'
        }, format='json')
        assert resp.status_code == 403

    def test_list_projects_visibility(self, client, member_user, team,
                                       public_project, team_project, private_project):
        auth(client, member_user)
        resp = client.get(f'/api/teams/{team.slug}/projects/')
        assert resp.status_code == 200
        slugs = [p['slug'] for p in resp.data['results']]
        # member sees public + team, not private
        assert 'public-sprint' in slugs
        assert 'team-sprint' in slugs
        assert 'private-sprint' not in slugs

    def test_owner_sees_private_project(self, client, owner, team, private_project):
        auth(client, owner)
        resp = client.get(f'/api/teams/{team.slug}/projects/')
        assert resp.status_code == 200
        slugs = [p['slug'] for p in resp.data['results']]
        assert 'private-sprint' in slugs

    def test_patch_project_as_admin(self, client, admin_user, team, team_project):
        auth(client, admin_user)
        resp = client.patch(
            f'/api/teams/{team.slug}/projects/{team_project.slug}/',
            {'name': 'Updated', 'status': 'completed'}, format='json'
        )
        assert resp.status_code == 200
        assert resp.data['status'] == 'completed'

    def test_archived_project_readonly(self, client, owner, team, team_project):
        team_project.status = 'archived'; team_project.save()
        auth(client, owner)
        resp = client.patch(
            f'/api/teams/{team.slug}/projects/{team_project.slug}/',
            {'name': 'Edit'}, format='json'
        )
        assert resp.status_code == 400

    def test_delete_project(self, client, owner, team, team_project):
        auth(client, owner)
        resp = client.delete(f'/api/teams/{team.slug}/projects/{team_project.slug}/')
        assert resp.status_code == 204


class TestDashboardAPI:
    def test_dashboard_returns_all_sections(self, client, owner, team,
                                             team_project, public_project):
        auth(client, owner)
        resp = client.get(f'/api/teams/{team.slug}/dashboard/')
        assert resp.status_code == 200
        d = resp.data
        assert 'team' in d
        assert 'stats' in d
        assert 'projects' in d
        assert 'members' in d
        assert 'recent_activity' in d
        assert d['stats']['projects_total'] >= 2
        assert d['stats']['members_count'] == 4

    def test_outsider_cannot_access_dashboard(self, client, outsider, team):
        auth(client, outsider)
        resp = client.get(f'/api/teams/{team.slug}/dashboard/')
        assert resp.status_code == 403


class TestInvitationAPI:
    def test_accept_invitation(self, db, client, team, owner):
        new_user = User.objects.create_user('invited', 'invited@test.com', 'pass1234')
        inv = TeamInvitation.objects.create(
            team=team, invited_by=owner, email='invited@test.com', role='member'
        )
        auth(client, new_user)
        resp = client.post(f'/api/teams/invitations/{inv.token}/accept/')
        assert resp.status_code == 200
        assert TeamMember.objects.filter(team=team, user=new_user).exists()
        inv.refresh_from_db()
        assert inv.status == 'accepted'

    def test_wrong_email_cannot_accept(self, db, client, team, owner, outsider):
        inv = TeamInvitation.objects.create(
            team=team, invited_by=owner, email='someone_else@test.com', role='member'
        )
        auth(client, outsider)  # outsider@test.com ≠ someone_else@test.com
        resp = client.post(f'/api/teams/invitations/{inv.token}/accept/')
        assert resp.status_code == 403

    def test_reject_invitation(self, db, client, team, owner):
        user = User.objects.create_user('rej_user', 'rej@test.com', 'pass1234')
        inv  = TeamInvitation.objects.create(
            team=team, invited_by=owner, email='rej@test.com', role='viewer'
        )
        auth(client, user)
        resp = client.post(f'/api/teams/invitations/{inv.token}/reject/')
        assert resp.status_code == 200
        inv.refresh_from_db()
        assert inv.status == 'rejected'
