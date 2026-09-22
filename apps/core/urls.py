from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('preview/<uuid:job_id>/', views.PreviewView.as_view(), name='preview'),
    path('generate/<uuid:job_id>/', views.GenerateExcelView.as_view(), name='generate'),
    path('gherkin/<uuid:job_id>/', views.GenerateGherkinView.as_view(), name='gherkin'),
    path('vscode/<uuid:job_id>/', views.OpenVSCodeView.as_view(), name='vscode'),
    path('result/<uuid:job_id>/', views.ResultView.as_view(), name='result'),
]
