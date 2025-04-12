from . import serializers
from .models import EventType, Event, Ticket, User
from rest_framework.response import Response
from rest_framework import viewsets, generics, parsers, permissions, status
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
        user = request.user
        if request.method.__eq__("PATCH"):
            u = self.serializer_class(user, data=request.data, partial=True)
            # Cannot change role through API
            if u.is_valid():
                if 'role' in u.validated_data:
                    return Response({'error': 'Cannot change role through API'}, status=status.HTTP_403_FORBIDDEN)
                u.save()
                return Response(u.data)
            return Response(u.errors, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.serializer_class(user))


