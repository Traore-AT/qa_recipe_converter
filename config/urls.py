from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.core.urls')),
    path('api/', include('apps.api.urls')),
    path('api/', include('apps.teams.urls')),
    path('api/', include('apps.qamanagement.urls')),
    path('api/admin/', include('apps.admin_api.urls')),
    path('teams/', include('apps.teams.web_urls')),   # Django template pages

    # OpenAPI / Swagger
    path('api/schema/',     SpectacularAPIView.as_view(),     name='schema'),
    path('api/docs/',       SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/',      SpectacularRedocView.as_view(url_name='schema'),   name='redoc'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
