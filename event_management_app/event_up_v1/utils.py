from django.core.mail import send_mail
from django.utils import timezone
from .models import Notification


def send_notification(user, title, message, send_email=True):
    notification = Notification.objects.create(
        participant_id=user,
        title=title,
        message=message,
        sent_at=timezone.now()
    )

    if send_email and user.email:
        try:
            send_mail(
                subject=title,
                message=message,
                from_email=None,
                recipient_list=[user.email],
                fail_silently=False
            )
            notification.sent_at = timezone.now()
            notification.save()
        except Exception as e:
            print(f"Email failed: {e}")