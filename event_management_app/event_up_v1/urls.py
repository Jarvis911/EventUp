from django.urls import path, include
from . import views
# For API
from rest_framework.routers import DefaultRouter


router = DefaultRouter()
router.register('event_type', views.EventTypeViewSet, basename='event_type')
router.register('event', views.EventViewSet, basename='event')
router.register('user', views.UserViewSet, basename='user')

urlpatterns = [
    path('', include(router.urls)),
    ]
