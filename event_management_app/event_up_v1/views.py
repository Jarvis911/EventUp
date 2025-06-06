from . import serializers, services, perms, paginators
from .models import Category, Event, Ticket, User, Invoice, Discount, Review, FavoriteEvent, UserPreference, ReviewResponse, Notification
from django.db.models import F, Count, Q, FloatField, ExpressionWrapper, Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework import viewsets, generics, parsers, permissions, status, filters
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404
from django.core.exceptions import ObjectDoesNotExist
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
import requests
from firebase_admin import auth
from oauth2_provider.models import AccessToken, Application


# Category API view:
class CategoryViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = Category.objects.filter(active=True)
    serializer_class = serializers.CategorySerializer

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


# Event API view:
class EventViewSet(viewsets.ViewSet, generics.ListCreateAPIView):
    queryset = Event.objects.filter(active=True)
    serializer_class = serializers.EventSerializer
    parser_classes = [parsers.MultiPartParser]
    pagination_class = paginators.EventPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['category_id', 'location', 'start_time']
    search_fields = ['title']

    def get_permissions(self):
        if self.action in ['get_my_event', 'create']:
            return [perms.OrganizerPermission()]
        if self.action in ['partial_update', 'destroy']:
            return [perms.OrganizerPermission(), perms.EventOwnerPermission()]
        return [permissions.AllowAny()]

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.select_related('category_id')

    def perform_create(self, serializer):
        serializer.save(organizer_id=self.request.user)

    def partial_update(self, request, pk=None):
        event = get_object_or_404(Event, pk=pk, active=True)
        self.check_object_permissions(request, event)

        e = self.get_serializer(event, data=request.data, partial=True)
        if e.is_valid():
            e.save()

            invoices = Invoice.objects.filter(event_id=event, payment_status='success').select_related('user_id')
            push_tokens = [inv.user_id.push_token for inv in invoices if inv.user_id.push_token]

            for token in push_tokens:
                message = {
                    "to": token,
                    "sound": "default",
                    "title": "Event update",
                    "body": f"Event '{event.title}' has been updated, please check for more detail information!",
                }
                try:
                    requests.post("https://exp.host/--/api/v2/push/send", json=message)
                except Exception as ex:
                    print(f"Push failed for token {token}: {ex}")

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

    @action(methods=['get'], detail=False, url_path='my_event')
    def get_my_event(self, request):
        events = self.get_queryset().filter(organizer_id=request.user)
        e = self.get_serializer(events, many=True)

        return Response(e.data, status=status.HTTP_200_OK)

    def destroy(self, request, pk=None):
        event = get_object_or_404(Event, pk=pk, active=True)
        self.check_object_permissions(request, event)

        if Invoice.objects.filter(event_id=event, payment_status__in=['pending', 'success']).exists():
            return Response({"detail": "You can not delete this event because it has associated invoices!"},
                            status=status.HTTP_400_BAD_REQUEST)

        event.active = False
        event.save()

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(methods=['get'], detail=False, url_path='trend')
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

    @action(methods=['get'], detail=False, permission_classes=[perms.ParticipantPermission], url_path='recommended')
    def recommended(self, request):
        user = request.user
        try:
            events = recommender.recommend_events(user, limit=10)
        except Exception as e:
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
    permission_classes = [permissions.IsAuthenticated]

    @action(methods=['get', 'patch'], url_path="me", detail=False)
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

    @action(methods=['post'], url_path='save-push-token', detail=False)
    def update_push_token(self, request):
        push_token = request.data.get('push_token')
        if not push_token:
            return Response({'error': 'Push token is required'}, status=status.HTTP_400_BAD_REQUEST)

        request.user.push_token = push_token
        request.user.save()

        return Response({'message': 'Push token updated successfully'}, status=status.HTTP_200_OK)

    @action(methods=['post'], url_path='google-login', detail=False, permission_classes=[permissions.AllowAny])
    def google_login(self, request):
        firebase_token = request.data.get('id_token')
        if not firebase_token:
            return Response({'error': 'Missing Firebase ID token'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Verify Firebase ID token
            decoded_token = auth.verify_id_token(firebase_token)
            email = decoded_token.get('email')
            uid = decoded_token.get('sub')  # Google user ID
            name = decoded_token.get('name', '')

            # Create or get user
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    'username': email.split('@')[0],
                    'first_name': name,
                    'role': 'participant',  # Vai trò mặc định
                }
            )

            import secrets

            def generate_oauth2_token():
                return secrets.token_urlsafe(32)

            # Generate OAuth2 token
            app = Application.objects.get(name='Event Up')  # Match name in admin
            token, _ = AccessToken.objects.get_or_create(
                user=user,
                application=app,
                expires=timezone.now() + timedelta(seconds=3600),
                defaults={'token': generate_oauth2_token()}
            )

            return Response({
                'access_token': token.token,
                'expires_in': 3600,
                'user': {
                    'username': user.username,
                    'email': user.email,
                    'name': user.first_name,
                    'role': user.role,
                }
            }, status=status.HTTP_200_OK)
        except auth.InvalidIdTokenError:
            return Response({'error': 'Invalid Firebase ID token'}, status=status.HTTP_401_UNAUTHORIZED)


class TicketViewSet(viewsets.GenericViewSet):
    queryset = Ticket.objects.all()
    serializer_class = serializers.TicketSerializer

    def get_permissions(self):
        if self.action in ['get_current_user_ticket']:
            return [perms.ParticipantPermission()]
        if self.action in ['check_in']:
            return [perms.OrganizerPermission(), perms.TicketHostPermission()]
        return [permissions.IsAuthenticated]

    @action(detail=False, methods=['get'], url_path='my_ticket')
    def get_current_user_ticket(self, request):
        tickets = Ticket.objects.select_related('invoice_id__event_id').filter(invoice_id__user_id=request.user, active=True)
        tk = self.get_serializer(tickets, many=True)

        return Response(tk.data)

    @action(methods=['post'], detail=False, url_path='check_in')
    def check_in(self, request, pk=None):
        serializer = serializers.QRCodeCheckInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        qr_code_data = serializer.validated_data['qr_code_data']
        if not qr_code_data:
            return Response({'detail': 'QR code data is required!'}, status=status.HTTP_400_BAD_REQUEST)

        result = services.check_in_ticket(qr_code_data, validate_only=True)
        ticket = get_object_or_404(Ticket, pk=result['ticket_id'], active=True)
        self.check_object_permissions(request, ticket)

        result = services.check_in_ticket(qr_code_data)
        if not result.get('success'):
            return Response({'detail': result.get('message', 'Check-in failed')}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': result['message']}, status=status.HTTP_200_OK)


class DiscountViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = Discount.objects.all()
    serializer_class = serializers.DiscountSerializer

    @action(methods=['get'], detail=False, url_path='my_discount', permission_classes=[perms.ParticipantPermission])
    def get_current_user_valid_discount(self, request):
        now = timezone.now()
        discounts = Discount.objects.filter(active=True,
                                            valid_from__lte=now,
                                            valid_until__gte=now,
                                            max_usage__gt=F('used_count'),
                                            membership_tier=request.user.membership_tier)

        dc = self.get_serializer(discounts, many=True)
        return Response(dc.data)

    @action(methods=['post'], detail=False, url_path='create_discount', permission_classes=[perms.AdminPermission])
    def create_discount(self, request):
        dc = self.get_serializer(data=request.data)
        if dc.is_valid():
            dc.save()
            return Response(dc.data, status=status.HTTP_201_CREATED)
        return Response(dc.errors, status=status.HTTP_400_BAD_REQUEST)


class InvoiceViewSet(viewsets.ViewSet, generics.RetrieveAPIView, generics.ListAPIView):
    serializer_class = serializers.InvoiceSerializer

    def get_permissions(self):
        if self.action in ['create']:
            return [perms.ParticipantPermission()]
        if self.action in ['momo_payment']:
            return [perms.ParticipantPermission(), perms.InvoiceOwnerPermission()]
        return [permissions.AllowAny]

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
        event = get_object_or_404(Event, pk=request.data['event_id'], active=True)
        invoice_existed = Invoice.objects.filter(event_id=event, payment_status='success')
        ticket_remain = event.ticket_quantity - (invoice_existed.aggregate(ticket_existed=Sum('ticket_count'))['ticket_existed'] or 0)
        ticket_buy = int(request.data['ticket_count'])

        if ticket_buy > ticket_remain:
            return Response({'detail': 'Your number of tickets you bought is larger than the remaining quantity!'},
                            status=status.HTTP_400_BAD_REQUEST)

        invoice = self.serializer_class(data=request.data, context={'request': request})
        if invoice.is_valid():
            invoice.validated_data['user_id'] = request.user
            event.ticket_sold = F('ticket_sold') + ticket_buy
            event.save()
            event.refresh_from_db()
            invoice.save()
            return Response(invoice.data, status=status.HTTP_201_CREATED)
        return Response(invoice.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=True, url_path='momo-payment')
    def momo_payment(self, request, pk=None):
        invoice = get_object_or_404(Invoice, pk=pk, user_id=request.user, payment_status='pending')
        try:
            pay_url = create_momo_payment(invoice, request_id=invoice.invoice_code)
            return Response({'pay_url': pay_url}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=False, url_path='momo/ipn')
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

    @action(methods=['get'], detail=False, url_path='momo/return')
    def momo_return(self, request):
        order_id = request.query_params.get('orderId')
        result_code = request.query_params.get('resultCode')
        invoice_code = order_id.split('-')[0]
        invoice = get_object_or_404(Invoice, invoice_code=invoice_code)

        if result_code == '0':
            return redirect('eventup://payment/success')
        else:
            return redirect('eventup://payment/fail')


class ReviewViewSet(viewsets.ViewSet, generics.ListAPIView):
    def get_permissions(self):
        if self.action in ['create']:
            return [perms.ParticipantPermission()]
        if self.action in ['partial_update', 'destroy']:
            return [perms.ParticipantPermission(), perms.ReviewOwnerPermission()]
        return [permissions.AllowAny()]

    def get_serializer_class(self):
        if self.action == 'get_review_stats':
            return serializers.ReviewStatsSerializer
        return serializers.ReviewSerializer

    def get_queryset(self):
        event_id = self.kwargs.get('event_id')
        return Review.objects.filter(event_id=event_id, active=True).select_related('participant_id', 'event_id')

    def create(self, request, event_id=None):
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

    def partial_update(self, request, event_id=None, pk=None):
        event = get_object_or_404(Event, pk=event_id, active=True)
        review = get_object_or_404(Review, pk=pk, event_id=event, active=True)

        self.check_object_permissions(request, review)

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

    def destroy(self, request, event_id=None, pk=None):
        event = get_object_or_404(Event, pk=event_id, active=True)
        review = get_object_or_404(Review, pk=pk, event_id=event, active=True)
        self.check_object_permissions(request, review)

        review.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(methods=['get'], detail=False, url_path='stats', permission_classes=[permissions.AllowAny])
    def get_review_stats(self, request, event_id=None):
        event = get_object_or_404(Event, pk=event_id, active=True)
        reviews = Review.objects.filter(event_id=event, active=True)
        count = reviews.count()
        avg_rating = reviews.aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0

        return Response({
            'event_id': int(event.id),
            'review_count': count,
            'average_rating': round(avg_rating, 1) if avg_rating else 0.0
        }, status=status.HTTP_200_OK)


class ResponseViewSet(viewsets.GenericViewSet):
    serializer_class = serializers.ReviewResponseSerializer

    def get_permissions(self):
        if self.action in ['create']:
            return [perms.OrganizerPermission]
        if self.action in ['partial_update', 'destroy']:
            return [perms.OrganizerPermission, perms.ResponseOwnerPermission]
        return [permissions.AllowAny]

    def get_queryset(self):
        event_id = self.kwargs.get('event_id')
        review_id = self.kwargs.get('review_id')
        return ReviewResponse.objects.filter(review_id=review_id, review_id__event_id=event_id, active=True)

    def perform_create(self, serializer):
        review = get_object_or_404(Review, pk=self.kwargs['review_id'])
        serializer.save(organizer_id=self.request.user, review_id=review)

    def get_review(self):
        event_id = self.kwargs.get('event_id')
        review_id = self.kwargs.get('review_id')
        return get_object_or_404(Review, pk=review_id, event_id=event_id, active=True)

    def list(self, request, *args, **kwargs):
        review = self.get_review()
        responses = review.responses.filter(active=True)
        serializer = self.get_serializer(responses, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        review = self.get_review()
        if ReviewResponse.objects.filter(review_id=review, active=True).exists():
            return Response({"detail": "You have already response this review"}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(
            data=request.data,
            context={'request': request, 'review': review}
        )

        if serializer.is_valid():
            self.perform_create(serializer)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs):
        review = self.get_review()
        responses = review.responses.filter(organizer_id=request.user, active=True)

        self.check_object_permissions(request, responses)

        if not responses.exists():
            return Response({"detail": "No active response found to update."}, status=status.HTTP_404_NOT_FOUND)
        response = responses.first()
        serializer = self.get_serializer(
            response,
            data=request.data,
            partial=True,
            context={'request': request, 'review': review}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None, *args, **kwargs):
        review = self.get_review()
        responses = review.responses.filter(organizer_id=request.user, active=True)
        self.check_object_permissions(request, responses)

        if not responses.exists():
            return Response({"detail": "No active response found to delete."}, status=status.HTTP_404_NOT_FOUND)
        response = responses.first()
        response.active = False
        response.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReportViewSet(viewsets.ViewSet):
    permission_classes = [perms.OrganizerPermission]

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


class FavoriteEventViewSet(viewsets.GenericViewSet):
    queryset = FavoriteEvent.objects.all()
    serializer_class = serializers.FavoriteEventSerializer

    def get_permissions(self):
        if self.action in ['destroy']:
            return [perms.ParticipantPermission, perms.FavoriteOwnerPermission]
        return [perms.ParticipantPermission]

    def list(self, request):
        favorites = FavoriteEvent.objects.filter(participant_id=request.user).select_related('event_id__category_id')
        serializer = self.get_serializer(favorites, many=True)
        return Response(serializer.data)

    def create(self, request):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            event_id = serializer.validated_data['event_id']
            participant = request.user

            if FavoriteEvent.objects.filter(participant_id=participant, event_id=event_id).exists():
                return Response({'detail': 'You have already favorited this event.'},
                                status=status.HTTP_400_BAD_REQUEST)

            serializer.save(participant_id=participant)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None):
        favorite = get_object_or_404(FavoriteEvent, participant_id=request.user, event_id__id=pk)
        favorite.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserPreferenceViewSet(viewsets.GenericViewSet):
    queryset = UserPreference.objects.all()
    permission_classes = [perms.ParticipantPermission]
    serializer_class = serializers.UserPreferenceSerializer

    def list(self, request):
        preferences = UserPreference.objects.filter(user=request.user)
        serializer = self.get_serializer(preferences, many=True)
        return Response(serializer.data)

    def create(self, request):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)




