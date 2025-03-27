from django.db import models
from django.contrib.auth.models import AbstractUser
from cloudinary.models import CloudinaryField
from ckeditor.fields import RichTextField
# Generate qr code
import qrcode
from io import BytesIO
from django.core.files import File
from PIL import Image, ImageDraw


# User with 3 main roles
class User(AbstractUser):
    ROLE_CHOICES = (
        ('admin', 'Administrator'),
        ('organizer', 'Organizer'),
        ('participant', 'Participant')
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='participant')
    avatar = CloudinaryField(null=True)

    def __str__(self):
        return self.username


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
    date_time = models.DateTimeField()
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


# Ticket
class Ticket(BaseModel):
    event_id = models.ForeignKey(Event, on_delete=models.CASCADE)
    user_id = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'role': 'participant'})
    status = models.CharField(max_length=20, choices=(('booked', 'Booked'), ('checked-in', 'Checked-in')),
                              default='booked')
    qr_code = models.ImageField(upload_to='qr_codes', blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"Ticket {self.id} - {self.event_id.title}"

    def save(self, *args, **kwargs):
        # Save first to make id has value
        if not self.pk:
            super().save(*args, **kwargs)

        qrcode_img = qrcode.make(str(self.id))
        qrcode_img = qrcode_img.convert("RGB")
        canvas = Image.new('RGB', (290, 290), 'white')
        draw = ImageDraw.Draw(canvas)
        canvas.paste(qrcode_img)
        fname = f'qr_code-{self.id}.png'
        buffer = BytesIO()
        canvas.save(buffer, 'PNG')
        self.qr_code.save(fname, File(buffer), save=False)
        canvas.close()
        super().save(*args, **kwargs)












