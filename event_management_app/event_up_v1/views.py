from . import serializers, services
from .models import Category, Event, Ticket, User, Invoice, Discount, Review, FavoriteEvent
from django.db.models import F, Count, Q, FloatField, ExpressionWrapper, Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework import viewsets, generics, parsers, permissions, status, filters
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404
from rest_framework.pagination import PageNumberPagination
from django.db.models import Avg
from rest_framework.exceptions import ValidationError
from django.db.models.functions import Coalesce
from datetime import datetime, timedelta
# Momo
from .utils import create_momo_payment, verify_momo_payment, send_notification
from django.shortcuts import redirect
from .services import create_tickets_after_payment
# Custom Swagger
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
# Filter backend
from django_filters.rest_framework import DjangoFilterBackend


class OrganizerPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'organizer'


class ParticipantPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'participant'


# Category API view:
class CategoryViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = Category.objects.filter(active=True)
    serializer_class = serializers.CategorySerializer


# Event API view:
class EventViewSet(viewsets.ViewSet, generics.ListCreateAPIView):
    queryset = Event.objects.filter(active=True)
    serializer_class = serializers.EventSerializer
    parser_classes = [parsers.MultiPartParser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['category_id']
    search_fields = ['title', 'description']

    def get_permissions(self):
        if self.request.method in ['POST', 'PATCH', 'DELETE']:
            return [permissions.IsAuthenticated()]
        return [permissions.AllowAny()]

    def perform_create(self, serializer):
        serializer.save(organizer_id=self.request.user)

    def create(self, request, *args, **kwargs):
        if request.user.role != 'organizer':
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

    def retrieve(self, request, *args, **kwargs):
        pk = kwargs.get('pk')
        if not pk or not pk.isdigit():
            return Response({'detail': 'Invalid event ID.'}, status=status.HTTP_400_BAD_REQUEST)

        instance = self.get_object()
        instance.views = F('views') + 1
        instance.save(update_fields=['views'])
        instance.refresh_from_db()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(methods=['delete'], detail=True, url_path='', permission_classes=[permissions.IsAuthenticated])
    def delete_event(self, request, pk=None):
        event = get_object_or_404(Event, pk=pk, active=True)
        if request.user != event.organizer_id and request.user.role != 'admin':
            return Response({"detail": "You do not have permission to delete this event!"},
                            status=status.HTTP_403_FORBIDDEN)

        if Invoice.objects.filter(event_id=event, payment_status__in=['pending', 'success']).exists():
            return Response({"detail": "You can not delete this event because it has associated invoices!"},
                            status=status.HTTP_400_BAD_REQUEST)

        event.active = False
        event.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @swagger_auto_schema(manual_parameters=[
        openapi.Parameter('category_id', openapi.IN_QUERY, description="Filter with category_id",
                          type=openapi.TYPE_INTEGER),
        openapi.Parameter('search', openapi.IN_QUERY, description="Search by keyword", type=openapi.TYPE_STRING),
    ])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @action(methods=['get'], detail=False, permission_classes=[permissions.AllowAny])
    def trend(self, request):
        events = Event.objects.filter(active=True).annotate(
            review_count=Coalesce(Count('review', filter=Q(review__active=True)), 0),
            sold_ticket_count=Coalesce(Count('invoices__tickets', filter=Q(invoices__payment_status='success')), 0),
            views_float=Coalesce(F('views'), 0),
        ).annotate(
            trend_score=ExpressionWrapper(
                F('views_float')*0.2 + F('review_count')*0.5 + F('sold_ticket_count')*0.3,
                output_field=FloatField()
            )
        ).order_by('-trend_score')[:10]

        serializer = self.get_serializer(events, many=True)
        return Response(serializer.data)


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

    @action(methods=['post'], detail=False, permission_classes=[permissions.IsAuthenticated])
    def check_in(self, request, pk=None):
        serializer = serializers.QRCodeCheckInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        qr_code_data = serializer.validated_data['qr_code_data']
        if not qr_code_data:
            return Response({'detail': 'QR code data is required!'}, status=status.HTTP_400_BAD_REQUEST)

        result = services.check_in_ticket(qr_code_data)

        if not result.get('success'):
            return Response({'detail': result.get('message', 'Check-in failed')}, status=status.HTTP_400_BAD_REQUEST)

        ticket = get_object_or_404(Ticket, pk=result['ticket_id'], active=True)
        if ticket.invoice_id.event_id.organizer_id != request.user:
            return Response({'detail': 'You do not have permission to check in this ticket!'},
                            status=status.HTTP_403_FORBIDDEN)

        return Response({'detail': result['message']}, status=status.HTTP_200_OK)


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
        user = self.request.user
        if user.is_authenticated:
            if getattr(user, 'role', None) == 'admin':
                return Invoice.objects.all()
            return Invoice.objects.filter(user_id=self.request.user)

        return Invoice.objects.none()

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

    @action(methods=['post'], detail=True, url_path='momo-payment')
    def momo_payment(self, request, pk=None):
        invoice = get_object_or_404(Invoice, pk=pk, user_id=request.user, payment_status='pending')
        try:
            pay_url = create_momo_payment(invoice, request_id=invoice.invoice_code)
            return Response({'pay_url': pay_url}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=False, url_path='momo/ipn', permission_classes=[permissions.AllowAny])
    def momo_ipn(self, request):
        data = request.data

        # Temporary disable validate signature

        if not verify_momo_payment(data):
            return Response({'detail': 'Invalid signature'}, status=status.HTTP_400_BAD_REQUEST)

        order_id = data.get('orderId')
        invoice_code = order_id.split('-')[0]
        invoice = get_object_or_404(Invoice, invoice_code=invoice_code)

        if data.get('resultCode') == 0:
            invoice.payment_status = 'success'
            invoice.transaction_id = data.get('transId')
            invoice.save()
            create_tickets_after_payment(invoice)
            send_notification(
                user=invoice.user_id,
                title=f"Payment Successful for {invoice.event_id.title}",
                message=f"Your payment of {invoice.final_amount} for {invoice.event_id.title} was successful. Invoice: {invoice.invoice_code}"
            )
        else:
            invoice.payment_status = 'fail'
            invoice.save()
            send_notification(
                user=invoice.user_id,
                title=f"Payment Failed for {invoice.event_id.title}",
                message=f"Your payment attempt for {invoice.event_id.title} failed. Reason: {data.get('message')}"
            )
        return Response({'status': 'success'}, status=status.HTTP_200_OK)

    @action(methods=['get'], detail=False, url_path='momo/return', permission_classes=[permissions.AllowAny])
    def momo_return(self, request):
        order_id = request.query_params.get('orderId')
        result_code = request.query_params.get('resultCode')
        invoice_code = order_id.split('-')[0]
        invoice = get_object_or_404(Invoice, invoice_code=invoice_code)

        if result_code == '0':
            return redirect('payment_success')
        else:
            return redirect('payment_fail')


class ReviewViewSet(viewsets.ViewSet, generics.ListAPIView):
    permission_classes = [permissions.AllowAny]

    def get_serializer_class(self):
        if self.action == 'get_review_stats':
            return serializers.ReviewStatsSerializer
        return serializers.ReviewSerializer

    def get_queryset(self):
        event_id = self.kwargs.get('event_id')

        if event_id is None:
            return Review.objects.all()
        try:
            event_id = int(event_id)
        except (TypeError, ValueError):
            raise ValidationError({'event_id': 'Invalid event_id format. Must be an integer.'})

        return Review.objects.filter(event_id=event_id, active=True)

    @action(methods=['post'], detail=False, url_path='create', permission_classes=[permissions.IsAuthenticated])
    def create_review(self, request, event_id=None):
        event = get_object_or_404(Event, pk=event_id, active=True)

        serializer = self.get_serializer(
            data=request.data,
            context={'request': request, 'event': event}
        )

        if serializer.is_valid():
            serializer.save(
                participant_id=request.user,
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=False, url_path='stats', permission_classes=[permissions.AllowAny])
    def get_review_stats(self, request, event_id=None):
        if not event_id or not event_id.isdigit():
            return Response({'detail': 'Invalid event ID.'}, status=status.HTTP_400_BAD_REQUEST)

        event = get_object_or_404(Event, pk=event_id, active=True)
        reviews = Review.objects.filter(event_id=event, active=True)
        count = reviews.count()
        avg_rating = reviews.aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0

        return Response({
            'event_id': int(event.id),
            'review_count': count,
            'average_rating': round(avg_rating, 1) if avg_rating else 0.0
        }, status=status.HTTP_200_OK)

    @action(methods=['delete'], detail=True, url_path='', permission_classes=[permissions.IsAuthenticated])
    def delete_review(self, request, event_id=None, pk=None):
        event = get_object_or_404(Event, pk=event_id, active=True)
        review = get_object_or_404(Review, pk=pk, event_id=event, active=True)
        if request.user != review.participant_id and request.user.role != 'admin':
            return Response({"detail": "You do not have permission to delete this review!!!"},
                            status=status.HTTP_403_FORBIDDEN)
        review.active = False
        review.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReportViewSet(viewsets.ViewSet):
    permission_classes = [OrganizerPermission]

    @swagger_auto_schema(
        responses={200: openapi.Response("Successfully get dashboard", serializers.OrganizerDashboardSerializer)},
        operation_description="Retrieve organizer dashboard with event statistics."
    )
    @action(methods=['get'], detail=False, url_path='organizer/dashboard')
    def organizer_dashboard(self, request):
        organizer = request.user
        events = Event.objects.filter(organizer_id=organizer, active=True)

        # Dashboard data
        total_tickets = Invoice.objects.filter(
            event_id__in=events,
            payment_status='success'
        ).aggregate(total=Sum('ticket_count'))['total'] or 0

        total_revenue = Invoice.objects.filter(
            event_id__in=events,
            payment_status='success'
        ).aggregate(total=Sum('amount'))['total'] or 0

        total_views = events.aggregate(total=Sum('views'))['total'] or 0

        # Bar chart data
        event_data = []
        for event in events:
            avg_rating = Review.objects.filter(
                event_id=event,
                active=True
            ).aggregate(avg=Avg('rating'))['avg'] or 0

            event_data.append({
                'event_id': event.id,
                'event_title': event.title,
                'views': event.views,
                'average_rating': round(avg_rating, 1)
            })

        data = {
            'total_tickets': total_tickets,
            'total_revenue': total_revenue,
            'total_views': total_views,
            'events': event_data
        }

        serializer = serializers.OrganizerDashboardSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                'month', openapi.IN_QUERY, description='Month of the report (1-12)', type=openapi.TYPE_INTEGER
            ),
            openapi.Parameter(
                'year', openapi.IN_QUERY, description='Year of the report (e.g., 2025)', type=openapi.TYPE_INTEGER
            ),
        ],
        responses={200: openapi.Response("Successfully get report data", serializers.MonthlyReportSerializer)},
        operation_description="Retrieve monthly report with ticket and revenue statistics for the organizer."
    )
    @action(methods=['get'], url_path='organizer/monthly', detail=False)
    def monthly_report(self, request):
        organizer = request.user
        month = request.query_params.get('month')
        year = request.query_params.get('year')

        if not (month and year):
            return Response({'detail': 'Month and year are required!!'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            month = int(month)
            year = int(year)
            if not (1 <= month <= 12):
                raise ValueError
        except ValueError:
            return Response({'detail': 'Invalid month or year format!!'},
                            status=status.HTTP_400_BAD_REQUEST)

        start_date = timezone.make_aware(datetime(year, month, 1))
        end_date = (start_date + timedelta(days=31)).replace(day=1) - timedelta(seconds=1)

        invoices = Invoice.objects.filter(
            event_id__organizer_id=organizer,
            payment_status='success',
            created_at__range=[start_date, end_date]
        )

        # Ticket data to draw chart
        ticket_data = invoices.values('event_id', 'event_id__title').annotate(ticket_count=Sum('ticket_count')).order_by('-ticket_count')
        # Revenue data to draw chart
        revenue_data = invoices.values('event_id', 'event_id__title').annotate(revenue=Sum('amount')).order_by('-revenue')

        data = {
            'ticket_pie_chart': [
                {'event_id': item['event_id'],
                 'event_title': item['event_id__title'],
                 'value': item['ticket_count']}
                for item in ticket_data
            ],
            'revenue_pie_chart': [
                {'event_id': item['event_id'],
                 'event_title': item['event_id__title'],
                 'value': float(item['revenue'])}
                for item in revenue_data
            ]
        }

        serializer = serializers.MonthlyReportSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class FavoriteEventViewSet(viewsets.ViewSet):
    queryset = FavoriteEvent.objects.all()
    permission_classes = [ParticipantPermission]
    serializer_class = serializers.FavoriteEventSerializer

    @swagger_auto_schema(responses={200: serializers.FavoriteEventSerializer(many=True)})
    def list(self, request):
        favorites = FavoriteEvent.objects.filter(participant_id=request.user)
        serializer = serializers.FavoriteEventSerializer(favorites, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(request_body=serializers.FavoriteEventSerializer,
                         responses={201: openapi.Response('Successfully created', serializers.FavoriteEventSerializer)},
                         operation_description="The authenticated participant (from token) will be used "
                                               "automatically as `participant_id`. Do NOT sent it in request")
    def create(self, request):
        serializer = serializers.FavoriteEventSerializer(data=request.data, context={'request': request})
        favorite_event = FavoriteEvent.objects.filter(participant_id=request.user, event_id=request.data["event_id"])
        if favorite_event:
            return Response({"detail": "This participant has already favored this event!"}, status=status.HTTP_400_BAD_REQUEST)
        if serializer.is_valid():
            serializer.save(participant_id=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(responses={204: openapi.Response('No content')})
    def destroy(self, request, pk=None):
        favorite = get_object_or_404(FavoriteEvent, participant_id=request.user, event_id__id=pk)
        favorite.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)







