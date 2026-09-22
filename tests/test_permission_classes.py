import sys
import pytest
from django.contrib.auth.models import User, AnonymousUser
from django.test import RequestFactory

from apps.teams.models import Team, TeamMember, Project, ProjectMember
from apps.teams.permissions import (
    IsTeamMember, IsTeamAdmin, IsTeamOwner, IsProjectLead, CanWriteProject,
    _get_team_role, _get_project_role,
)

PY314 = sys.version_info >= (3, 14)
skip_if_py314 = pytest.mark.skipif(PY314, reason='Django 5.0.6 incompatible avec Python 3.14')


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
def project(db, team, owner):
    p = Project.objects.create(team=team, name='Sprint', created_by=owner, visibility='team')
    ProjectMember.objects.create(project=p, user=owner, role='lead')
    return p

@pytest.fixture
def rf():
    return RequestFactory()


class TestHelperFunctions:
    def test_get_team_role_owner(self, owner, team):
        assert _get_team_role(owner, team) == 'owner'

    def test_get_team_role_admin(self, admin_user, team):
        assert _get_team_role(admin_user, team) == 'admin'

    def test_get_team_role_member(self, member_user, team):
        assert _get_team_role(member_user, team) == 'member'

    def test_get_team_role_viewer(self, viewer_user, team):
        assert _get_team_role(viewer_user, team) == 'viewer'

    def test_get_team_role_outsider(self, outsider, team):
        assert _get_team_role(outsider, team) is None

    def test_get_team_role_unauthenticated(self, team):
        anon = User()  # not saved, is_authenticated will be False by default
        from django.contrib.auth.models import AnonymousUser
        assert _get_team_role(AnonymousUser(), team) is None

    def test_get_project_role_lead(self, owner, project):
        assert _get_project_role(owner, project) == 'lead'

    def test_get_project_role_none(self, member_user, project):
        assert _get_project_role(member_user, project) is None

    def test_get_project_role_unauthenticated(self, project):
        from django.contrib.auth.models import AnonymousUser
        assert _get_project_role(AnonymousUser(), project) is None


class FakeView:
    pass

class TestIsTeamMember:
    def make_request(self, user):
        req = RequestFactory().get('/')
        req.user = user
        return req

    def _view_with_team(self, team):
        v = FakeView()
        v.team = team
        return v

    def test_owner_is_member(self, owner, team):
        perm = IsTeamMember()
        req = self.make_request(owner)
        assert perm.has_permission(req, self._view_with_team(team)) is True

    def test_member_is_member(self, member_user, team):
        perm = IsTeamMember()
        req = self.make_request(member_user)
        assert perm.has_permission(req, self._view_with_team(team)) is True

    def test_outsider_not_member(self, outsider, team):
        perm = IsTeamMember()
        req = self.make_request(outsider)
        assert perm.has_permission(req, self._view_with_team(team)) is False

    def test_unauthenticated_not_member(self, team):
        perm = IsTeamMember()
        req = self.make_request(AnonymousUser())
        assert perm.has_permission(req, self._view_with_team(team)) is False

    def test_no_team_returns_true(self, owner):
        perm = IsTeamMember()
        req = self.make_request(owner)
        assert perm.has_permission(req, FakeView()) is True


class TestIsTeamAdmin:
    def make_request(self, user):
        req = RequestFactory().get('/')
        req.user = user
        return req

    def _view_with_team(self, team):
        v = FakeView()
        v.team = team
        return v

    def test_owner_is_admin(self, owner, team):
        perm = IsTeamAdmin()
        req = self.make_request(owner)
        assert perm.has_permission(req, self._view_with_team(team)) is True

    def test_admin_is_admin(self, admin_user, team):
        perm = IsTeamAdmin()
        req = self.make_request(admin_user)
        assert perm.has_permission(req, self._view_with_team(team)) is True

    def test_member_not_admin(self, member_user, team):
        perm = IsTeamAdmin()
        req = self.make_request(member_user)
        assert perm.has_permission(req, self._view_with_team(team)) is False

    def test_viewer_not_admin(self, viewer_user, team):
        perm = IsTeamAdmin()
        req = self.make_request(viewer_user)
        assert perm.has_permission(req, self._view_with_team(team)) is False

    def test_unauthenticated_not_admin(self, team):
        perm = IsTeamAdmin()
        req = self.make_request(AnonymousUser())
        assert perm.has_permission(req, self._view_with_team(team)) is False


class TestIsTeamOwner:
    def make_request(self, user):
        req = RequestFactory().get('/')
        req.user = user
        return req

    def _view_with_team(self, team):
        v = FakeView()
        v.team = team
        return v

    def test_owner_is_owner(self, owner, team):
        perm = IsTeamOwner()
        req = self.make_request(owner)
        assert perm.has_permission(req, self._view_with_team(team)) is True

    def test_admin_not_owner(self, admin_user, team):
        perm = IsTeamOwner()
        req = self.make_request(admin_user)
        assert perm.has_permission(req, self._view_with_team(team)) is False

    def test_member_not_owner(self, member_user, team):
        perm = IsTeamOwner()
        req = self.make_request(member_user)
        assert perm.has_permission(req, self._view_with_team(team)) is False


class TestIsProjectLead:
    def make_request(self, user):
        req = RequestFactory().get('/')
        req.user = user
        return req

    def test_owner_can_manage(self, owner, project):
        perm = IsProjectLead()
        req = self.make_request(owner)
        assert perm.has_object_permission(req, None, project) is True

    def test_admin_can_manage(self, admin_user, project, team):
        perm = IsProjectLead()
        req = self.make_request(admin_user)
        assert perm.has_object_permission(req, None, project) is True

    def test_member_cannot_manage(self, member_user, project):
        perm = IsProjectLead()
        req = self.make_request(member_user)
        assert perm.has_object_permission(req, None, project) is False

    def test_viewer_cannot_manage(self, viewer_user, project):
        perm = IsProjectLead()
        req = self.make_request(viewer_user)
        assert perm.has_object_permission(req, None, project) is False

    def test_no_project_returns_false(self, owner):
        perm = IsProjectLead()
        req = self.make_request(owner)
        assert perm.has_object_permission(req, None, None) is False


class TestCanWriteProject:
    def make_request(self, user):
        req = RequestFactory().get('/')
        req.user = user
        return req

    def test_member_can_write(self, member_user, project):
        perm = CanWriteProject()
        req = self.make_request(member_user)
        assert perm.has_object_permission(req, None, project) is True

    def test_viewer_cannot_write(self, viewer_user, project):
        perm = CanWriteProject()
        req = self.make_request(viewer_user)
        assert perm.has_object_permission(req, None, project) is False

    def test_archived_blocks_write(self, owner, project):
        project.status = 'archived'; project.save()
        perm = CanWriteProject()
        req = self.make_request(owner)
        assert perm.has_object_permission(req, None, project) is False

    def test_no_project_returns_false(self, owner):
        perm = CanWriteProject()
        req = self.make_request(owner)
        assert perm.has_object_permission(req, None, None) is False

    def test_unauthenticated_cannot_write(self, project):
        perm = CanWriteProject()
        from django.contrib.auth.models import AnonymousUser
        req = self.make_request(AnonymousUser())
        assert perm.has_object_permission(req, None, project) is False
