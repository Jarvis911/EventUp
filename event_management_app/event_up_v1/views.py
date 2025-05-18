from . import serializers, services
from .models import Category, Event, Ticket, User, Invoice, Discount, Review, FavoriteEvent, UserPreference, ReviewResponse
from django.db.models import F, Count, Q, FloatField, ExpressionWrapper, Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework import viewsets, generics, parsers, permissions, status, filters
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404
from django.core.exceptions import ObjectDoesNotExist
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
# Recommend
from . import recommender
import logging
from django.views.decorators.cache import cache_page
from django.core.cache import cache
from django.utils.decorators import method_decorator


class OrganizerPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return user.is_authenticated and getattr(user, 'role', None) == 'organizer'


class ParticipantPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return user.is_authenticated and getattr(user, 'role', None) == 'participant'


# Category API view:
class CategoryViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = Category.objects.filter(active=True)
    serializer_class = serializers.CategorySerializer

    @swagger_auto_schema(
        responses={200: serializers.CategorySerializer(many=True)},
        operation_description="Retrieve a list of all active categories.",
        operation_summary="List all active categories"
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


# Event API view:
class EventViewSet(viewsets.ViewSet, generics.ListCreateAPIView):
    queryset = Event.objects.filter(active=True)
    serializer_class = serializers.EventSerializer
    parser_classes = [parsers.MultiPartParser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['category_id']
    search_fields = ['title', 'description']

    def get_permissions(self):
        if self.action == 'get_my_event':
            return [OrganizerPermission()]
        if self.request.method in ['POST', 'PATCH', 'DELETE']:
            return [OrganizerPermission()]
        return [permissions.AllowAny()]

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.select_related('category_id')

    def perform_create(self, serializer):
        serializer.save(organizer_id=self.request.user)

    @swagger_auto_schema(
        request_body=serializers.EventSerializer,
        responses={
            201: openapi.Response('Successfully created', serializers.EventSerializer),
            403: 'Forbidden',
            400: 'Bad Request'
        },
        operation_description="Create a new event.\nThe authenticated organizer (from token) will be used "
                              "automatically as `organizer_id`. Do NOT send it in request.\n"
                              "Longitude and latitude will be calculated base on location. Do NOT send it in request",
        operation_summary="Create a new event"
    )
    def create(self, request, *args, **kwargs):
        if request.user.role != 'organizer':
            return Response({"detail": "You do not have permission to create event!"})
        return super().create(request, *args, **kwargs)

    @swagger_auto_schema(
        request_body=serializers.EventSerializer,
        responses={
            200: openapi.Response('Successfully updated', serializers.EventSerializer),
            403: 'Forbidden',
            400: 'Bad Request',
            404: 'Not Found'
        },
        operation_description="Update an existing event. Only the organizer of the event can update it.",
        operation_summary="Update an event"
    )
    def partial_update(self, request, pk=None):
        event = get_object_or_404(Event, pk=pk, active=True)

        if event.organizer_id != request.user:
            return Response({"detail": "You do not have permission to edit this event."}, status=status.HTTP_403_FORBIDDEN)

        e = serializers.EventSerializer(event, data=request.data, partial=True)
        if e.is_valid():
            e.save()
            return Response(e.data)
        return Response(e.errors, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(
        responses={
            200: openapi.Response('Event details', serializers.EventSerializer),
            400: 'Bad Request',
            404: 'Not Found'
        },
        operation_description="Retrieve details of a specific event by ID. Increments the view count.",
        operation_summary="Get event details"
    )
    def retrieve(self, request, *args, **kwargs):
        pk = kwargs.get('pk')
        if not pk or not pk.isdigit():
            return Response({'detail': 'Invalid event ID.'}, status=status.HTTP_400_BAD_REQUEST)

        instance = self.get_object()
        instance.views = F('views') + 1
        instance.save(update_fields=['views'])
        instance.refresh_from_db()
        serializer = self.get_serializer(instance)

        cache.delete('trending:/event/trend/')
        cache.delete('recommend:/event/recommended/')
        return Response(serializer.data)

    @swagger_auto_schema(
        responses={
            200: openapi.Response('Successfully get my events', serializers.EventSerializer),
            403: 'Forbidden'
        },
        operation_description="Get current organizer events. "
                              "The authenticated organizer (from token) will be used automatically as `organizer_id`.",
        operation_summary="Get current organizer events"
    )
    @action(methods=['get'], detail=False, url_path='my_event', permission_classes=[OrganizerPermission])
    def get_my_event(self, request):
        events = self.get_queryset().filter(organizer_id=request.user)
        e = self.get_serializer(events, many=True)

        return Response(e.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        responses={
            204: 'No Content',
            403: 'Forbidden',
            400: 'Bad Request',
            404: 'Not Found'
        },
        operation_description="Soft delete an event. Only the organizer or admin can delete it.\n"
                              "Cannot delete if there are pending or successful invoices.",
        operation_summary="Delete an event"
    )
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
        cache.delete('trending:/event/trend/')
        cache.delete('recommend:/event/recommended/')
        return Response(status=status.HTTP_204_NO_CONTENT)

    @swagger_auto_schema(manual_parameters=[
        openapi.Parameter('category_id', openapi.IN_QUERY, description="Filter with category_id",
                          type=openapi.TYPE_INTEGER),
        openapi.Parameter('search', openapi.IN_QUERY, description="Search by keyword", type=openapi.TYPE_STRING)],
        operation_description="Retrieve a list of events",
        operation_summary="List all events"
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        responses={200: serializers.EventSerializer(many=True)},
        operation_description="Retrieve the top 10 trending events based on views, reviews, and ticket sales.",
        operation_summary="Get trending events"
    )
    @action(methods=['get'], detail=False, permission_classes=[permissions.AllowAny])
    @method_decorator(cache_page(60 * 5, key_prefix='trending_event'))
    def trend(self, request):
        events = self.get_queryset().annotate(
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

    @swagger_auto_schema(
        responses={
            200: serializers.EventSerializer(many=True),
            401: 'Unauthorized'
        },
        operation_description="Retrieve AI-recommended events for the authenticated participant based on preferences "
                              "and favorites.",
        operation_summary="Get recommended events"
    )
    @action(methods=['get'], detail=False, permission_classes=[ParticipantPermission], url_path='recommended')
    @method_decorator(cache_page(60 * 5, key_prefix='recommend'))
    def recommended(self, request):
        user = request.user
        if not user.is_authenticated:
            return Response({'detail': 'Authentication required'}, status=401)

        try:
            events = recommender.recommend_events(user, limit=10)
        except Exception as e:
            logging.error(f"AI recommendation failed: {str(e)}")

            preferred_categories = UserPreference.objects.filter(user=user).values_list('category_id', flat=True)
            favorite_categories = FavoriteEvent.objects.filter(participant_id=user).values_list('event_id__category_id', flat=True)
            category_ids = set(preferred_categories).union(favorite_categories)

            if not category_ids:
                events = self.get_queryset().order_by('-views')[:10]
            else:
                favorite_event_ids = FavoriteEvent.objects.filter(participant_id=user).values_list('event_id', flat=True)
                events = Event.objects.filter(
                    active=True,
                    category_id__in=category_ids
                ).exclude(
                    id__in=favorite_event_ids
                ).order_by('-views')[:10]

        serializer = serializers.EventSerializer(events, many=True)
        return Response(serializer.data)


# User API view:
class UserViewSet(viewsets.ViewSet, generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = serializers.UserSerializer
    parser_classes = [parsers.MultiPartParser]

    # Only user owned account can get and patch their data
    @swagger_auto_schema(
        request_body=serializers.UserSerializer,
        responses={
            201: openapi.Response('Successfully created', serializers.UserSerializer),
            400: 'Bad Request'
        },
        operation_description="Create a new user account.",
        operation_summary="Register a new user"
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @swagger_auto_schema(
        methods=['get'],
        responses={
            200: openapi.Response('User details', serializers.UserSerializer),
            401: 'Unauthorized'
        },
        operation_description="Retrieve the authenticated user's profile.",
        operation_summary="Get user profile"
    )
    @swagger_auto_schema(
        methods=['patch'],
        request_body=serializers.UserSerializer,
        responses={
            200: openapi.Response('Successfully updated', serializers.UserSerializer),
            400: 'Bad Request',
            401: 'Unauthorized',
            403: 'Forbidden'
        },
        operation_description="Update the authenticated user's profile. Role cannot be changed via API.",
        operation_summary="Update user profile"
    )
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

    @swagger_auto_schema(
        responses={200: serializers.TicketSerializer(many=True)},
        operation_description="List all tickets. Only accessible to authenticated users.",
        operation_summary="List all tickets"
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        responses={
            200: serializers.TicketSerializer(many=True),
            403: 'Forbidden'
        },
        operation_description="Retrieve all active tickets belonging to the authenticated participant.",
        operation_summary="Get participant's tickets"
    )
    @action(detail=False, methods=['get'], url_path='my_ticket', permission_classes=[permissions.IsAuthenticated])
    def get_current_user_ticket(self, request):
        if request.user.role != 'participant':
            return Response({'detail': 'Only participants can get their ticket!'}, status=status.HTTP_403_FORBIDDEN)

        tickets = Ticket.objects.select_related('invoice_id__event_id').filter(invoice_id__user_id=request.user, active=True)
        tk = self.get_serializer(tickets, many=True)

        return Response(tk.data)

    @swagger_auto_schema(
        request_body=serializers.QRCodeCheckInSerializer,
        responses={
            200: openapi.Response('Check-in successful', openapi.Schema(type=openapi.TYPE_OBJECT, properties={
                'detail': openapi.Schema(type=openapi.TYPE_STRING)})),
            400: 'Bad Request',
            403: 'Forbidden',
            404: 'Not Found'
        },
        operation_description="Check in a ticket using QR code data. Only the event organizer can perform this action.",
        operation_summary="Check in a ticket"
    )
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

    @swagger_auto_schema(
        responses={200: serializers.DiscountSerializer(many=True)},
        operation_description="List all discounts.",
        operation_summary="List all discounts"
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        responses={
            200: serializers.DiscountSerializer(many=True),
            403: 'Forbidden'
        },
        operation_description="Retrieve valid discounts for the authenticated participant's membership tier.",
        operation_summary="Get participant's valid discounts"
    )
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

    @swagger_auto_schema(
        request_body=serializers.DiscountSerializer,
        responses={
            201: openapi.Response('Successfully created', serializers.DiscountSerializer),
            400: 'Bad Request',
            403: 'Forbidden'
        },
        operation_description="Create a new discount. Only admins can perform this action.",
        operation_summary="Create a discount"
    )
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

    @swagger_auto_schema(
        responses={200: serializers.InvoiceSerializer(many=True)},
        operation_description="List all invoices for the authenticated user. Admins can see all invoices.",
        operation_summary="List user's invoices"
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        request_body=serializers.InvoiceSerializer,
        responses={
            201: openapi.Response('Successfully created', serializers.InvoiceSerializer),
            400: 'Bad Request',
            403: 'Forbidden'
        },
        operation_description="Create a new invoice for ticket purchase. Only participants can create invoices. "
                              "The authenticated user (from token) will be used automatically as `user_id`.",
        operation_summary="Create an invoice"
    )
    def create(self, request):
        if request.user.role != 'participant':
            return Response({'detail': 'Only participant can buy ticket!'}, status=status.HTTP_403_FORBIDDEN)

        event = get_object_or_404(Event, pk=request.data['event_id'], active=True)
        invoice_existed = Invoice.objects.filter(event_id=event, payment_status='success')
        ticket_remain = event.ticket_quantity - invoice_existed.aggregate(ticket_existed=Sum('ticket_count'))['ticket_existed'] or 0
        ticket_buy = int(request.data['ticket_count'])

        if ticket_buy > ticket_remain:
            return Response({'detail': 'Your number of tickets you bought is larger than the remaining quantity!'},
                            status=status.HTTP_400_BAD_REQUEST)

        invoice = self.serializer_class(data=request.data, context={'request': request})
        if invoice.is_valid():
            invoice.validated_data['user_id'] = request.user
            invoice.save()
            return Response(invoice.data, status=status.HTTP_201_CREATED)
        return Response(invoice.errors, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(
        responses={
            200: serializers.InvoiceSerializer(many=True),
            403: 'Forbidden'
        },
        operation_description="Retrieve all invoices belonging to the authenticated participant.",
        operation_summary="Get participant's invoices"
    )
    @action(methods=['get'], detail=False, url_path='my_invoice', permission_classes=[permissions.IsAuthenticated])
    def get_current_user_valid_invoice(self, request):
        if request.user.role != 'participant':
            return Response({'detail': 'Only participant can have invoice!'}, status=status.HTTP_403_FORBIDDEN)

        invoices = Invoice.objects.filter(
            user_id=request.user
        )

        iv = self.get_serializer(invoices, many=True)
        return Response(iv.data)

    @swagger_auto_schema(
        responses={
            200: openapi.Response('Payment URL', openapi.Schema(type=openapi.TYPE_OBJECT, properties={
                'pay_url': openapi.Schema(type=openapi.TYPE_STRING)})),
            400: 'Bad Request',
            404: 'Not Found'
        },
        operation_description="Generate a MoMo payment URL for a pending invoice. Only the invoice owner can initiate payment.",
        operation_summary="Initiate MoMo payment"
    )
    @action(methods=['post'], detail=True, url_path='momo-payment')
    def momo_payment(self, request, pk=None):
        invoice = get_object_or_404(Invoice, pk=pk, user_id=request.user, payment_status='pending')
        try:
            pay_url = create_momo_payment(invoice, request_id=invoice.invoice_code)
            return Response({'pay_url': pay_url}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(
        request_body=openapi.Schema(type=openapi.TYPE_OBJECT, properties={
            'orderId': openapi.Schema(type=openapi.TYPE_STRING),
            'resultCode': openapi.Schema(type=openapi.TYPE_INTEGER),
            'transId': openapi.Schema(type=openapi.TYPE_STRING),
            'message': openapi.Schema(type=openapi.TYPE_STRING)
        }),
        responses={
            200: openapi.Response('Success', openapi.Schema(type=openapi.TYPE_OBJECT, properties={
                'status': openapi.Schema(type=openapi.TYPE_STRING)})),
            400: 'Bad Request',
            404: 'Not Found'
        },
        operation_description="Handle MoMo IPN (Instant Payment Notification) to update invoice status and create tickets.",
        operation_summary="Handle MoMo IPN"
    )
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

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('orderId', openapi.IN_QUERY, description="MoMo order ID", type=openapi.TYPE_STRING),
            openapi.Parameter('resultCode', openapi.IN_QUERY, description="MoMo result code", type=openapi.TYPE_STRING)
        ],
        responses={
            302: 'Redirect to payment success or failure page'
        },
        operation_description="Handle MoMo return URL to redirect users to success or failure page based on payment result.",
        operation_summary="Handle MoMo return URL"
    )
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

        return Review.objects.filter(event_id=event_id, active=True).select_related('participant_id', 'event_id')

    @swagger_auto_schema(
        responses={200: serializers.ReviewSerializer(many=True)},
        operation_description="List all reviews of a event.",
        operation_summary="List all reviews of a event."
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        request_body=serializers.ReviewSerializer,
        responses={
            201: openapi.Response('Successfully created', serializers.ReviewSerializer),
            400: 'Bad Request',
            403: 'Forbidden',
            404: 'Not Found'
        },
        operation_description="Create a new review for an event. The authenticated participant (from token) will be used "
                              "automatically as `participant_id`. Do NOT send it in request.",
        operation_summary="Create a review"
    )
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

    @swagger_auto_schema(
        responses={200: openapi.Response('Successfully update the review', serializers.ReviewSerializer),
                   403: 'Forbidden', 400: 'Bad Request (Not valid input)'},
        operation_description="The authenticated participant (from token) will be used \n"
                              "automatically as `participant_id`. Do NOT sent it in request",
        operation_summary="Participant change a review content that they wrote"
    )
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

    @swagger_auto_schema(
        responses={200: 'OK'},
        operation_description="Anyone can access this API",
        operation_summary="User read a review statistics"
    )
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

    @swagger_auto_schema(
        responses={204: 'No Content', 403: 'Forbidden'},
        operation_description="The authenticated participant (from token) will be used \n"
                              "automatically as `participant_id`. Do NOT sent it in request",
        operation_summary="Participants delete a review"
    )
    @action(methods=['delete'], detail=True, url_path='', permission_classes=[permissions.IsAuthenticated])
    def delete_review(self, request, event_id=None, pk=None):
        event = get_object_or_404(Event, pk=event_id, active=True)
        review = get_object_or_404(Review, pk=pk, event_id=event, active=True)
        if request.user != review.participant_id and request.user.role != 'admin':
            return Response({"detail": "You do not have permission to delete this review!!!"},
                            status=status.HTTP_403_FORBIDDEN)
        review.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @swagger_auto_schema(
        methods=['POST'],
        request_body=serializers.ReviewResponseSerializer,
        responses={201: serializers.ReviewResponseSerializer(many=False)},
        operation_description="Only the organizer of the event can respond to its reviews. Each review can have only one active response, enforced by logic.",
        operation_summary="Create a response to a review"
    )
    @swagger_auto_schema(
        methods=['GET'],
        responses={200: serializers.ReviewResponseSerializer(many=True)},
        operation_description="Retrieve the single active response to a review, if it exists, as a list. Returns an empty list if no active response exists.",
        operation_summary="Get the response to a review"
    )
    @swagger_auto_schema(
        methods=['PATCH'],
        request_body=serializers.ReviewResponseSerializer,
        responses={200: serializers.ReviewResponseSerializer(many=False)},
        operation_description="Only the organizer who created the response can update it.",
        operation_summary="Update a response to a review"
    )
    @swagger_auto_schema(
        methods=['DELETE'],
        responses={204: 'No Content'},
        operation_description="Only the organizer who created the response can soft delete it by setting active=False.",
        operation_summary="Delete a response to a review"
    )
    @action(methods=['get', 'post', 'patch', 'delete'], detail=True, url_path='response', permission_classes=[permissions.AllowAny])
    def response(self, request, event_id=None, pk=None):
        event = get_object_or_404(Event, pk=event_id, active=True)
        review = get_object_or_404(Review, pk=pk, event_id=event, active=True)

        if request.method == 'GET':
            self.permission_classes = [permissions.AllowAny]

        if request.method in ['POST', 'PATCH', 'DELETE'] and request.user != event.organizer_id:
            return Response({"detail": "You do not have permission to reply this review."}, status=status.HTTP_403_FORBIDDEN)

        if request.method == 'POST':
            if ReviewResponse.objects.filter(review_id=review, active=True).exists():
                return Response({"detail": "You have already response this review"}, status=status.HTTP_400_BAD_REQUEST)
            serializer = serializers.ReviewResponseSerializer(
                data=request.data,
                context={'request': request, 'review': review}
            )

            if serializer.is_valid():
                serializer.save(organizer_id=request.user, review_id=review)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        elif request.method == 'GET':
            responses = review.responses.filter(active=True)
            serializer = serializers.ReviewResponseSerializer(responses, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        elif request.method == 'PATCH':
            responses = review.responses.filter(organizer_id=request.user, active=True)
            if not responses.exists():
                return Response({"detail": "No active response found to update."}, status=status.HTTP_404_NOT_FOUND)
            response = responses.first()
            serializer = serializers.ReviewResponseSerializer(
                response,
                data=request.data,
                partial=True,
                context={'request': request, 'review': review}
            )
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_200_OK)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        elif request.method == 'DELETE':
            responses = review.responses.filter(organizer_id=request.user, active=True)
            if not responses.exists():
                return Response({"detail": "No active response found to delete."}, status=status.HTTP_404_NOT_FOUND)
            response = responses.first()
            response.active = False
            response.save()
            return Response(status=status.HTTP_204_NO_CONTENT)


class ReportViewSet(viewsets.ViewSet):
    permission_classes = [OrganizerPermission]

    @swagger_auto_schema(
        responses={200: openapi.Response("Successfully get dashboard", serializers.OrganizerDashboardSerializer)},
        operation_description="Retrieve organizer dashboard with event statistics.\n"
                              "The authenticated organizer (from token) will be used automatically as "
                              "`organizer_id`. Do NOT sent it in request",
        operation_summary="Retrieve dashboard with statistics"
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
        operation_description="Retrieve monthly report with ticket and revenue statistics for the organizer.\n"
                              "The authenticated organizer (from token) will be used automatically as "
                              "`organizer_id`. Do NOT sent it in request",
        operation_summary="Retrieve monthly data about ticket and revenue"
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

    @swagger_auto_schema(responses={200: serializers.FavoriteEventSerializer(many=True)},
                         operation_description="The authenticated participant (from token) will be used \n"
                                               "automatically as `participant_id`. Do NOT sent it in request",
                         operation_summary="Participants read event in their favorite list")
    def list(self, request):
        favorites = FavoriteEvent.objects.filter(participant_id=request.user).select_related('event_id__category_id')
        serializer = serializers.FavoriteEventSerializer(favorites, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(request_body=serializers.FavoriteEventSerializer,
                         responses={201: openapi.Response('Successfully created', serializers.FavoriteEventSerializer)},
                         operation_description="The authenticated participant (from token) will be used \n"
                                               "automatically as `participant_id`. Do NOT sent it in request",
                         operation_summary="Participants add event to favorite list")
    def create(self, request):
        serializer = serializers.FavoriteEventSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            event_id = serializer.validated_data['event_id']
            participant = request.user

            # Kiểm tra đã tồn tại favorite chưa
            if FavoriteEvent.objects.filter(participant_id=participant, event_id=event_id).exists():
                return Response({'detail': 'You have already favorited this event.'},
                                status=status.HTTP_400_BAD_REQUEST)

            serializer.save(participant_id=participant)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(responses={204: openapi.Response('No content')},
                         operation_description="The authenticated participant (from token) will be used \n"
                                               "automatically as `participant_id`. Do NOT sent it in request",
                         operation_summary="Participants delete an event in their favorite list"
                         )
    def destroy(self, request, pk=None):
        favorite = get_object_or_404(FavoriteEvent, participant_id=request.user, event_id__id=pk)
        favorite.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserPreferenceViewSet(viewsets.ViewSet):
    permission_classes = [ParticipantPermission]
    serializer_class = serializers.UserPreferenceSerializer
    queryset = UserPreference.objects.all()

    @swagger_auto_schema(
        responses={200: serializers.UserPreferenceSerializer(many=True)},
        operation_description="The authenticated participant (from token) will be used \n"
                              "automatically as `participant_id`. Do NOT sent it in request",
        operation_summary="Participants read their interest categories"
    )
    def list(self, request):
        preferences = UserPreference.objects.filter(user=request.user)
        serializer = serializers.UserPreferenceSerializer(preferences, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(
        request_body=serializers.UserPreferenceSerializer,
        responses={201: serializers.UserPreferenceSerializer()},
        operation_description="The authenticated participant (from token) will be used \n"
                              "automatically as `participant_id`. Do NOT sent it in request",
        operation_summary="Participants add an category to their interest"
    )
    def create(self, request):
        serializer = serializers.UserPreferenceSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(
        responses={204: 'No Content', 404: 'Not Found'},
        operation_description="The authenticated participant (from token) will be used \n"
                              "automatically as `participant_id`. Do NOT sent it in request",
        operation_summary="Participants delete an category off their interest"
    )
    def destroy(self, request, pk=None):
        preference = get_object_or_404(UserPreference, user=request.user, category__id=pk)
        preference.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)



