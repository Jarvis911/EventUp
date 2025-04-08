from django.db import models
from django.contrib.auth.models import AbstractUser
from cloudinary.models import CloudinaryField
from ckeditor.fields import RichTextField
from django.utils import timezone
# Generate qr code
import qrcode
from io import BytesIO
from django.core.files import File
from PIL import Image, ImageDraw
from django.core.signing import Signer
from django.core.validators import MinValueValidator, MaxValueValidator
# Update membership
from django.db import transaction


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


# Event type
class EventType(BaseModel):
    name = models.CharField(max_length=50, unique=True)
    description = RichTextField(null=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.name


# Event
class Event(BaseModel):
    organizer_id = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'role': 'organizer'})
    event_type_id = models.ForeignKey(EventType, on_delete=models.CASCADE)
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


    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.title


# Discount for each different membership tier
class Discount(BaseModel):
    discount_code = models.CharField(max_length=30, unique=True, null=False)
    discount_percent = models.DecimalField(max_digits=3, decimal_places=0, null=False,
                                           validators=[MinValueValidator(1), MaxValueValidator(100)])
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    max_usage = models.IntegerField(null=False)
    used_count = models.IntegerField(default=0)
    membership_tier = models.CharField(max_length=20, choices=Membership.choices, null=False)

    def __str__(self):
        return self.discount_code


# Invoice
class Invoice(models.Model):
    invoice_code = models.CharField(max_length=30, unique=True, null=False)
    user_id = models.ForeignKey(User, max_length=20, null=False, on_delete=models.PROTECT, limit_choices_to={'role': 'participant'})
    event_id = models.ForeignKey(Event, on_delete=models.CASCADE)
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
                super().save(*args, **kwargs)
                date_str = self.created_at.strftime('%Y%m%d')
                self.invoice_code = f"EVTUP-{date_str}-{str(self.id).zfill(6)}"
            self.calculate_amount()
            super().save(*args, **kwargs)
            if was_pending and self.payment_status == 'success':
                update_user_membership(self.user_id)


# Each ticket have a qr code
class Ticket(BaseModel):
    invoice_id = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=(('booked', 'Booked'), ('checked-in', 'Checked-in')),
                              default='booked')
    qr_code = models.ImageField(upload_to='qr_codes', blank=True)
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
        if is_new:
            super().save(*args, **kwargs)
            if not self.qr_code:
                qr_code_data = self.generate_qr_code_data()
                qrcode_img = qrcode.make(str(qr_code_data))
                qrcode_img = qrcode_img.convert("RGB")
                canvas = Image.new('RGB', (250, 250), 'white')
                draw = ImageDraw.Draw(canvas)
                canvas.paste(qrcode_img)
                fname = f'qr_code-{self.id}.png'
                buffer = BytesIO()
                canvas.save(buffer, 'PNG')
                self.qr_code.save(fname, File(buffer), save=False)
                canvas.close()
            super().save(update_fields=['qr_code'])
        else:
            super().save(*args, **kwargs)

# Review from 1 to 5, with comment followed
class Review(BaseModel):
    # CASCADE because review have no meaning if there is no reviewer or event
    participant_id = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'role': 'participant'}, null=False)
    event_id = models.ForeignKey(Event, on_delete=models.CASCADE, null=False)
    rating = models.DecimalField(max_digits=1, decimal_places=0, null=False,
                                 validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = RichTextField()

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"Participant: {self.participant_id.full_name} - Event: {self.event_id.title} - Rating: {self.rating}"


# Notification
class Notification(BaseModel):
    participant_id = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'participant'}, null=False)
    event_id = models.ForeignKey(Event, on_delete=models.CASCADE, null=False)
    title = models.CharField(max_length=50, null=False)
    message = RichTextField()
    is_read = models.BooleanField(default=False)

    def __str__(self):
        return f"Participant: {self.participant_id.name} - Event: {self.event_id.title} - Title: {self.title}"


# Chat between user
class ChatMessage(BaseModel):
    sender_id = models.ForeignKey(User, on_delete=models.CASCADE, null=False, related_name='sent_messages')
    receiver_id = models.ForeignKey(User, on_delete=models.CASCADE, null=False, related_name='received_messages')
    message = RichTextField()
    is_read = models.BooleanField(default=False)

    def __str__(self):
        return f"Sender: {self.sender_id.name} - Receiver: {self.receiver_id.name}"











