from django.core.management.base import BaseCommand
from django.utils import timezone
from eventapp.event_management_app.event_up_v1.models import Ticket
from eventapp.event_management_app.event_up_v1.utils import send_notification


class Command(BaseCommand):
    help = 'Send reminders for upcoming events'

    # Send reminder when event is coming in 1 day and 1 hour
    def handle(self, *args, **kwargs):
        now = timezone.now()

        one_day_later = now + timezone.timedelta(days=1)
        tickets_one_day = Ticket.objects.filter(
            invoice_id__event_id__start_time__range=(one_day_later - timezone.timedelta(hours=1),
                                                     one_day_later + timezone.timedelta(hours=1)),
            status='booked'
        )

        for ticket in tickets_one_day:
            event = ticket.invoice_id.event_id
            send_notification(
                user=ticket.invoice_id.user_id,
                title=f"Reminder: {event.title} is Tomorrow!",
                message=f"Hi {ticket.invoice_id.user_id.username}, \n{event.title} start at {event.start_time}. Get ready!"
            )

        one_hour_later = now + timezone.timedelta(hours=1)
        tickets_one_hour = Ticket.objects.filter(
            invoice_id__event_id__start_time__range=(one_hour_later - timezone.timedelta(minutes=5),
                                                     one_hour_later + timezone.timedelta(minutes=5)),
            status='booked'
        )
        for ticket in tickets_one_hour:
            event = ticket.invoice_id.event_id
            send_notification(
                user=ticket.invoice_id.user_id,
                title=f"Reminder: {event.title} in 1 Hour",
                message=f"Hi {ticket.invoice_id.user_id.username}, \n{event.title} start at {event.start_time}. See you soon!"
            )

        self.stdout.write(self.style.SUCCESS(f"Sent reminders: {tickets_one_day.count()} for 1 day, {tickets_one_hour.count()} for 1 hour"))