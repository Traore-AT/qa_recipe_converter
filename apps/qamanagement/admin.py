from django.contrib import admin
from .models import (
    Sprint, UseCaseAssignment, UseCaseExecution, Defect, DefectComment, WeeklyReport, ActivityLog,
    UseCaseComment, UseCaseScreenshot, ProjectQASettings,
)


@admin.register(Sprint)
class SprintAdmin(admin.ModelAdmin):
    list_display = ['name', 'project', 'status', 'start_date', 'end_date', 'created_by']
    list_filter = ['status']
    search_fields = ['name', 'project__name']


@admin.register(UseCaseAssignment)
class UseCaseAssignmentAdmin(admin.ModelAdmin):
    list_display = ['use_case', 'assigned_to', 'sprint', 'status', 'assigned_at']
    list_filter = ['status']
    search_fields = ['assigned_to__user__username', 'use_case__description']


@admin.register(Defect)
class DefectAdmin(admin.ModelAdmin):
    list_display = ['title', 'severity', 'priority', 'status', 'project', 'reported_by', 'created_at']
    list_filter = ['status', 'severity', 'priority']
    search_fields = ['title', 'description']


@admin.register(ProjectQASettings)
class ProjectQASettingsAdmin(admin.ModelAdmin):
    list_display = ['project', 'alert_on_regression', 'alert_on_critical_defect', 'auto_report_on_close', 'jira_enabled', 'jira_project_key']
    search_fields = ['project__name', 'jira_project_key']


@admin.register(UseCaseExecution)
class UseCaseExecutionAdmin(admin.ModelAdmin):
    list_display = ['use_case', 'project', 'sprint', 'old_status', 'new_status', 'is_regression', 'executed_by', 'executed_at']
    list_filter = ['is_regression', 'new_status']


@admin.register(DefectComment)
class DefectCommentAdmin(admin.ModelAdmin):
    list_display = ['defect', 'author', 'created_at']
    search_fields = ['content']


@admin.register(WeeklyReport)
class WeeklyReportAdmin(admin.ModelAdmin):
    list_display = ['user', 'team', 'week_start', 'week_end', 'status', 'submitted_at']
    list_filter = ['status']
    search_fields = ['user__username', 'team__name']


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ['actor', 'action_type', 'description', 'created_at', 'is_read']
    list_filter = ['action_type', 'is_read']
    search_fields = ['description']


@admin.register(UseCaseComment)
class UseCaseCommentAdmin(admin.ModelAdmin):
    list_display = ['author', 'use_case', 'created_at']
    search_fields = ['content']


@admin.register(UseCaseScreenshot)
class UseCaseScreenshotAdmin(admin.ModelAdmin):
    list_display = ['assignment', 'uploaded_by', 'uploaded_at', 'caption']
    list_filter = ['uploaded_at']
    search_fields = ['caption']
