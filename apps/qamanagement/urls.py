from django.urls import path
from . import automation_views, views

app_name = 'qamanagement'

urlpatterns = [
    # ── Sprints ──────────────────────────────────────────────────────────────
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/',
         views.SprintListCreateView.as_view(), name='sprint-list'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/',
         views.SprintDetailView.as_view(), name='sprint-detail'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/board/',
         views.SprintBoardView.as_view(), name='sprint-board'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/cases/',
         views.SprintCaseListCreateView.as_view(), name='sprint-case-list'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/cases/<uuid:ticket_id>/',
         views.SprintCaseDetailView.as_view(), name='sprint-case-detail'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/cases/<uuid:ticket_id>/comments/',
         views.SprintCaseCommentListCreateView.as_view(), name='sprint-case-comments'),
    # Compatibility paths point to the same canonical case records.
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/tickets/',
         views.SprintCaseListCreateView.as_view(), name='sprint-ticket-list'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/tickets/<uuid:ticket_id>/',
         views.SprintCaseDetailView.as_view(), name='sprint-ticket-detail'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/tickets/<uuid:ticket_id>/comments/',
         views.SprintCaseCommentListCreateView.as_view(), name='sprint-ticket-comments'),

    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/report/pdf/',
         views.SprintReportPDFView.as_view(), name='sprint-report-pdf'),

    # ── Performance, évolution & rapports ────────────────────────────────────
    path('teams/<slug:slug>/performance/',
         views.TeamPerformanceView.as_view(), name='team-performance'),
    path('teams/<slug:slug>/portfolio/',
         views.TeamPortfolioView.as_view(), name='team-portfolio'),
    path('teams/<slug:slug>/portfolio/pdf/',
         views.TeamPortfolioPDFView.as_view(), name='team-portfolio-pdf'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/performance/',
         views.ProjectPerformanceView.as_view(), name='project-performance'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/evolution/',
         views.ProjectEvolutionView.as_view(), name='project-evolution'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/report/',
         views.ProjectReportView.as_view(), name='project-report'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/report/pdf/',
         views.ProjectReportPDFView.as_view(), name='project-report-pdf'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/regressions/',
         views.RegressionListView.as_view(), name='regression-list'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/assignable-members/',
         views.AssignableMembersView.as_view(), name='assignable-members'),

    # ── Automatisation & intégrations ────────────────────────────────────────
    path('teams/<slug:slug>/projects/<slug:project_slug>/qa-settings/',
         automation_views.QASettingsView.as_view(), name='qa-settings'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/report/xlsx/',
         automation_views.ProjectReportExcelView.as_view(), name='project-report-xlsx'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/defects/export/xlsx/',
         automation_views.DefectExcelView.as_view(), name='defect-export-xlsx'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/report/send/',
         automation_views.SprintReportSendView.as_view(), name='sprint-report-send'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/import-cases/',
         automation_views.SprintCaseImportView.as_view(), name='sprint-case-import'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/sprints/<uuid:sprint_id>/carry-over/',
         automation_views.CarryOverView.as_view(), name='sprint-carry-over'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/regression-campaign/',
         automation_views.RegressionCampaignView.as_view(), name='regression-campaign'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/test-results/import/',
         automation_views.TestResultsImportView.as_view(), name='test-results-import'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/jira/sync/',
         automation_views.JiraSyncView.as_view(), name='jira-sync'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/jira/validate-tickets/',
         automation_views.JiraTicketValidateView.as_view(), name='jira-validate-tickets'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/defects/<uuid:defect_id>/jira/',
         automation_views.DefectJiraPushView.as_view(), name='defect-jira-push'),
    path('integrations/jira/webhook/',
         automation_views.JiraWebhookView.as_view(), name='jira-webhook'),

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
    path('teams/<slug:slug>/projects/<slug:project_slug>/defects/<uuid:defect_id>/comments/',
         views.DefectCommentListCreateView.as_view(), name='defect-comments'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/defects/<uuid:defect_id>/history/',
         views.DefectHistoryView.as_view(), name='defect-history'),
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
