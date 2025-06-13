from rest_framework.serializers import ModelSerializer
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from django.utils import timezone
from .models import Event, Category, Ticket, User, Discount, Invoice, Review, FavoriteEvent, UserPreference, ReviewResponse,  Notification
from django.core.signing import Signer
# To call API
import requests


# Override to_presentation to show Cloudinary url
class BaseSerializer(ModelSerializer):
    def to_representation(self, instance):
        d = super().to_representation(instance)
        d['image'] = instance.image.url if instance.image else None # Check if image is null
        return d


# Serializer for EventType
class CategorySerializer(ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name']


# Serializer for user
class UserSerializer(ModelSerializer):
    role = serializers.ChoiceField(
        choices=[('participant', 'Participant'), ('organizer', 'Organizer')],
        default='participant'
    )
    avatar = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'password', 'first_name', 'last_name', 'email', 'role', 'membership_tier', 'avatar']
        read_only_fields = ['id', 'membership_tier']
        extra_kwargs = {
            'password': {'write_only': True},
            'email': {'required': True}
        }

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['avatar'] = instance.avatar.url if instance.avatar else None
        return data

    def create(self, validated_data):
        data = validated_data.copy()
        user = User(**data)
        user.set_password(user.password)
        user.save()
        return user

    def update(self, instance, validated_data):
        data = validated_data.copy()
        if data.password:
            instance.set_password(data.password)
        for attr, value in data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


# Serializer for Event
class EventSerializer(BaseSerializer):
    category = CategorySerializer(source='category_id', read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all()
    )
    organizer = UserSerializer(source='organizer_id', read_only=True)

    class Meta:
        model = Event
        fields = ['id', 'title', 'category', 'category_id', 'organizer_id', 'organizer', 'description', 'start_time',
                  'end_time', 'location', 'image', 'ticket_quantity', 'ticket_sold', 'ticket_price', 'latitude', 'longitude', 'avg_rating']
        read_only_fields = ['organizer_id', 'latitude', 'longitude', 'avg_rating']

    # Calling API to update latitude and longitude when change location from API request
    def update_geocoding(self, validated_data):
        location = validated_data.get('location')
        if location and not (validated_data.get('latitude') and validated_data.get('longitude')):
            api_key = "67e3b02f3fa84067694021akgad948d"
            url = f"https://geocode.maps.co/search?q={location}&api_key={api_key}"
            try:
                response = requests.get(url)
                response.raise_for_status()
                data = response.json()
                if data and isinstance(data, list) and len(data) > 0:
                    validated_data['latitude'] = float(data[0]['lat'])
                    validated_data['longitude'] = float(data[0]['lon'])
                else:
                    raise serializers.ValidationError(f"No geocoding results for location: {location}")
            except requests.RequestException as e:
                raise serializers.ValidationError(f"Error calling geocode.maps.co: {str(e)}")
            except (KeyError, ValueError) as e:
                raise serializers.ValidationError(f"Invalid response format: {str(e)}")

        return validated_data

    def create(self, validated_data):
        validated_data = self.update_geocoding(validated_data)
        return Event.objects.create(**validated_data)

    def update(self, instance, validated_data):
        if 'location' in validated_data and validated_data['location'] != instance.location:
            validated_data.pop('latitude', None)
            validated_data.pop('longitude', None)
            validated_data = self.update_geocoding(validated_data)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        return instance


class QRCodeCheckInSerializer(serializers.Serializer):
    qr_code_data = serializers.CharField(required=True)


class TicketSerializer(ModelSerializer):
    qr_code_data = serializers.SerializerMethodField()
    event = EventSerializer(source='invoice_id.event_id', read_only=True)

    class Meta:
        model = Ticket
        fields = ['invoice_id', 'status', 'qr_code', 'checked_in_at', 'qr_code_data', 'event']

    def get_qr_code_data(self, obj):
        signer = Signer()
        return signer.sign(obj.id)

    def to_representation(self, instance):
        d = super().to_representation(instance)
        d['qr_code'] = instance.qr_code.url if instance.qr_code else None
        return d


class DiscountSerializer(ModelSerializer):
    class Meta:
        model = Discount
        fields = '__all__'
        read_only_fields = ['used_count']


class InvoiceSerializer(ModelSerializer):
    event_id = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all())
    discount_id = serializers.PrimaryKeyRelatedField(queryset=Discount.objects.all(), required=False, allow_null=True)
    event = EventSerializer(source='event_id', read_only=True)

    class Meta:
        model = Invoice
        fields = ['id', 'invoice_code', 'user_id', 'event_id', 'event', 'discount_id', 'ticket_count', 'amount', 'discount_amount', 'final_amount',
                  'payment_status', 'transaction_id', 'created_at']
        read_only_fields = [
            'id', 'invoice_code', 'user_id', 'amount', 'discount_amount', 'final_amount', 'payment_status', 'transaction_id',
            'created_at'
        ]

    def validate(self, data):
        if data.get('discount_id'):
            discount = data['discount_id']
            now = timezone.now()
            user = self.context['request'].user
            if not (
                discount.active and
                discount.valid_from < now < discount.valid_until and
                discount.used_count < discount.max_usage and
                (discount.membership_tier == user.membership_tier or discount.membership_tier is None)
            ):
                raise serializers.ValidationError({"discount_id": "Invalid discount!"})

        if data['ticket_count'] <= 0:
            raise serializers.ValidationError({"ticket_count": "Invoice must have one or more tickets!"})

        return data


class ReviewResponseSerializer(ModelSerializer):
    organizer = UserSerializer(source='organizer_id', read_only=True)

    class Meta:
        model = ReviewResponse
        fields = ['id', 'review_id', 'organizer', 'organizer_id', 'active']
        read_only_fields = ['id', 'review_id', 'organizer', 'organizer_id', 'active']

    def validate(self, data):
        request = self.context.get('request')
        review = self.context.get('review')

        review = Review.objects.get(pk=review, active=True)
        if review.event_id.organizer_id != request.user:
            raise ValidationError("You do not have permission to response to this review!")

        return data


class ReviewSerializer(ModelSerializer):
    participant = UserSerializer(source='participant_id', read_only=True)

    class Meta:
        model = Review
        fields = ['id', 'participant', 'event_id', 'rating', 'comment', 'created_date', 'active']
        read_only_fields = ['id', 'participant', 'event_id', 'created_date', 'active']

    def validate(self, data):
        request = self.context.get('request')
        event = self.context.get('event')

        if not Invoice.objects.filter(
            user_id=request.user,
            event_id=event,
            payment_status='success'
        ).exists():
            raise serializers.ValidationError({"event_id": "You must buy a ticket to reviews!"})

        if self.instance is None:
            if Review.objects.filter(participant_id=request.user, event_id=event).exists():
                raise serializers.ValidationError({"event_id": "You have already reviewed this event!"})
        return data


class ReviewStatsSerializer(serializers.Serializer):
    event_id = serializers.IntegerField()
    review_count = serializers.IntegerField()
    average_rating = serializers.FloatField()


class OrganizerDashboardSerializer(serializers.Serializer):
    total_tickets = serializers.IntegerField()
    total_revenue = serializers.DecimalField(max_digits=10, decimal_places=2)
    total_views = serializers.IntegerField()
    events = serializers.ListField(child=serializers.DictField())


class MonthlyReportSerializer(serializers.Serializer):
    ticket_pie_chart = serializers.ListField(child=serializers.DictField())
    revenue_pie_chart = serializers.ListField(child=serializers.DictField())


class FavoriteEventSerializer(ModelSerializer):
    event = EventSerializer(source='event_id', read_only=True)

    class Meta:
        model = FavoriteEvent
        fields = ['id', 'event', 'event_id', 'created_date']
        read_only_fields = ['id', 'created_date']


class UserPreferenceSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(active=True),
        source='category',
        write_only=True
    )   

    class Meta:
        model = UserPreference
        fields = ['id', 'user', 'category', 'category_id', 'created_date']
        read_only_fields = ['id', 'user', 'created_date']


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['participant_id', 'title', 'message', 'is_read', 'sent_at']


