from django.urls import path
from . import views

app_name = 'teams'

urlpatterns = [
    # ── Auth ────────────────────────────────────────────────────────────────
    path('auth/register/',          views.RegisterView.as_view(),             name='register'),
    path('auth/login/',             views.LoginView.as_view(),                name='login'),
    path('auth/logout/',            views.LogoutView.as_view(),               name='logout'),
    path('auth/me/',                views.MeView.as_view(),                   name='me'),
    path('auth/password-reset/',    views.PasswordResetRequestView.as_view(), name='password-reset'),
    path('auth/password-reset/confirm/', views.PasswordResetConfirmView.as_view(), name='password-reset-confirm'),

    # ── Teams ────────────────────────────────────────────────────────────────
    path('teams/',         views.TeamListCreateView.as_view(), name='team-list'),
    path('teams/<slug:slug>/', views.TeamDetailView.as_view(), name='team-detail'),

    # ── Team Members ─────────────────────────────────────────────────────────
    path('teams/<slug:slug>/members/',
         views.TeamMemberListView.as_view(), name='team-members'),
    path('teams/<slug:slug>/members/<int:user_id>/',
         views.TeamMemberDetailView.as_view(), name='team-member-detail'),

    # ── Invitations ──────────────────────────────────────────────────────────
    path('teams/invitations/<uuid:token>/',
         views.InvitationDetailView.as_view(), name='invitation-detail'),
    path('teams/invitations/<uuid:token>/accept/',
         views.InvitationAcceptView.as_view(), name='invitation-accept'),
    path('teams/invitations/<uuid:token>/reject/',
         views.InvitationRejectView.as_view(), name='invitation-reject'),

    # ── Dashboard ────────────────────────────────────────────────────────────
    path('teams/<slug:slug>/dashboard/',
         views.TeamDashboardView.as_view(), name='team-dashboard'),

    # ── Projects ─────────────────────────────────────────────────────────────
    path('teams/<slug:slug>/projects/',
         views.ProjectListCreateView.as_view(), name='project-list'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/',
         views.ProjectDetailView.as_view(), name='project-detail'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/avatar/',
         views.ProjectAvatarView.as_view(), name='project-avatar'),

    # ── Project Members ──────────────────────────────────────────────────────
    path('teams/<slug:slug>/projects/<slug:project_slug>/members/',
         views.ProjectMemberListView.as_view(), name='project-members'),
    path('teams/<slug:slug>/projects/<slug:project_slug>/members/<int:user_id>/',
         views.ProjectMemberDetailView.as_view(), name='project-member-detail'),

    # ── Project Progress ─────────────────────────────────────────────────────
    path('teams/<slug:slug>/projects/<slug:project_slug>/progress/',
         views.ProjectProgressView.as_view(), name='project-progress'),

    # ── Project Member Assign ─────────────────────────────────────────────────
    path('teams/<slug:slug>/projects/<slug:project_slug>/assign-members/',
         views.ProjectMemberAssignView.as_view(), name='project-assign-members'),

    # ── Reports ──────────────────────────────────────────────────────────────
    path('teams/<slug:slug>/reports/daily-scrum/',
         views.DailyScrumReportView.as_view(), name='daily-scrum-report'),
    path('teams/<slug:slug>/reports/weekly/',
         views.WeeklyReportView.as_view(), name='weekly-report'),
]
