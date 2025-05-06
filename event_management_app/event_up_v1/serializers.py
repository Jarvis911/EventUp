from rest_framework.serializers import ModelSerializer
from rest_framework import serializers
from django.utils import timezone
from .models import Event, Category, Ticket, User, Discount, Invoice, Review, FavoriteEvent
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


# Serializer for Event
class EventSerializer(BaseSerializer):
    category = CategorySerializer(source='category_id', read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all()
    )

    class Meta:
        model = Event
        fields = ['id', 'title', 'category', 'category_id', 'organizer_id', 'description', 'start_time',
                  'end_time', 'location', 'image', 'ticket_quantity', 'ticket_price', 'latitude', 'longitude']
        extra_kwargs = {
            'organizer_id': {'read_only': True},
            'latitude': {'read_only': True},
            'longitude': {'read_only': True}
        }

    # Calling API to update latitude and longitude when change location from API request
    def _update_geocoding(self, validated_data):
        location = validated_data.get('location')
        if location and not (validated_data.get('latitude') and validated_data.get('longitude')):
            api_key = "67e3b02f3fa84067694021akgad948d"
            url = f"https://geocode.maps.co/search?q={location}&api_key={api_key}"
            try:
                response = requests.get(url)
                response.raise_for_status()  # Raise lỗi nếu HTTP status không phải 200
                data = response.json()
                if data and isinstance(data, list) and len(data) > 0:  # Kiểm tra response hợp lệ
                    validated_data['latitude'] = float(data[0]['lat'])
                    validated_data['longitude'] = float(data[0]['lon'])
                else:
                    raise serializers.ValidationError(f"No geocoding results for location: {location}")
            except requests.RequestException as e:
                raise serializers.ValidationError(f"Error calling geocode.maps.co: {str(e)}")
            except (KeyError, ValueError) as e:
                raise serializers.ValidationError(f"Invalid response format: {str(e)}")

        return validated_data

    def to_internal_value(self, data):
        validated_data = super().to_internal_value(data)

        unknown_fields = set(data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError({
                field: 'This field is not allowed.' for field in unknown_fields
            })

        return validated_data

    def create(self, validated_data):
        validated_data = self._update_geocoding(validated_data)
        return Event.objects.create(**validated_data)

    def update(self, instance, validated_data):
        if 'location' in validated_data and validated_data['location'] != instance.location:
            validated_data.pop('latitude', None)
            validated_data.pop('longitude', None)
            validated_data = self._update_geocoding(validated_data)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        return instance


# Serializer for user
class UserSerializer(ModelSerializer):
    role = serializers.ChoiceField(
        choices=[('participant', 'Participant'), ('organizer', 'Organizer')],
        default='participant'
    )
    avatar = serializers.ImageField(required=False, allow_null=True)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['avatar'] = instance.avatar.url if instance.avatar else None
        return data

    # Check if user try to send invalid fields
    def to_internal_value(self, data):
        validated_data = super().to_internal_value(data)

        unknown_fields = set(data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError({
                field: 'This field is not allowed.' for field in unknown_fields
            })

        return validated_data

    class Meta:
        model = User
        fields = ['username', 'password', 'first_name', 'last_name', 'email', 'role', 'membership_tier', 'avatar']
        extra_kwargs = {
            'password': {'write_only': True},
            'email': {'required': True},
            'membership_tier': {'read_only': True}
        }

    # Encrypt password before save to database
    def create(self, validated_data):
        avatar = validated_data.pop('avatar', None)

        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', ''),
            role=validated_data['role']
        )

        if avatar:
            user.avatar = avatar
        user.save()

        return user

    def update(self, instance, validated_data):
        if 'password' in validated_data:
            instance.set_password(validated_data.pop('password'))
        if 'avatar' in validated_data:
            instance.avatar = validated_data.pop('avatar')
        return super().update(instance, validated_data)


class TicketSerializer(ModelSerializer):
    class Meta:
        model = Ticket
        fields = ['invoice_id', 'status', 'qr_code', 'checked_in_at']


class DiscountSerializer(ModelSerializer):
    # Check if user try to send invalid fields
    def to_internal_value(self, data):
        validated_data = super().to_internal_value(data)

        unknown_fields = set(data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError({
                field: 'This field is not allowed.' for field in unknown_fields
            })

        return validated_data

    class Meta:
        model = Discount
        fields = '__all__'
        extra_kwargs = {
            'used_count': {'read_only': True},
        }


class InvoiceSerializer(ModelSerializer):
    event_id = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all())
    discount_id = serializers.PrimaryKeyRelatedField(queryset=Discount.objects.all(), required=False, allow_null=True)

    class Meta:
        model = Invoice
        fields = ['invoice_code', 'user_id', 'event_id', 'discount_id', 'ticket_count','amount', 'discount_amount', 'final_amount',
                  'payment_status', 'transaction_id', 'created_at']

        read_only_fields = [
            'invoice_code', 'user_id', 'amount', 'discount_amount', 'final_amount', 'payment_status', 'transaction_id',
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


class ReviewSerializer(ModelSerializer):
    participant = UserSerializer(source='participant_id', read_only=True)
    event_id = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all())

    class Meta:
        model = Review
        fields = ['id', 'participant', 'event_id', 'rating', 'comment', 'created_date']
        read_only_fields = ['id', 'participant', 'event_id', 'created_date']

    def to_internal_value(self, data):
        validated_data = super().to_internal_value(data)

        unknown_fields = set(data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError({
                field: 'This field is not allowed.' for field in unknown_fields
            })

        return validated_data

    def validate(self, data):
        request = self.context.get('request')
        event = self.context.get('event')

        if not event:
            raise serializers.ValidationError({"event_id": "Event is required to write reviews!"})

        if not request.user.is_authenticated:
            raise serializers.ValidationError({"participant_id": "Authentication is required!"})

        if request.user.role != 'participant':
            raise serializers.ValidationError({"participant_id": "Only participants can write reviews!"})

        if not Invoice.objects.filter(
            user_id=request.user,
            event_id=event,
            payment_status='success'
        ).exists():
            raise serializers.ValidationError({"event_id": "You must buy a ticket to reviews!"})

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
    class Meta:
        model = FavoriteEvent
        fields = ['id', 'event_id', 'created_date']
        read_only_fields = ['id', 'created_date']

    def validate(self, data):
        user = self.context['request'].user
        if getattr(user, 'role', None) != 'participant':
            raise serializers.ValidationError('Only participant can favorite events!')
        return data



