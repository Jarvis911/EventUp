from django.core.mail import send_mail
from django.utils import timezone
from datetime import timedelta, datetime
from .models import Notification, User, Event, Invoice, Review
from django.db.models import Sum, Avg
# Momo
import hmac
import hashlib
import json
import requests
from django.conf import settings
import logging
# Google login
from firebase_admin import auth
import secrets
from oauth2_provider.models import AccessToken, Application
# Initialize logger
logger = logging.getLogger(__name__)


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


def create_momo_payment(invoice, request_id):
    endpoint = settings.MOMO_ENDPOINT
    partner_code = settings.MOMO_PARTNER_CODE
    access_key = settings.MOMO_ACCESS_KEY
    secret_key = settings.MOMO_SECRET_KEY
    ipn_url = settings.MOMO_IPN_URL
    redirect_url = settings.MOMO_REDIRECT_URL

    order_id = f"{invoice.invoice_code}-{int(timezone.now().timestamp())}"
    order_info = f"Payment {invoice.invoice_code} for {invoice.event_id.title}"
    amount = str(int(invoice.final_amount))  # MoMo required int
    extra_data = ""

    raw_signature = f'accessKey={access_key}&amount={amount}&extraData={extra_data}&ipnUrl={ipn_url}&orderId={order_id}&orderInfo={order_info}&partnerCode={partner_code}&redirectUrl={redirect_url}&requestId={request_id}&requestType=captureWallet'
    signature = hmac.new(
        key=secret_key.encode('utf-8'),
        msg=raw_signature.encode('utf-8'),
        digestmod=hashlib.sha256
    ).hexdigest()

    payload = {
        "partnerCode": partner_code,
        "partnerName": "EventUp",
        "storeId": "EventUpStore",
        "requestId": request_id,
        "amount": amount,
        "orderId": order_id,
        "orderInfo": order_info,
        "redirectUrl": redirect_url,
        "ipnUrl": ipn_url,
        "lang": "vi",
        "extraData": extra_data,
        "requestType": "captureWallet",
        "signature": signature
    }
    result = {}
    try:
        response = requests.post(endpoint, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        logger.info(f"MoMo response: {result}")
        if result.get('resultCode') == 0:
            return result.get('payUrl', '')
        else:
            raise Exception(f"MoMo error: {result.get('message', 'Unknown error')}")
    except Exception as e:
        logger.info(f"MoMo response (fallback): {json.dumps(result, indent=2)}")
        logger.error(f"Failed to create MoMo payment: {e}")
        raise Exception(f"Failed to create MoMo payment: {str(e)}")


def verify_momo_payment(data):
    secret_key = settings.MOMO_SECRET_KEY
    access_key = settings.MOMO_ACCESS_KEY
    signature = data.get('signature')
    logger.info(f"IPN payload: {data}")
    raw_signature = f"accessKey={access_key}&amount={data.get('amount')}&extraData={data.get('extraData')}&message={data.get('message')}&orderId={data.get('orderId')}&orderInfo={data.get('orderInfo')}&orderType={data.get('orderType')}&partnerCode={data.get('partnerCode')}&payType={data.get('payType')}&requestId={data.get('requestId')}&responseTime={data.get('responseTime')}&resultCode={data.get('resultCode')}&transId={data.get('transId')}"
    logger.info(f"Raw signature: {raw_signature}")
    h = hmac.new(secret_key.encode('utf-8'), raw_signature.encode('utf-8'), hashlib.sha256)
    computed_signature = h.hexdigest()
    logger.info(f"Computed signature: {computed_signature}, Received signature: {signature}")
    return computed_signature == signature


def send_push_notification_for_updating(invoices, event):
    push_tokens = [inv.user_id.push_token for inv in invoices if inv.user_id.push_token]

    for token in push_tokens:
        message = {
            "to": token,
            "sound": "default",
            "title": "Event update",
            "body": f"Event '{event.title}' has been updated, please check for more detail information!",
        }
        try:
            requests.post("https://exp.host/--/api/v2/push/send", json=message)
        except Exception as ex:
            print(f"Push failed for token {token}: {ex}")


def verify_firebase_token(id_token):
    decoded = auth.verify_id_token(id_token)
    return {
        'email': decoded.get('email'),
        'uid': decoded.get('sub'),
        'name': decoded.get('name', '')
    }


def get_or_create_user_from_firebase(email, name):
    return User.objects.get_or_create(
        email=email,
        defaults={
            'username': email.split('@')[0],
            'first_name': name,
            'role': 'participant'
        }
    )


def generate_oauth2_token(user):
    app = Application.objects.get(name='Event Up')
    token, _ = AccessToken.objects.get_or_create(
        user=user,
        application=app,
        expires=timezone.now() + timedelta(seconds=3600),
        defaults={'token': secrets.token_urlsafe(32)}
    )
    return token


def get_organizer_dashboard_data(organizer):
    events = Event.objects.filter(organizer_id=organizer, active=True)

    # Dashboard data
    total_tickets = Invoice.objects.filter(
        event_id__in=events,
        payment_status='success'
    ).aggregate(total=Sum('ticket_count'))['total'] or 0

    total_revenue = Invoice.objects.filter(
        event_id__in=events,
        payment_status='success'
    ).aggregate(total=Sum('amount'))['total'] or 0

    total_views = events.aggregate(total=Sum('views'))['total'] or 0

    # Bar chart data
    event_data = []
    for event in events:
        avg_rating = Review.objects.filter(
            event_id=event,
            active=True
        ).aggregate(avg=Avg('rating'))['avg'] or 0

        event_data.append({
            'event_id': event.id,
            'event_title': event.title,
            'views': event.views,
            'average_rating': round(avg_rating, 1)
        })

    data = {
        'total_tickets': total_tickets,
        'total_revenue': total_revenue,
        'total_views': total_views,
        'events': event_data
    }

    return data


def get_monthly_report_data(organizer, year, month):
    start_date = timezone.make_aware(datetime(year, month, 1))
    end_date = (start_date + timedelta(days=31)).replace(day=1) - timedelta(seconds=1)

    invoices = Invoice.objects.filter(
        event_id__organizer_id=organizer,
        payment_status='success',
        created_at__range=[start_date, end_date]
    )

    # Ticket data to draw chart
    ticket_data = invoices.values('event_id', 'event_id__title').annotate(ticket_count=Sum('ticket_count')).order_by(
        '-ticket_count')
    # Revenue data to draw chart
    revenue_data = invoices.values('event_id', 'event_id__title').annotate(revenue=Sum('amount')).order_by('-revenue')

    data = {
        'ticket_pie_chart': [
            {'event_id': item['event_id'],
             'event_title': item['event_id__title'],
             'value': item['ticket_count']}
            for item in ticket_data
        ],
        'revenue_pie_chart': [
            {'event_id': item['event_id'],
             'event_title': item['event_id__title'],
             'value': float(item['revenue'])}
            for item in revenue_data
        ]
    }

    return data


