from . import serializers, services
from .models import EventType, Event, Ticket, User, Invoice, Discount, Review
from django.db.models import F
from django.utils import timezone
from rest_framework.response import Response
from rest_framework import viewsets, generics, parsers, permissions, status, filters
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404
from rest_framework.pagination import PageNumberPagination
from django.db.models import Avg
# Custom Swagger
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
# Filter backend
from django_filters.rest_framework import DjangoFilterBackend


# Event type API view:
class EventTypeViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = EventType.objects.filter(active=True)
    serializer_class = serializers.EventTypeSerializer


# Event API view:
class EventViewSet(viewsets.ViewSet, generics.ListCreateAPIView):
    queryset = Event.objects.filter(active=True)
    serializer_class = serializers.EventSerializer
    parser_classes = [parsers.MultiPartParser]

    filter_backends = [DjangoFilterBackend, filters.SearchFilter]

    filterset_fields = ['event_type_id']
    search_fields = ['title', 'description']

    def get_permissions(self):
        if self.request.method in ['POST', 'PATCH']:
            return [permissions.IsAuthenticated()]
        return [permissions.AllowAny()]

    def perform_create(self, serializer):
        serializer.save(organizer_id=self.request.user)

    def create(self, request, *args, **kwargs):
        if not request.user.role != 'organizer':
            return Response({"detail": "You do not have permission to create event!"})
        return super().create(request, *args, **kwargs)

    def partial_update(self, request, pk=None):
        event = get_object_or_404(Event, pk=pk, active=True)

        if event.organizer_id != request.user:
            return Response({"detail": "You do not have permission to edit this event."}, status=status.HTTP_403_FORBIDDEN)

        e = serializers.EventSerializer(event, data=request.data, partial=True)
        if e.is_valid():
            e.save()
            return Response(e.data)
        return Response(e.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['delete'], detail=True, url_path='', permission_classes=[permissions.IsAuthenticated])
    def delete_event(self, request, pk=None):
        event = get_object_or_404(Event, pk=pk, active=True)
        if request.user != event.organizer_id and request.user.role != 'admin':
            return Response({"detail": "You do not have permission to delete this event!"},
                            status=status.HTTP_403_FORBIDDEN)

        if Invoice.objects.filter(event=event, payment_status__in=['pending', 'success']).exist():
            return Response({"detail": "You can not delete this event because it has associated invoices!"},
                            status=status.HTTP_400_BAD_REQUEST)

        event.active = False
        event.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @swagger_auto_schema(manual_parameters=[
        openapi.Parameter('event_type_id', openapi.IN_QUERY, description="Filter with event_type_id",
                          type=openapi.TYPE_INTEGER),
        openapi.Parameter('search', openapi.IN_QUERY, description="Search by keyword", type=openapi.TYPE_STRING),
    ])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

# User API view:
class UserViewSet(viewsets.ViewSet, generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = serializers.UserSerializer
    parser_classes = [parsers.MultiPartParser]

    # Only user owned account can get and patch their data
    @action(methods=['get', 'patch'], url_path="me", detail=False, permission_classes=[permissions.IsAuthenticated])
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
        return Response(self.serializer_class(user).data)


class TicketViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = Ticket.objects.all()
    serializer_class = serializers.TicketSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['get'], url_path='my_ticket', permission_classes=[permissions.IsAuthenticated])
    def get_current_user_ticket(self, request):
        if request.user.role != 'participant':
            return Response({'detail': 'Only participants can get their ticket!'}, status=status.HTTP_403_FORBIDDEN)

        tickets = Ticket.objects.filter(invoice_id__user_id=request.user, active=True)
        tk = self.get_serializer(tickets, many=True)

        return Response(tk.data)

    @action(methods=['post'], detail=True, permission_classes=[permissions.IsAuthenticated])
    def check_in(self, request, pk=None):
        ticket = get_object_or_404(Ticket, pk=pk, is_active=True)

        if ticket.event.organizer_id != request.user:
            return Response({'detail': 'You do not have permission to check in this ticket!'}, status=status.HTTP_403_FORBIDDEN)

        if ticket.status != 'booked':
            return Response({'detail': 'This ticket has already been checked in!'}, status=status.HTTP_400_BAD_REQUEST)

        qr_code_data = ticket.generate_qr_code_data()
        result = services.check_in_ticket(qr_code_data)

        if result.get('success'):
            ticket.status = 'checked_in'
            ticket.save()
            return Response({'detail': result['message']}, status=status.HTTP_200_OK)

        return Response({'detail': result['message']}, status=status.HTTP_400_BAD_REQUEST)


class DiscountViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = Discount.objects.all()
    serializer_class = serializers.DiscountSerializer

    @action(methods=['get'], detail=False, url_path='my_discount', permission_classes=[permissions.IsAuthenticated])
    def get_current_user_valid_discount(self, request):
        if request.user.role != 'participant':
            return Response({'detail': 'You do not have permission to get discount!'}, status=status.HTTP_403_FORBIDDEN)

        now = timezone.now()
        discounts = Discount.objects.filter(active=True,
                                            valid_from__lte=now,
                                            valid_until__gte=now,
                                            max_usage__gt=F('used_count'),
                                            membership_tier=request.user.membership_tier)

        dc = self.get_serializer(discounts, many=True)
        return Response(dc.data)

    @action(methods=['post'], detail=False, url_path='create_discount', permission_classes=[permissions.IsAuthenticated])
    def create_discount(self, request):
        if request.user.role != 'admin':
            return Response({'detail': 'You do not have permission to create discount!'}, status=status.HTTP_403_FORBIDDEN)

        dc = self.get_serializer(data=request.data)
        if dc.is_valid():
            dc.save()
            return Response(dc.data, status=status.HTTP_201_CREATED)
        return Response(dc.errors, status=status.HTTP_400_BAD_REQUEST)


class InvoiceViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = Invoice.objects.all()
    serializer_class = serializers.InvoiceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.role == 'admin':
            return Invoice.objects.all()
        return Invoice.objects.filter(user_id=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user_id=self.request.user)

    def create(self, request):
        if request.user.role != 'participant':
            return Response({'detail': 'Only participant can buy ticket!'}, status=status.HTTP_403_FORBIDDEN)

        invoice = self.serializer_class(data=request.data, context={'request': request})
        if invoice.is_valid():
            invoice.validated_data['user_id'] = request.user
            invoice.save()
            return Response(invoice.data, status=status.HTTP_201_CREATED)
        return Response(invoice.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=False, url_path='my_invoice', permission_classes=[permissions.IsAuthenticated])
    def get_current_user_valid_invoice(self, request):
        if request.user.role != 'participant':
            return Response({'detail': 'Only participant can have invoice!'}, status=status.HTTP_403_FORBIDDEN)

        invoices = Invoice.objects.filter(
            user_id=request.user
        )

        iv = self.get_serializer(invoices, many=True)
        return Response(iv.data)


class ReviewViewSet(viewsets.ViewSet, generics.ListAPIView):
    serializer_class = serializers.ReviewSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = PageNumberPagination

    def get_queryset(self):
        event_id = self.kwargs.get('event_id')
        return Review.objects.filter(event_id=event_id, active=True)

    @action(methods=['post'], detail=False, url_path='create', permission_classes=[permissions.IsAuthenticated])
    def create_review(self, request, event_id=None):
        event = get_object_or_404(Event, pk=event_id, active=True)
        serializer = self.get_serializer(
            data = request.data,
            event_id=event
        )

        if serializer.is_valid():
            serializer.save(
                participant_id=request.user,
                event_id=event
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.error, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['patch'], detail=True, url_path='', permission_classes=[permissions.IsAuthenticated])
    def update_review(self, request, event_id=None, pk=None):
        event = get_object_or_404(Event, pk=event_id, active=True)
        review = get_object_or_404(Review, pk=pk, event_id=event, active=True)

        if request.user != review.participant_id:
            return Response({"detail": "You do not have permission to edit this review!"}, status=status.HTTP_403_FORBIDDEN)

        serializer = self.get_serializer(
            review,
            data=request.data,
            partial=True,
            context={'request': request, 'event': event}
        )

        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.error, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=False, url_path='stats', permission_classes=[permissions.IsAuthenticated])
    def get_review_stats(self, request, event_id=None):
        event = get_object_or_404(Event, pk=event_id, active=True)
        reviews = Review.objects.filter(event_id=event, active=True)
        count = reviews.count()
        avg_rating = reviews.aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0

        return Response({
            'event_id': event_id,
            'review_count': count,
            'average_rating': round(avg_rating, 1) if avg_rating else 0.0
        }, status=status.HTTP_200_OK)

    @action(methods=['delete'], detail=True, url_path='', permission_classes=[permissions.IsAuthenticated])
    def delete_reviews(self, request, event_id=None, pk=None):
        event = get_object_or_404(Event, pk=event_id, active=True)
        review = get_object_or_404(Review, pk=pk, event_id=event, active=True)
        if request.user != review.participant_id and request.user.role != 'admin':
            return Response({"detail": "You do not have permission to delete this review!!!"},
                            status=status.HTTP_403_FORBIDDEN)
        review.active = False
        review.save()
        return Response(status=status.HTTP_204_NO_CONTENT)







