import json

import cloudinary.uploader
from django.db import models
from django.contrib.auth.models import AbstractUser
from cloudinary.models import CloudinaryField
from ckeditor.fields import RichTextField
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal
import uuid
# Generate qr code
import qrcode
from io import BytesIO
from django.core.files import File
from PIL import Image, ImageDraw
from django.core.signing import Signer
from django.core.validators import MinValueValidator, MaxValueValidator
# Update membership
from django.db import transaction
# Recommend


# Enum for membership
class Membership(models.TextChoices):
    NORMAL = 'NORMAL', 'Normal'
    SILVER = 'SILVER', 'Silver'
    GOLD = 'GOLD', 'Gold'
    DIAMOND = 'DIAMOND', 'Diamond'


# User with 3 main roles
class User(AbstractUser):
    ROLE_CHOICES = (
        ('admin', 'Administrator'),
        ('organizer', 'Organizer'),
        ('participant', 'Participant')
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='participant')
    membership_tier = models.CharField(max_length=20, choices=Membership.choices, null=True, blank=True)
    avatar = CloudinaryField(null=True, blank=True)

    def __str__(self):
        return self.get_full_name()

    # Only participant have membership tier
    def save(self, *args, **kwargs):
        if self.role != 'participant':
            self.membership_tier = None
        elif not self.membership_tier:
            self.membership_tier = Membership.NORMAL
        super().save(*args, **kwargs)


# Base class to inherit
class BaseModel(models.Model):
    active = models.BooleanField(default=True)
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# Category
class Category(BaseModel):
    name = models.CharField(max_length=50, unique=True)
    description = RichTextField(null=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.name


# Event
class Event(BaseModel):
    organizer_id = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'role': 'organizer'})
    category_id = models.ForeignKey(Category, on_delete=models.CASCADE)
    title = models.CharField(max_length=100, unique=True)
    description = RichTextField(null=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    location = models.CharField(max_length=255, null=True, blank=True)  # lat, lng
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    image = CloudinaryField(null=True)
    ticket_quantity = models.PositiveIntegerField()
    ticket_price = models.DecimalField(max_digits=10, decimal_places=2)
    views = models.PositiveIntegerField(default=0)
    feature_vector = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.title

    # Process validated data
    def clean(self):
        if self.end_time < self.start_time:
            raise ValidationError({
                'end_time': 'End time must be greater than or equal to start time.'
            })
        super().clean()

    def set_feature_vector(self, vector):
        self.feature_vector = json.dumps(vector.tolist())
        self.save()

    def get_feature_vector(self):
        if self.feature_vector:
            return json.loads(self.feature_vector)
        return []


# Discount for each different membership tier
class Discount(BaseModel):
    discount_code = models.CharField(max_length=30, unique=True, null=False)
    discount_percent = models.DecimalField(max_digits=3, decimal_places=0, null=False,
                                           validators=[MinValueValidator(Decimal(1)), MaxValueValidator(Decimal(100))])
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    max_usage = models.IntegerField(null=False)
    used_count = models.IntegerField(default=0)
    membership_tier = models.CharField(max_length=20, choices=Membership.choices, null=False)

    def __str__(self):
        return self.discount_code

    def clean(self):
        if self.valid_until and self.valid_from:
            if self.valid_until < self.valid_from:
                raise ValidationError({
                    'valid_until': 'Valid until must be greater than or equal to valid from.'
                })
            super().clean()


# Invoice
class Invoice(models.Model):
    invoice_code = models.CharField(max_length=30, unique=True, null=False)
    user_id = models.ForeignKey(User, max_length=20, null=False, on_delete=models.PROTECT, limit_choices_to={'role': 'participant'})
    event_id = models.ForeignKey(Event, related_name='invoices', on_delete=models.CASCADE)
    discount_id = models.ForeignKey(Discount, on_delete=models.PROTECT, null=True, blank=True)
    amount = models.FloatField(null=True, blank=True)
    discount_amount = models.FloatField(null=True, default=0)
    final_amount = models.FloatField(null=True, blank=True)
    ticket_count = models.IntegerField(null=False)
    PAYMENT_STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('success', 'Success'),
        ('fail', 'Fail')
    )
    payment_status = models.CharField(max_length=10, choices=PAYMENT_STATUS_CHOICES, default='pending')
    transaction_id = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.invoice_code

    def calculate_amount(self):
        # Calculate amount if there's an event
        if not self.event_id:
            raise ValueError("Event is required to calculate amounts")
        ticket_price = float(self.event_id.ticket_price)
        self.amount = ticket_price * self.ticket_count

        # Calculate discount amount if there's a discount
        if self.discount_id:
            current_time = timezone.now()
            if (self.discount_id.valid_from <= current_time <= self.discount_id.valid_until and
                self.discount_id.used_count < self.discount_id.max_usage):
                discount_percent = float(self.discount_id.discount_percent)
                self.discount_amount = self.amount * (discount_percent / 100)
                # Increase used count of Discount and save
                self.discount_id.used_count += 1
                self.discount_id.save()

        # Calculate final amount after discount
        self.final_amount = self.amount - self.discount_amount

    def save(self, *args, **kwargs):
        with transaction.atomic():
            # Update membership whenever a invoice become success from pending
            from .services import update_user_membership
            was_pending = self.pk and Invoice.objects.get(pk=self.pk).payment_status == 'pending'
            if not self.pk:
                date_str = timezone.now().strftime('%Y%m%d')
                unique_id = str(uuid.uuid4())[:8]
                self.invoice_code = f"EVTUP{date_str}{unique_id}"
            self.calculate_amount()
            super().save(*args, **kwargs)
            if was_pending and self.payment_status == 'success':
                update_user_membership(self.user_id)

                # Send notification to user after ticket was paying successfully
                from .utils import send_notification
                send_notification(
                    user=self.user_id,
                    title=f"Payment Completed for {self.event_id.title}",
                    message=f"Hi {self.user_id.first_name}, \nYour payment of {self.final_amount} for {self.event_id.title} is completed. Invoice code: {self.invoice_code}"
                )


# Each ticket have a qr code
class Ticket(BaseModel):
    invoice_id = models.ForeignKey(Invoice, related_name='tickets', on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=(('booked', 'Booked'), ('checked-in', 'Checked-in')),
                              default='booked')
    qr_code = CloudinaryField('qr_codes', blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"Ticket {self.id}"

    def generate_qr_code_data(self):
        signer = Signer()
        return signer.sign(str(self.id))

    def save(self, *args, **kwargs):
        is_new = not self.pk
        # Only create qr code 1 time at first save
        if is_new and not self.qr_code:
            super().save(*args, **kwargs)
            qr = qrcode.QRCode (
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=0
            )
            qr.add_data(str(self.generate_qr_code_data()))
            qr.make(fit=True)
            qrcode_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            qr_size = 200
            qrcode_img = qrcode_img.resize((qr_size, qr_size), Image.Resampling.LANCZOS)

            canvas_size = 250
            canvas = Image.new('RGB', (canvas_size, canvas_size), 'white')
            paste_position = ((canvas_size - qr_size) // 2, (canvas_size - qr_size) // 2)
            canvas.paste(qrcode_img, paste_position)

            buffer = BytesIO()
            canvas.save(buffer, 'PNG')
            buffer.seek(0)
            upload_result = cloudinary.uploader.upload(
                buffer,
                folder='qr_codes',
                public_id=f'qr-code-{self.id}',
                resource_type='image',
                overwrite=True
            )
            self.qr_code = upload_result['public_id']
            canvas.close()
            super().save(update_fields=['qr_code'])

            # Send notification to user after ticket created
            from .utils import send_notification
            event = self.invoice_id.event_id
            send_notification(
                user=self.invoice_id.user_id,
                title=f"Ticket purchased for {event.title}",
                message=f"Hi {self.invoice_id.user_id.first_name}, \nYou've successfully purchased a ticket for {event.title} on {event.start_time}."
            )
        else:
            super().save(*args, **kwargs)


# Review from 1 to 5, with comment followed
class Review(BaseModel):
    # CASCADE because review have no meaning if there is no reviewer or event
    participant_id = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'role': 'participant'}, null=False)
    event_id = models.ForeignKey(Event, on_delete=models.CASCADE, null=False)
    rating = models.DecimalField(max_digits=1, decimal_places=0, null=False,
                                 validators=[MinValueValidator(Decimal(1)), MaxValueValidator(Decimal(5))],
                                 help_text="Rating from 1 to 5 stars")
    comment = RichTextField()

    class Meta:
        # Each participant reviews 1 time for each event
        unique_together = ('participant_id', 'event_id')
        ordering = ['id']

    def clean(self):
        if not Invoice.objects.filter(event_id=self.event_id, user_id=self.participant_id, payment_status='success').exists():
            raise ValidationError("User must buy ticket to review this event")
        super().clean()

    def __str__(self):
        return f"Participant: {self.participant_id.first_name} - Event: {self.event_id.title}"


# Notification
class Notification(BaseModel):
    participant_id = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'role': 'participant'}, null=False)
    title = models.CharField(max_length=50, null=False)
    message = RichTextField()
    is_read = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True)

    def __str__(self):
        return f"Participant: {self.participant_id.username} - Title: {self.title}"

    class Meta:
        ordering = ['sent_at']


# Favorite
class FavoriteEvent(BaseModel):
    participant_id = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'role': 'participant'}, null=False)
    event_id = models.ForeignKey(Event, on_delete=models.CASCADE, null=False)

    class Meta:
        unique_together = ['participant_id', 'event_id']
        indexes = [
            models.Index(fields=['participant_id', 'event_id'])
        ]

    def clean(self):
        if self.participant_id.role != 'participant':
            raise ValidationError('Only participant can favorite events!')
        if not self.event_id.active:
            raise ValidationError('Cannot favorite an inactive event!')


class UserPreference(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='preferences')
    category = models.ForeignKey(Category, on_delete=models.CASCADE)

    class Meta:
        unique_together = ['user', 'category']


class ReviewResponse(BaseModel):
    review_id = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='responses')
    organizer_id = models.ForeignKey(User, on_delete=models.CASCADE)
    response = RichTextField()

    def clean(self):
        if self.organizer_id.role != 'organizer':
            raise ValidationError("Only organizer can respond to reviews")
        if self.review_id.organizer_id != self.organizer_id:
            raise ValidationError("Organizer can only respond to reviews of their own events!")

    def __str__(self):
        return f"Response to Review {self.review_id.id} by {self.organizer_id.name}"











