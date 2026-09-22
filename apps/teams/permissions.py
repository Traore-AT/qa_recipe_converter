"""
Custom DRF permission classes for the Teams module.

Permission matrix:
  Action                      | Owner | Admin | Member | Viewer | Anon
  ----------------------------|-------|-------|--------|--------|-----
  Read project (public)       |  ✅   |  ✅   |  ✅    |  ✅    |  ❌
  Read project (team)         |  ✅   |  ✅   |  ✅    |  ✅    |  ❌
  Read project (private)      |  ✅   |  ✅   |  ❌    |  ❌    |  ❌
  Upload recette / gen Excel  |  ✅   |  ✅   |  ✅    |  ❌    |  ❌
  Modify project              |  ✅   |  ✅   |  ❌    |  ❌    |  ❌
  Invite members              |  ✅   |  ✅   |  ❌    |  ❌    |  ❌
  Delete project              |  ✅   |  ✅   |  ❌    |  ❌    |  ❌
  Manage team (settings)      |  ✅   |  ❌   |  ❌    |  ❌    |  ❌
"""

from rest_framework.permissions import BasePermission, IsAuthenticated
from .models import Team, TeamMember, Project, ProjectMember


def _get_team_role(user, team) -> str | None:
    """Return role string for user in team, or None if not a member."""
    if not user.is_authenticated:
        return None
    if team.owner == user:
        return TeamMember.Role.OWNER
    m = TeamMember.objects.filter(team=team, user=user).first()
    return m.role if m else None


def _get_project_role(user, project) -> str | None:
    """Return project role for user, or None."""
    if not user.is_authenticated:
        return None
    m = ProjectMember.objects.filter(project=project, user=user).first()
    return m.role if m else None


# ─────────────────────────────────────────────────────────────────────────────
# Generic helpers used by views
# ─────────────────────────────────────────────────────────────────────────────

def can_view_project(user, project) -> bool:
    """Can the user read this project at all?"""
    if not user.is_authenticated:
        return False
    team_role = _get_team_role(user, project.team)
    if team_role in ('owner', 'admin'):
        return True
    if project.visibility == Project.Visibility.PUBLIC:
        return team_role is not None  # any team member
    if project.visibility == Project.Visibility.TEAM:
        return team_role in ('member', 'viewer', 'admin', 'owner')
    if project.visibility == Project.Visibility.PRIVATE:
        proj_role = _get_project_role(user, project)
        return proj_role is not None
    return False


def can_write_project(user, project) -> bool:
    """Member-level write: can upload/generate. Archived projects block write."""
    if not user.is_authenticated:
        return False
    if project.is_archived:
        return False
    team_role = _get_team_role(user, project.team)
    return team_role in ('owner', 'admin', 'member')


def can_manage_project(user, project) -> bool:
    """Can the user modify/delete/invite on the project?"""
    if not user.is_authenticated:
        return False
    team_role = _get_team_role(user, project.team)
    return team_role in ('owner', 'admin')


def can_manage_team(user, team) -> bool:
    """Only owner can manage team settings."""
    return user.is_authenticated and team.owner == user


# ─────────────────────────────────────────────────────────────────────────────
# DRF Permission classes
# ─────────────────────────────────────────────────────────────────────────────

class IsTeamMember(BasePermission):
    """Request user must be any member of the team."""
    message = "Vous devez être membre de cette équipe."

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        team = getattr(view, 'team', None)
        if team is None:
            return True
        return team.is_member(request.user)


class IsTeamAdmin(BasePermission):
    """Request user must be owner or admin of the team."""
    message = "Vous devez être administrateur de cette équipe."

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        team = getattr(view, 'team', None)
        if team is None:
            return True
        return team.is_admin(request.user)


class IsTeamOwner(BasePermission):
    """Request user must be the team owner."""
    message = "Seul le propriétaire peut effectuer cette action."

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        team = getattr(view, 'team', None)
        if team is None:
            return True
        return team.is_owner(request.user)


class IsProjectLead(BasePermission):
    """Request user must be project lead or team owner/admin."""
    message = "Vous devez être lead QA ou admin de l'équipe."

    def has_object_permission(self, request, view, obj):
        project = obj if isinstance(obj, Project) else getattr(obj, 'project', None)
        if not project:
            return False
        return can_manage_project(request.user, project)


class CanWriteProject(BasePermission):
    """Member-level write: can upload/generate. Archived = read-only."""
    message = "Seuls les membres actifs peuvent uploader des fichiers."

    def has_object_permission(self, request, view, obj):
        project = obj if isinstance(obj, Project) else getattr(obj, 'project', None)
        if not project:
            return False
        if project.is_archived:
            self.message = "Ce projet est archivé (lecture seule)."
            return False
        return can_write_project(request.user, project)
