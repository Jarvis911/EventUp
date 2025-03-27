from . import serializers
from .models import EventType, Event, Ticket, User
from rest_framework import viewsets, generics


# Event type API view:
class EventTypeViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = EventType.objects.filter(active=True)
    serializer_class = serializers.EventTypeSerializer


# Event API view:
class EventViewSet(viewsets.ViewSet, generics.ListCreateAPIView, generics.UpdateAPIView):
    queryset = Event.objects.filter(active=True)
    serializer_class = serializers.EventSerializer

