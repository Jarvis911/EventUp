from django.core.mail import send_mail
from django.utils import timezone
from .models import Notification
# Momo
import hmac
import hashlib
import json
import requests
from django.conf import settings
import logging

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
            return result.get('payUrl'), result.get('qrCodeUrl', ''), result.get('deeplink', '')
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

