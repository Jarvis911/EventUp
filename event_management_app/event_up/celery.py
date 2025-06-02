import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'event_up.settings')

app = Celery('event_up')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
