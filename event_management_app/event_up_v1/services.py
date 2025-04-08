from django.core.signing import Signer, BadSignature
from django.utils import timezone
from django.db import transaction
from .models import Ticket, Invoice, User, Membership
from django.db.models import Sum


def check_in_ticket(qr_code_data):
    signer = Signer()
    try:
        ticket_id = signer.unsign(qr_code_data)
        ticket = Ticket.objects.get(id=ticket_id)
        if ticket.status == 'booked':
            ticket.status = 'checked-in'
            ticket.checked_in_at = timezone.now()
            ticket.save()
            return {"status": "success", "message": "Checked in successfully"}
        return {"status": "error", "message": "Ticket already checked in"}
    except BadSignature:
        return {"status": "error", "message": "Invalid QR Code"}


def create_tickets_after_payment(invoice):
    with transaction.atomic():
        existing_tickets = invoice.ticket_set.count()
        if existing_tickets < invoice.ticket_count:
            for _ in range(invoice.ticket_count - existing_tickets):
                Ticket.objects.create(
                    invoice_id=invoice,
                    status='booked'
                )
            update_user_membership(invoice.user_id)

def update_user_membership(user):
    if user.role == 'participant':
        user.membership_tier = None
    else:
        invoices = Invoice.objects.filter(user_id=user, payment_status='success')
        total_tickets = invoices.aggregate(total=Sum('ticket_count'))['total'] or 0
        total_spent = invoices.aggregate(total=Sum('final_amount'))['total'] or 0

        if total_tickets >= 50 or total_spent >= 1000000:
            user.membership_tier = Membership.DIAMOND
        elif total_tickets >= 20 or total_spent >= 3000000:
            user.membership_tier = Membership.GOLD
        elif total_tickets >= 10 or total_spent >= 1000000:
            user.membership_tier = Membership.SILVER
        else:
            user.membership_tier = Membership.NORMAL
    user.save()

