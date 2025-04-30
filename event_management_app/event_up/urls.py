from django.contrib import admin
from django.urls import path, include, re_path
# Help serving file in Debug mode
from django.conf import settings
from django.conf.urls.static import static
# Import Swagger
from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from oauth2_provider.views import TokenView
# Import debug toolbar
import debug_toolbar

schema_view = get_schema_view(
    openapi.Info(
        title="EventUp API",
        default_version='v1',
        description="APIs for EventUp",
        contact=openapi.Contact(email="2251052124tri@ou.edu.vn"),
        license=openapi.License(name="Ho Duc Tri"),
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('event_up_v1.urls')),
    re_path(r'^ckeditor/', include('ckeditor_uploader.urls')),
    re_path(r'^swagger(?P<format>\.json|\.yaml)$', schema_view.without_ui(cache_timeout=0),
                        name='schema-json'),
    re_path(r'^swagger/$', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    re_path(r'^redoc/$', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    path('o/', include('oauth2_provider.urls', namespace='oauth2_provider')),
    path('__debug__/', include(debug_toolbar.urls)),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

