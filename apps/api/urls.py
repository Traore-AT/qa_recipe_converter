from django.urls import path
from . import views

app_name = 'api'

urlpatterns = [
    path('health/', views.HealthCheckView.as_view(), name='healthcheck'),
    path('csrf/', views.CsrfTokenView.as_view(), name='csrf'),
    path('upload/', views.UploadAPIView.as_view(), name='upload'),
    path('jobs/', views.JobListAPIView.as_view(), name='jobs'),
    path('jobs/<uuid:job_id>/', views.JobDetailAPIView.as_view(), name='job-detail'),
    path('jobs/<uuid:job_id>/generate/', views.GenerateExcelAPIView.as_view(), name='generate'),
    path('jobs/<uuid:job_id>/use-cases/<uuid:uc_id>/', views.UseCaseUpdateAPIView.as_view(), name='uc-update'),
    path('jobs/<uuid:job_id>/gherkin/', views.GenerateGherkinAPIView.as_view(), name='gherkin-download'),
    path('jobs/<uuid:job_id>/save/', views.BatchSaveAPIView.as_view(), name='batch-save'),
    path('jobs/<uuid:job_id>/vscode/', views.OpenVSCodeAPIView.as_view(), name='vscode-open'),
    path('jobs/<uuid:job_id>/bulk-status/', views.UseCaseBulkStatusAPIView.as_view(), name='uc-bulk-status'),
    path('files/search/', views.FileSearchAPIView.as_view(), name='file-search'),
    path('jobs/<uuid:job_id>/csv/', views.GenerateCSVAPIView.as_view(), name='csv-download'),
]
