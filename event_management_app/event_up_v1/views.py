from . import serializers
from .models import EventType, Event, Ticket, User
from rest_framework.response import Response
from rest_framework import viewsets, generics, parsers, permissions
from rest_framework.decorators import action


# Event type API view:
class EventTypeViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = EventType.objects.filter(active=True)
    serializer_class = serializers.EventTypeSerializer


# Event API view:
class EventViewSet(viewsets.ViewSet, generics.ListCreateAPIView, generics.UpdateAPIView):
    queryset = Event.objects.filter(active=True)
    serializer_class = serializers.EventSerializer


# User API view:
class UserViewSet(viewsets.ViewSet, generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = serializers.UserSerializer
    parser_classes = [parsers.MultiPartParser]

    # Only user owned account can get and patch their data
    @action(methods=['get', 'patch'], url_path="current-user", detail=False, permission_classes=[permissions.IsAuthenticated])
    def get_current_user(self, request):
        if request.method.__eq__("PATCH"):
            u = request.user
            for key in u:
                if key in ['first_name', 'last_name']:
                    setattr(u, key, request.data[key])
                elif key.__eq__('password'):
                    u.set_password(request.data[key])

            u.save()
            return Response(serializers.UserSerializer(u).data)
        return Response(serializers.UserSerializer(request.user).data)


