from celery import shared_task
from datetime import timedelta, datetime
from django.utils import timezone

import requests

@shared_task(name="send_event_reminder_notifications")
def send_event_reminder_notifications():
    from .models import Event, Invoice
    now = timezone.now()
    target_time = now + timedelta(days=1)

    events = Event.objects.filter(
        active=True,
        start_time__date=target_time.date()
    )

    for event in events:
        invoices = Invoice.objects.filter(event_id=event, payment_status='success').select_related('user_id')
        push_tokens = [inv.user_id.push_token for inv in invoices if inv.user_id.push_token]

        for token in push_tokens:
            message = {
                "to": token,
                "sound": "default",
                "title": "Event Reminder",
                "body": f"Don't forget: Event '{event.title}' will take place tomorrow!",
            }
            try:
                requests.post("https://exp.host/--/api/v2/push/send", json=message)
            except Exception as ex:
                print(f"Push failed for token {token}: {ex}")
