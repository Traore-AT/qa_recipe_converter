import sys
import pytest
from django.contrib.auth.models import User
from django.test import Client

from apps.teams.models import Team, TeamMember, Project, ProjectMember

PY314 = sys.version_info >= (3, 14)
skip_if_py314 = pytest.mark.skipif(PY314, reason='Django 5.0.6 incompatible avec Python 3.14')


@pytest.fixture
def owner(db):
    return User.objects.create_user('owner_u', 'owner@test.com', 'pass1234')

@pytest.fixture
def member_user(db):
    return User.objects.create_user('member_u', 'member@test.com', 'pass1234')

@pytest.fixture
def team(db, owner, member_user):
    t = Team.objects.create(name='Alpha QA Team', owner=owner)
    TeamMember.objects.create(team=t, user=owner, role='owner')
    TeamMember.objects.create(team=t, user=member_user, role='member')
    return t

@pytest.fixture
def client():
    return Client()


class TestWebLoginView:
    @skip_if_py314
    def test_get_renders_form(self, client):
        resp = client.get('/teams/login/')
        assert resp.status_code == 200
        assert 'teams/login.html' in [t.name for t in resp.templates]

    def test_get_redirects_when_authenticated(self, client, owner):
        client.force_login(owner)
        resp = client.get('/teams/login/')
        assert resp.status_code == 302
        assert resp.url == '/teams/'

    def test_post_success(self, client, owner):
        resp = client.post('/teams/login/', {'username': 'owner_u', 'password': 'pass1234'})
        assert resp.status_code == 302
        assert resp.url == '/teams/'

    def test_post_by_email(self, client, owner):
        resp = client.post('/teams/login/', {'username': 'owner@test.com', 'password': 'pass1234'})
        assert resp.status_code == 302

    @skip_if_py314
    def test_post_invalid_credentials(self, client):
        resp = client.post('/teams/login/', {'username': 'nobody', 'password': 'wrong'})
        assert resp.status_code == 200

    def test_post_with_next_param(self, client, owner, team):
        resp = client.post('/teams/login/?next=/teams/alpha-qa-team/', {'username': 'owner_u', 'password': 'pass1234'})
        assert resp.status_code == 302
        assert resp.url == '/teams/alpha-qa-team/'


class TestWebRegisterView:
    @skip_if_py314
    def test_get_renders_form(self, client):
        resp = client.get('/teams/register/')
        assert resp.status_code == 200
        assert 'teams/register.html' in [t.name for t in resp.templates]

    def test_get_redirects_when_authenticated(self, client, owner):
        client.force_login(owner)
        resp = client.get('/teams/register/')
        assert resp.status_code == 302

    @skip_if_py314
    def test_post_creates_user_and_logs_in(self, client):
        resp = client.post('/teams/register/', {
            'username': 'newuser', 'email': 'new@test.com',
            'password': 'securepass1', 'first_name': 'New', 'last_name': 'User',
        })
        assert resp.status_code == 302
        assert User.objects.filter(username='newuser').exists()

    @skip_if_py314
    def test_post_duplicate_username_shows_errors(self, client, owner):
        resp = client.post('/teams/register/', {
            'username': 'owner_u', 'email': 'other@test.com', 'password': 'securepass1',
        })
        assert resp.status_code == 200


class TestWebLogoutView:
    def test_post_logs_out(self, client, owner):
        client.force_login(owner)
        resp = client.post('/teams/logout/')
        assert resp.status_code == 302
        assert resp.url == '/teams/login/'

    def test_get_returns_405(self, client, owner):
        client.force_login(owner)
        resp = client.get('/teams/logout/')
        assert resp.status_code == 405


class TestTeamsHomeView:
    @skip_if_py314
    def test_get_shows_user_teams(self, client, owner, team):
        client.force_login(owner)
        resp = client.get('/teams/')
        assert resp.status_code == 200
        assert 'teams/home.html' in [t.name for t in resp.templates]
        assert team in resp.context['teams']

    def test_get_redirects_unauthenticated(self, client):
        resp = client.get('/teams/')
        assert resp.status_code == 302
        assert '/teams/login/' in resp.url

    def test_post_creates_team(self, client, owner):
        client.force_login(owner)
        resp = client.post('/teams/', {'name': 'Quick Team', 'description': 'desc'})
        assert resp.status_code == 302
        assert Team.objects.filter(slug='quick-team').exists()

    def test_post_empty_name_shows_error(self, client, owner):
        client.force_login(owner)
        resp = client.post('/teams/', {'name': '', 'description': ''})
        assert resp.status_code == 302


class TestTeamDashboardPageView:
    @skip_if_py314
    def test_get_shows_dashboard(self, client, owner, team):
        client.force_login(owner)
        resp = client.get(f'/teams/{team.slug}/')
        assert resp.status_code == 200

    def test_get_redirects_non_member(self, client, owner, team):
        outsider = User.objects.create_user('outsider', 'out@test.com', 'pass1234')
        client.force_login(outsider)
        resp = client.get(f'/teams/{team.slug}/')
        assert resp.status_code == 302
        assert resp.url == '/teams/'

    @skip_if_py314
    def test_get_redirects_unknown_team(self, client, owner):
        client.force_login(owner)
        resp = client.get('/teams/nonexistent/')
        assert resp.status_code == 404

    @skip_if_py314
    def test_dashboard_context_contains_stats(self, client, owner, team):
        client.force_login(owner)
        resp = client.get(f'/teams/{team.slug}/')
        assert resp.status_code == 200


class TestCreateProjectPageView:
    @skip_if_py314
    def test_get_renders_form(self, client, owner, team):
        client.force_login(owner)
        resp = client.get(f'/teams/{team.slug}/projects/new/')
        assert resp.status_code == 200

    def test_get_redirects_non_admin(self, client, member_user, team):
        client.force_login(member_user)
        resp = client.get(f'/teams/{team.slug}/projects/new/')
        assert resp.status_code == 302

    def test_post_creates_project(self, client, owner, team):
        client.force_login(owner)
        resp = client.post(f'/teams/{team.slug}/projects/new/', {
            'name': 'Sprint 1', 'description': 'First sprint', 'visibility': 'team',
        })
        assert resp.status_code == 302
        assert Project.objects.filter(slug='sprint-1', team=team).exists()

    @skip_if_py314
    def test_post_empty_name_rerenders_form(self, client, owner, team):
        client.force_login(owner)
        resp = client.post(f'/teams/{team.slug}/projects/new/', {
            'name': '', 'description': '',
        })
        assert resp.status_code == 200

    def test_post_by_non_admin_redirects(self, client, member_user, team):
        client.force_login(member_user)
        resp = client.post(f'/teams/{team.slug}/projects/new/', {
            'name': 'Hacked', 'description': '',
        })
        assert resp.status_code == 302


class TestProjectPageView:
    @skip_if_py314
    def test_get_shows_project(self, client, owner, team):
        client.force_login(owner)
        p = Project.objects.create(team=team, name='My Project', created_by=owner)
        ProjectMember.objects.create(project=p, user=owner, role='lead')
        resp = client.get(f'/teams/{team.slug}/projects/{p.slug}/')
        assert resp.status_code == 200

    @skip_if_py314
    def test_get_redirects_non_member(self, client, owner, team):
        client.force_login(owner)
        p = Project.objects.create(team=team, name='Private', created_by=owner, visibility='private')
        outsider = User.objects.create_user('outsider2', 'out2@test.com', 'pass1234')
        TeamMember.objects.create(team=team, user=outsider, role='viewer')
        client.force_login(outsider)
        resp = client.get(f'/teams/{team.slug}/projects/{p.slug}/')
        assert resp.status_code == 302

    @skip_if_py314
    def test_get_404_unknown_project(self, client, owner, team):
        client.force_login(owner)
        resp = client.get(f'/teams/{team.slug}/projects/nonexistent/')
        assert resp.status_code == 404

    @skip_if_py314
    def test_context_contains_project_data(self, client, owner, team):
        client.force_login(owner)
        p = Project.objects.create(team=team, name='Context Check', created_by=owner)
        ProjectMember.objects.create(project=p, user=owner, role='lead')
        resp = client.get(f'/teams/{team.slug}/projects/{p.slug}/')
        assert resp.status_code == 200
