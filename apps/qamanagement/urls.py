from django.urls import path
from . import views

app_name = 'qamanagement'

urlpatterns = [
    # ── Sprints ──────────────────────────────────────────────────────────────
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/',
         views.SprintListCreateView.as_view(), name='sprint-list'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/',
         views.SprintDetailView.as_view(), name='sprint-detail'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/board/',
         views.SprintBoardView.as_view(), name='sprint-board'),

    # ── Use Case Assignments ─────────────────────────────────────────────────
    path('teams/<slug:slug>/projects/<slug:project_slug>/assignments/',
         views.AssignmentListCreateView.as_view(), name='assignment-list'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/assignments/<uuid:assignment_id>/',
         views.AssignmentDeleteView.as_view(), name='assignment-delete'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/unassigned-ucs/',
         views.UnassignedUseCasesView.as_view(), name='unassigned-ucs'),

    # ── Use Case Execution ───────────────────────────────────────────────────
    path('teams/<slug:slug>/projects/<slug:project_slug>/use-cases/<uuid:uc_id>/status/',
         views.UseCaseStatusUpdateView.as_view(), name='uc-status-update'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/use-cases/<uuid:uc_id>/jira/',
         views.UseCaseJiraUpdateView.as_view(), name='uc-jira-update'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/use-cases/<uuid:uc_id>/comments/',
         views.UseCaseCommentListCreateView.as_view(), name='uc-comments'),

    # ── Defects ──────────────────────────────────────────────────────────────
    path('teams/<slug:slug>/projects/<slug:project_slug>/defects/',
         views.DefectListCreateView.as_view(), name='defect-list'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/defects/<uuid:defect_id>/',
         views.DefectDetailView.as_view(), name='defect-detail'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/defects/stats/',
         views.DefectStatsView.as_view(), name='defect-stats'),

    # ── Weekly Reports ───────────────────────────────────────────────────────
    path('teams/<slug:slug>/weekly-reports/',
         views.WeeklyReportListCreateView.as_view(), name='weekly-report-list'),
    path('teams/<slug:slug>/weekly-reports/current/',
         views.WeeklyReportCurrentView.as_view(), name='weekly-report-current'),
    path('teams/<slug:slug>/weekly-reports/<uuid:report_id>/',
         views.WeeklyReportDetailView.as_view(), name='weekly-report-detail'),

    # ── Member Progress ──────────────────────────────────────────────────────
    path('teams/<slug:slug>/projects/<slug:project_slug>/member-progress/',
         views.MemberProgressView.as_view(), name='member-progress'),

    # ── Activity / Notifications ─────────────────────────────────────────────
    path('notifications/',
         views.NotificationListView.as_view(), name='notification-list'),
    path('notifications/<uuid:notification_id>/read/',
         views.NotificationReadView.as_view(), name='notification-read'),
    path('notifications/mark-all-read/',
         views.MarkAllNotificationsReadView.as_view(), name='notifications-mark-all-read'),
    path('teams/<slug:slug>/activity/',
         views.TeamActivityView.as_view(), name='team-activity'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/activity/',
         views.ProjectActivityView.as_view(), name='project-activity'),

    # ── Use Case Detail ───────────────────────────────────────────────────────
    path('teams/<slug:slug>/projects/<slug:project_slug>/assignments/<uuid:assignment_id>/detail/',
         views.UseCaseDetailView.as_view(), name='uc-detail'),

    # ── Screenshots ───────────────────────────────────────────────────────────
    path('teams/<slug:slug>/projects/<slug:project_slug>/assignments/<uuid:assignment_id>/screenshots/',
         views.UseCaseScreenshotListCreateView.as_view(), name='screenshot-list'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/assignments/<uuid:assignment_id>/screenshots/<uuid:screenshot_id>/',
         views.UseCaseScreenshotDeleteView.as_view(), name='screenshot-delete'),

    # ── Sprint CSV Export ─────────────────────────────────────────────────────
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/export/csv/',
         views.SprintCSVExportView.as_view(), name='sprint-csv-export'),
]
