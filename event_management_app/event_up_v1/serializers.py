from rest_framework.serializers import ModelSerializer
from rest_framework import serializers
from .models import Event, EventType, Ticket, User
# To call API
import requests


# Override to_presentation to show Cloudinary url
class BaseSerializer(ModelSerializer):
    def to_representation(self, instance):
        d = super().to_representation(instance)
        d['image'] = instance.image.url if instance.image else None # Check if image is null
        return d


# Serializer for EventType
class EventTypeSerializer(ModelSerializer):
    class Meta:
        model = EventType
        fields = ['id', 'name']


# Serializer for Event
class EventSerializer(BaseSerializer):
    class Meta:
        model = Event
        fields = ['id', 'title', 'event_type_id', 'organizer_id', 'description', 'date_time', 'location',
                  'image', 'ticket_quantity', 'ticket_price', 'latitude', 'longitude']

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
    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['avatar'] = instance.avatar.url if instance.avatar else None
        return data

    class Meta:
        model = User
        fields = ['username', 'password', 'first_name', 'last_name', 'avatar']
        extra_kwargs = {
            'password': {
                'write_only': True
            }
        }

    # Encrypt password before save to database
    def create(self, validated_data):
        data = validated_data.copy()
        u = User(**data)
        u.set_password(u.password)
        u.save()

        return u

