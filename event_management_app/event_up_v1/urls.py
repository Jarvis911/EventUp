from django.urls import path, include
from . import views
# For API
from rest_framework.routers import DefaultRouter
# Momo result route
from django.views.generic import TemplateView
# For swagger document
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

router = DefaultRouter()
router.register('category', views.CategoryViewSet, basename='Category')
router.register('event', views.EventViewSet, basename='Event')
router.register('user', views.UserViewSet, basename='User')
router.register('ticket', views.TicketViewSet, basename='Ticket')
router.register('discount', views.DiscountViewSet, basename='Discount')
router.register('invoice', views.InvoiceViewSet, basename='Invoice')
router.register(r'event/(?P<event_id>\d+)/reviews', views.ReviewViewSet, basename='Review')
router.register(r'event/(?P<event_id>\d+)/reviews/(?P<review_id>\d+)/response', views.ResponseViewSet, basename='Response')
router.register('reports', views.ReportViewSet, basename='Reports')
router.register('favorite/event', views.FavoriteEventViewSet, basename='FavoriteEvent')
router.register('user/preference', views.UserPreferenceViewSet, basename='UserPreference')

urlpatterns = [
    path('', include(router.urls)),
    path('payment/success/', TemplateView.as_view(template_name='success.html'), name='payment_success'),
    path('payment/fail/', TemplateView.as_view(template_name='fail.html'), name='payment_fail'),
    ]
