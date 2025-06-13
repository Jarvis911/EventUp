from . import serializers, services, perms, paginators, utils, recommender
from .models import Category, Event, Ticket, User, Invoice, Discount, Review, FavoriteEvent, UserPreference, ReviewResponse, Notification
from django.db.models import F, Count, Q, FloatField, ExpressionWrapper, Sum, Avg
from django.utils import timezone
from rest_framework.response import Response
from rest_framework import viewsets, generics, parsers, permissions, status, filters
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404, redirect
from django.db.models.functions import Coalesce
# Filter backend
from django_filters.rest_framework import DjangoFilterBackend
# Recommend
from firebase_admin import auth


# Category API view:
class CategoryViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = Category.objects.filter(active=True)
    serializer_class = serializers.CategorySerializer


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
        if self.action in ['get_reviews']:
            if self.request.method.__eq__('POST'):
                return [perms.ParticipantPermission()]
        if self.action in ['recommended']:
            return [perms.ParticipantPermission()]
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
            utils.send_push_notification_for_updating(invoices, event)
            return Response(e.data)
        return Response(e.errors, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, *args, **kwargs):
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

    @action(methods=['get', 'post'], detail=True, url_path='reviews')
    def get_reviews(self, request, pk):
        if request.method.__eq__('POST'):
            event = self.get_object()
            rv = serializers.ReviewSerializer(data={
                'participant_id': request.user.event,
                'event_id': event,
                'rating': request.data.get('rating'),
                'comment': request.data.get('comment')
            }, context={
                'request': request,
                'event': event
            })

            if rv.is_valid():
                r = rv.save()
                avg_rating = event.review_set.aggregate(avg=Avg('rating'))['avg'] or 0.0
                event.avg_rating = round(avg_rating, 1)
                event.save(update_fields=['avg_rating'])
                return Response(r.data, status=status.HTTP_201_CREATED)

        rv = self.get_object().review_set.select_related('participant_id').filter(active=True)
        return Response(serializers.ReviewSerializer(rv, many=True).data, status=status.HTTP_200_OK)

    @action(methods=['get'], detail=True, url_path='stats')
    def get_stats(self, request, pk):
        event = get_object_or_404(Event, pk=pk)
        reviews = event.review_set.filter(active=True)
        count = reviews.count()
        avg_rating = reviews.aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0

        return Response({
            'event_id': int(event.id),
            'review_count': count,
            'average_rating': round(avg_rating, 1) if avg_rating else 0.0
        }, status=status.HTTP_200_OK)

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

    @action(methods=['get'], detail=False, url_path='recommended')
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
    permission_classes = [permissions.AllowAny]

    @action(methods=['get', 'patch'], url_path="me", detail=False,  permission_classes=[permissions.IsAuthenticated])
    def get_current_user(self, request):
        u = request.user
        if request.method.__eq__('PATCH'):
            for k, v in request.data.items():
                if k in ['first_name', 'last_name']:
                    setattr(u, k, v)
                elif k.__eq__('password'):
                    u.set_password(v)

            u.save()
        return Response(serializers.UserSerializer(u).data)

    @action(methods=['post'], url_path='save-push-token', detail=False)
    def update_push_token(self, request):
        push_token = request.data.get('push_token')
        if not push_token:
            return Response({'error': 'Push token is required'}, status=status.HTTP_400_BAD_REQUEST)

        request.user.push_token = push_token
        request.user.save()

        return Response({'message': 'Push token updated successfully'}, status=status.HTTP_200_OK)

    @action(methods=['post'], url_path='google-login', detail=False)
    def google_login(self, request):
        firebase_token = request.data.get('id_token')
        if not firebase_token:
            return Response({'error': 'Missing Firebase ID token'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            data = utils.verify_firebase_token(firebase_token)
            user, _ = utils.get_or_create_user_from_firebase(data['email'], data['name'])
            token = utils.generate_oauth2_token(user)

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


class InvoiceViewSet(viewsets.ViewSet, generics.RetrieveAPIView, generics.ListAPIView):
    serializer_class = serializers.InvoiceSerializer

    def get_permissions(self):
        if self.action in ['create', 'list', 'retrieve']:
            return [perms.ParticipantPermission()]
        if self.action in ['momo_payment']:
            return [perms.ParticipantPermission(), perms.InvoiceOwnerPermission()]
        return [permissions.AllowAny()]

    def get_queryset(self):
        user = self.request.user
        return Invoice.objects.filter(user_id=user)

    def perform_create(self, serializer):
        serializer.save(user_id=self.request.user)

    def create(self, request):
        event = get_object_or_404(Event, pk=request.data['event_id'], active=True)
        ticket_buy = int(request.data['ticket_count'])
        ticket_remain = event.ticket_quantity - event.ticket_sold

        if ticket_buy > ticket_remain:
            return Response({'detail': 'Your number of tickets you bought is larger than the remaining quantity!'},
                            status=status.HTTP_400_BAD_REQUEST)

        invoice = self.serializer_class(data=request.data, context={'request': request})
        if invoice.is_valid():
            invoice.save(user_id=request.user)
            Event.objects.filter(pk=event.pk).update(ticket_sold=F('ticket_sold') + ticket_buy)

            return Response(invoice.data, status=status.HTTP_201_CREATED)
        return Response(invoice.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=True, url_path='momo-payment')
    def momo_payment(self, request, pk=None):
        invoice = get_object_or_404(Invoice, pk=pk, user_id=request.user, payment_status='pending')
        try:
            pay_url = utils.create_momo_payment(invoice, request_id=invoice.invoice_code)
            return Response({'pay_url': pay_url}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=False, url_path='momo/ipn')
    def momo_ipn(self, request):
        data = request.data

        # Temporary disable validate signature
        if not utils.verify_momo_payment(data):
            return Response({'detail': 'Invalid signature'}, status=status.HTTP_400_BAD_REQUEST)

        order_id = data.get('orderId')
        invoice_code = order_id.split('-')[0]
        invoice = get_object_or_404(Invoice, invoice_code=invoice_code)

        if data.get('resultCode') == 0:
            invoice.payment_status = 'success'
            invoice.transaction_id = data.get('transId')
            invoice.save()
            services.create_tickets_after_payment(invoice)
            utils.send_notification(
                user=invoice.user_id,
                title=f"Payment Successful for {invoice.event_id.title}",
                message=f"Your payment of {invoice.final_amount} for {invoice.event_id.title} was successful. Invoice: {invoice.invoice_code}"
            )
        else:
            invoice.payment_status = 'fail'
            invoice.save()
            utils.send_notification(
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
            return redirect('payment_success')
        else:
            return redirect('payment_fail')


class ReviewViewSet(viewsets.ViewSet, generics.UpdateAPIView):
    queryset = Review.objects.filter(active=True)
    serializer_class = serializers.ReviewSerializer

    def get_permissions(self):
        if self.action == 'get_response':
            if self.request.method.__eq__('GET'):
                return [permissions.AllowAny()]
            elif self.request.method.__eq__('POST'):
                return [perms.OrganizerPermission()]
        elif self.action in ['update', 'partial_update', 'destroy']:
            return [perms.ReviewOwnerPermission(), perms.ParticipantPermission()]

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        event = instance.event_id
        instance.delete()
        # Recalculate avg_rating
        reviews = Review.objects.filter(event_id=event.id, active=True)
        event.avg_rating = round(reviews.aggregate(avg=Avg('rating'))['avg'] or 0.0, 1)
        event.save(update_fields=['avg_rating'])

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(methods=['get', 'post'], detail=True, url_path='response')
    def get_response(self, request, pk):
        if request.method.__eq__('POST'):
            rs = serializers.ReviewResponseSerializer(data={
                'organizer_id': request.user.pk,
                'review_id': pk,
                'response': request.data.get('response')
            }, context={
                'request': request,
                'review': pk
            })

            rs.is_valid(raise_exception=True)
            response = rs.save()
            return Response(response.data, status=status.HTTP_201_CREATED)

        responses = self.get_object().reviewresponse_set.filter(active=True)
        return Response(serializers.ReviewSerializer(responses).data, status=status.HTTP_200_OK)


class ReportViewSet(viewsets.ViewSet):
    permission_classes = [perms.OrganizerPermission]

    @action(methods=['get'], detail=False, url_path='organizer/dashboard')
    def organizer_dashboard(self, request):
        organizer = request.user
        data = utils.get_organizer_dashboard_data(organizer)
        dashboard = serializers.OrganizerDashboardSerializer(data)
        return Response(dashboard.data, status=status.HTTP_200_OK)

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

        data = utils.get_monthly_report_data(organizer, year, month)
        report = serializers.MonthlyReportSerializer(data)
        return Response(report.data, status=status.HTTP_200_OK)


class FavoriteEventViewSet(viewsets.ViewSet, generics.DestroyAPIView, generics.ListAPIView):
    serializer_class = serializers.FavoriteEventSerializer

    def get_queryset(self):
        return FavoriteEvent.objects.filter(participant_id=self.request.user).select_related('event_id__category_id')

    def get_permissions(self):
        if self.action in ['destroy']:
            return [perms.ParticipantPermission(), perms.FavoriteOwnerPermission()]
        return [perms.ParticipantPermission()]

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid(raise_exception=True):
            event_id = serializer.validated_data['event_id']
            participant = request.user

            if FavoriteEvent.objects.filter(participant_id=participant, event_id=event_id).exists():
                 return Response({'detail': 'You have already favorited this event.'},
                                            status=status.HTTP_400_BAD_REQUEST)

            serializer.save(participant_id=participant)
            return Response(serializer.data, status=status.HTTP_201_CREATED)


class UserPreferenceViewSet(viewsets.GenericViewSet, generics.CreateAPIView):
    queryset = UserPreference.objects.all()
    permission_classes = [perms.ParticipantPermission]
    serializer_class = serializers.UserPreferenceSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ResponseViewSet(viewsets.GenericViewSet, generics.UpdateAPIView, generics.DestroyAPIView):
    queryset = ReviewResponse.objects.filter(active=True)
    serializer_class = serializers.ReviewResponseSerializer
    permission_classes = [perms.OrganizerPermission, perms.ResponseOwnerPermission]



