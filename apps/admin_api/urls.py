from django.urls import path
from . import views

app_name = 'admin_api'

urlpatterns = [
    path('summary/', views.AdminSummaryView.as_view(), name='summary'),
    path('statistics/', views.AdminStatisticsView.as_view(), name='statistics'),
    path('system/', views.AdminSystemView.as_view(), name='system'),

    path('users/', views.AdminUserListView.as_view(), name='users'),
    path('users/<int:pk>/', views.AdminUserDetailView.as_view(), name='user-detail'),
    path('users/<int:pk>/reset-password/', views.AdminResetPasswordView.as_view(), name='user-reset-password'),

    path('teams/', views.AdminTeamListView.as_view(), name='teams'),
    path('teams/<slug:slug>/', views.AdminTeamDetailView.as_view(), name='team-detail'),
    path('teams/<slug:slug>/transfer/', views.AdminTeamTransferView.as_view(), name='team-transfer'),

    path('projects/', views.AdminProjectListView.as_view(), name='projects'),
    path('projects/<uuid:pk>/', views.AdminProjectDetailView.as_view(), name='project-detail'),

    path('jobs/', views.AdminJobListView.as_view(), name='jobs'),
    path('jobs/<uuid:pk>/', views.AdminJobDetailView.as_view(), name='job-detail'),

    path('activity/', views.AdminActivityListView.as_view(), name='activity'),

    path('use-cases/', views.AdminUseCaseListView.as_view(), name='use-cases'),
    path('sprints/', views.AdminSprintListView.as_view(), name='sprints'),
    path('defects/', views.AdminDefectListView.as_view(), name='defects'),
    path('reports/', views.AdminReportListView.as_view(), name='reports'),
]