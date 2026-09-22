from django.contrib import admin
from .models import Team, TeamMember, TeamInvitation, Project, ProjectMember


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display  = ['name', 'slug', 'owner', 'created_at']
    search_fields = ['name', 'owner__username']
    prepopulated_fields = {}
    readonly_fields = ['id', 'slug', 'created_at', 'updated_at']


@admin.register(TeamMember)
class TeamMemberAdmin(admin.ModelAdmin):
    list_display  = ['user', 'team', 'role', 'joined_at']
    list_filter   = ['role']
    search_fields = ['user__username', 'team__name']


@admin.register(TeamInvitation)
class TeamInvitationAdmin(admin.ModelAdmin):
    list_display  = ['email', 'team', 'role', 'status', 'expires_at']
    list_filter   = ['status']
    readonly_fields = ['token', 'created_at']


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display  = ['name', 'team', 'visibility', 'status', 'created_by', 'created_at']
    list_filter   = ['visibility', 'status']
    search_fields = ['name', 'team__name']
    readonly_fields = ['id', 'slug', 'created_at', 'updated_at']


@admin.register(ProjectMember)
class ProjectMemberAdmin(admin.ModelAdmin):
    list_display  = ['user', 'project', 'role', 'added_at']
    list_filter   = ['role']
