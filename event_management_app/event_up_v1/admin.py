from django.contrib import admin, messages
from .models import User, Event, EventType, Ticket, Invoice
from django.contrib.auth.admin import UserAdmin
from ckeditor_uploader.widgets import CKEditorUploadingWidget
from django.utils.safestring import mark_safe
from django import forms
import requests
from . import services
from django.db import transaction
from django.utils import timezone


# Using CKEditor uploading widget instead of base widget
class EventForm(forms.ModelForm):
    description = forms.CharField(widget=CKEditorUploadingWidget)

    # Using model Event, with all fields
    class Meta:
        model = Event
        fields = '__all__'


class CustomUserAdmin(UserAdmin):
    list_display = ['username', 'first_name', 'last_name', 'role', 'membership_tier', 'display_avatar']

    fieldsets = UserAdmin.fieldsets + (
        ('Additional Info', {'fields': ('role', 'display_avatar')}),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Additional Info', {'fields': ('first_name', 'last_name', 'role', 'avatar')}),
    )

    readonly_fields = ['display_avatar']

    @staticmethod
    # Show image to admin view
    def display_avatar(user):
        if user.avatar:
            return mark_safe(f"<img src='{user.avatar.url}' width='70' height='70'/>")
        return "No image is available"


class EventAdmin(admin.ModelAdmin):
    list_display = ['id', 'organizer_id', 'event_type_id', 'title', 'description', 'start_time',
                    'end_time', 'location', 'ticket_quantity', 'ticket_price']
    fields = ['organizer_id', 'event_type_id', 'title', 'description', 'start_time', 'end_time',
              'location', 'latitude', 'longitude', 'ticket_quantity', 'ticket_price', 'image',
              'image_view']
    search_fields = ['title']
    list_filter = ['id', 'start_time', 'end_time']
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


class TicketInLine(admin.TabularInline):
    model = Ticket
    extra = 0
    max_num = 0
    can_delete = False
    can_add = False
    readonly_fields = ['qr_code', 'status', 'checked_in_at', 'created_date']
    show_change_link = True


class TicketAdmin(admin.ModelAdmin):
    list_display = ['id', 'invoice_id', 'created_date', 'qr_code_view', 'status']
    readonly_fields = ['qr_code_view']
    search_fields = ['created_date']
    list_filter = ['id', 'created_date']
    fields = ['invoice_id', 'status', 'qr_code_view']
    actions = ['check_in_tickets']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @staticmethod
    # Show image to admin view
    def qr_code_view(ticket):
        if ticket:
            return mark_safe(f"<img src='/media/{ticket.qr_code.name}' width='80' />")

    def check_in_tickets(self, request, queryset):
        for ticket in queryset.filter(status='booked'):
            qr_code_data = ticket.generate_qr_code_data()
            result = services.check_in_ticket(qr_code_data)
            self.message_user(request, result['message'])

    check_in_tickets.short_description = "Check-in selected ticket"


class EventTypeAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'description']


class DiscountAdmin(admin.ModelAdmin):
    list_display = ['discount_code', 'discount_percent', 'valid_from', 'valid_until',
                    'max_usage', 'used_count', 'membership_tier']
    fields = ['discount_code', 'discount_percent', 'valid_from', 'valid_until',
              'max_usage', 'membership_tier']
    search_fields = ['discount_percent', 'valid_from']


class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['invoice_code', 'event_id', 'user_id', 'ticket_count', 'amount', 'discount_amount', 'final_amount',
                    'payment_status']
    fields = ['user_id', 'event_id', 'discount_id', 'ticket_count', 'amount', 'discount_amount', 'final_amount', 'payment_status']
    readonly_fields = ['amount', 'discount_amount', 'final_amount', 'payment_status']
    search_fields = ['event_id', 'user_id']
    list_filter = ['payment_status']
    inlines = [TicketInLine]
    actions = ['mark_as_paid']

    def mark_as_paid(self, request, queryset):
        updated = 0
        for invoice in queryset.filter(payment_status='pending'):
            with transaction.atomic():
                invoice.payment_status = 'success'
                invoice.transaction_id = f"MANUAL-{timezone.now().strftime('%Y%m%d%H%M%S')}"
                invoice.save()
                if not invoice.ticket_set.exists():
                    services.create_tickets_after_payment(invoice)
                updated += 1
        self.message_user(request, f"{updated} invoices processed")


admin.site.site_header = 'EventUp Admin Site'
admin.site.site_title = 'EventUp Site'
admin.site.index_title = 'EventUp Site'


# Register your models here.
admin.site.register(User, CustomUserAdmin)
admin.site.register(Event, EventAdmin)
admin.site.register(EventType, EventTypeAdmin)
admin.site.register(Ticket, TicketAdmin)
admin.site.register(Invoice, InvoiceAdmin)
