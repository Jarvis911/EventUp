from django.urls import path, include
from . import views
# For API
from rest_framework.routers import DefaultRouter


router = DefaultRouter()
router.register('event_type', views.EventTypeViewSet, basename='Event Type')
router.register('event', views.EventViewSet, basename='Event')
router.register('user', views.UserViewSet, basename='User')
router.register('ticket', views.TicketViewSet, basename='Ticket')
router.register('discount', views.DiscountViewSet, basename='Discount')
router.register('invoice', views.InvoiceViewSet, basename='Invoice')
router.register('event/(?P<event_id>[^/.]+)/reviews', views.ReviewViewSet, basename='Review')

urlpatterns = [
    path('', include(router.urls)),
    ]
