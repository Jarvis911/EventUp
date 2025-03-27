from django.contrib import admin, messages
from .models import User, Event, EventType, Ticket
from django.contrib.auth.admin import UserAdmin
from ckeditor_uploader.widgets import CKEditorUploadingWidget
from django.utils.safestring import mark_safe
from django import forms
import requests


# Using CKEditor uploading widget instead of base widget
class EventForm(forms.ModelForm):
    description = forms.CharField(widget=CKEditorUploadingWidget)

    # Using model Event, with all fields
    class Meta:
        model = Event
        fields = '__all__'


class CustomUserAdmin(UserAdmin):
    list_display = ['username', 'first_name', 'last_name', 'role', 'display_avatar']

    fieldsets = UserAdmin.fieldsets + (
        ('Additional Info', {'fields': ('role', 'display_avatar')}),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Additional Info', {'fields': ('role', 'avatar')}),
    )

    readonly_fields = ['display_avatar']

    @staticmethod
    # Show image to admin view
    def display_avatar(user):
        if user.avatar:
            return mark_safe(f"<img src='{user.avatar.url}' width='70' height='70'/>")
        return "No image is available"


class EventAdmin(admin.ModelAdmin):
    list_display = ['id', 'organizer_id', 'event_type_id', 'title', 'description', 'date_time', 'location',
                    'ticket_quantity', 'ticket_price']
    fields = ['organizer_id', 'event_type_id', 'title', 'description', 'date_time', 'location', 'latitude',
              'longitude', 'ticket_quantity', 'ticket_price', 'image', 'image_view']
    search_fields = ['title']
    list_filter = ['id', 'date_time']
    readonly_fields = ['image_view', 'latitude', 'longitude']
    form = EventForm

    @staticmethod
    # Show image to admin view
    def image_view(event):
        if event.image:
            return mark_safe(f"<img src='{event.image.url}' width='200' />")
        return "No image is available"

    # Update latitude and longitude when change location from admin site
    def save_model(self, request, obj, form, change):
        if 'location' in form.changed_data:  # Call API when location is change
            api_key = "67e3b02f3fa84067694021akgad948d"
            url = f"https://geocode.maps.co/search?q={obj.location}&api_key={api_key}"

            try:
                response = requests.get(url, timeout=5)
                response.raise_for_status()
                data = response.json()

                if data and isinstance(data, list) and len(data) > 0:
                    obj.latitude = float(data[0]['lat'])
                    obj.longitude = float(data[0]['lon'])
                else:
                    messages.error(request, f"Can find geocode: {obj.location}")

            except requests.RequestException as e:
                messages.error(request, f"Error calling API Geocoding: {e}")

        super().save_model(request, obj, form, change)

    # Customize admin page css
    class Media:
        css = {'all': ('css/style.css',)}


class TicketAdmin(admin.ModelAdmin):
    list_display = ['id', 'event_id', 'user_id', 'created_date', 'qr_code_view', 'status']
    readonly_fields = ['qr_code_view']
    search_fields = ['event_id', 'user_id']
    list_filter = ['id', 'created_date']
    fields = ['event_id', 'user_id', 'status', 'qr_code_view']

    @staticmethod
    # Show image to admin view
    def qr_code_view(ticket):
        if ticket:
            return mark_safe(f"<img src='/media/{ticket.qr_code.name}' width='80' />")

    def save_model(self, request, obj, form, change):
        if 'location' in form.changed_data:  # Chỉ gọi API nếu location thay đổi
            api_key = "67e3b02f3fa84067694021akgad948d"
            url = f"https://geocode.maps.co/search?q={obj.location}&api_key={api_key}"

            try:
                response = requests.get(url, timeout=5)  # Giới hạn timeout 5s
                response.raise_for_status()
                data = response.json()

                if data and isinstance(data, list) and len(data) > 0:
                    obj.latitude = float(data[0]['lat'])
                    obj.longitude = float(data[0]['lon'])
                else:
                    messages.error(request, f"Không tìm thấy tọa độ cho địa chỉ: {obj.location}")

            except requests.RequestException as e:
                messages.error(request, f"Lỗi khi gọi API Geocoding: {e}")

        super().save_model(request, obj, form, change)

class EventTypeAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'description']


admin.site.site_header = 'EventUp Admin Site'
admin.site.site_title = 'EventUp Site'
admin.site.index_title = 'EventUp Site'


# Register your models here.
admin.site.register(User, CustomUserAdmin)
admin.site.register(Event, EventAdmin)
admin.site.register(EventType, EventTypeAdmin)
admin.site.register(Ticket, TicketAdmin)
