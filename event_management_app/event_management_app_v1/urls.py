from django.urls import path, include
from . import views
# For API
from rest_framework.routers import DefaultRouter


router = DefaultRouter()
router.register(r'event_type', views.EventTypeViewSet, basename='event_type')
router.register(r'event', views.EventViewSet, basename='event')

urlpatterns = [
    path('', include(router.urls)),
    ]
